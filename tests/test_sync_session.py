"""Shared playback-session event policy with controlled observations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from lyriflux.application.playback_clock import PlaybackClock
from lyriflux.application.sync_session import PlaybackSyncSession
from lyriflux.domain.models import PlayerEvent, PlayerEventKind
from lyriflux.domain.synchronization import (
    ClockUpdateKind,
    ObservationReason,
    PlaybackState,
    PositionObservation,
)
from tests.stage2_helpers import fixture_snapshot


class FakeSampler:
    def __init__(
        self,
        positions_us: tuple[int, ...],
        in_flight: Callable[[], None] | None = None,
    ) -> None:
        self.positions = iter(positions_us)
        self.in_flight = in_flight
        self.reasons: list[ObservationReason] = []

    def sample(
        self,
        _snapshot: object,
        session_id: str,
        *,
        reason: ObservationReason = ObservationReason.PERIODIC,
    ) -> PositionObservation:
        self.reasons.append(reason)
        if self.in_flight is not None:
            callback, self.in_flight = self.in_flight, None
            callback()
        position_us = next(self.positions)
        return PositionObservation(
            session_id,
            position_us,
            PlaybackState.PLAYING,
            1.0,
            position_us * 1_000,
            position_us * 1_000,
            reason,
        )


def test_seek_received_during_position_call_is_not_lost() -> None:
    snapshot = replace(fixture_snapshot("stage2/youtube_jesskah.json"), rate=1.0)
    session = PlaybackSyncSession(PlaybackClock(lambda: 0), snapshot, "track-a")
    sampler = FakeSampler(
        (1_000_000, 2_000_000),
        lambda: session.handle_event(
            PlayerEvent(PlayerEventKind.SEEKED, snapshot.service_name, position_us=2)
        ),
    )

    first = session.sample(sampler)
    second = session.sample(sampler)

    assert first.kind is ClockUpdateKind.REJECTED_STALE
    assert second.kind is ClockUpdateKind.RESET
    assert sampler.reasons == [ObservationReason.INITIAL, ObservationReason.SEEK]
    estimate = session.estimate(duration_us=None)
    assert estimate is not None
    assert estimate.diagnostics.discontinuity_count == 1


def test_seek_signal_reanchors_immediately_and_latest_rapid_seek_wins() -> None:
    snapshot = replace(fixture_snapshot("stage2/youtube_jesskah.json"), rate=1.0)
    clock = PlaybackClock(lambda: 10_000_000_000)
    session = PlaybackSyncSession(clock, snapshot, "track-a")
    session.sample(FakeSampler((1_000_000,)))

    for position_us in (
        23_000_000,
        31_000_000,
        41_000_000,
        53_000_000,
        70_000_000,
        90_000_000,
        143_000_000,
        136_000_000,
        115_000_000,
    ):
        session.handle_event(
            PlayerEvent(
                PlayerEventKind.SEEKED,
                snapshot.service_name,
                position_us=position_us,
            )
        )

    estimate = session.estimate(duration_us=None)

    assert estimate is not None
    assert estimate.position_us == 115_000_000
    assert estimate.diagnostics.discontinuity_count == 9


def test_old_seek_after_track_invalidation_is_ignored() -> None:
    snapshot = fixture_snapshot("stage2/youtube_jesskah.json")
    clock = PlaybackClock(lambda: 0)
    session = PlaybackSyncSession(clock, snapshot, "track-a")
    session.sample(FakeSampler((1_000_000,)))
    session.handle_event(
        PlayerEvent(PlayerEventKind.METADATA_CHANGED, snapshot.service_name)
    )

    session.handle_event(
        PlayerEvent(
            PlayerEventKind.SEEKED,
            snapshot.service_name,
            position_us=99_000_000,
        )
    )

    estimate = session.estimate(duration_us=None)
    assert estimate is not None
    assert estimate.position_us == 1_000_000


def test_seek_outranks_rate_and_status_until_sampled() -> None:
    snapshot = fixture_snapshot("stage2/youtube_jesskah.json")
    session = PlaybackSyncSession(PlaybackClock(lambda: 0), snapshot, "track-a")
    session.handle_event(
        PlayerEvent(PlayerEventKind.SEEKED, snapshot.service_name, position_us=2)
    )
    session.handle_event(
        PlayerEvent(
            PlayerEventKind.PLAYBACK_STATUS_CHANGED,
            snapshot.service_name,
            playback_status="Playing",
        )
    )
    session.handle_event(
        PlayerEvent(
            PlayerEventKind.PROPERTIES_CHANGED,
            snapshot.service_name,
            snapshot=snapshot,
            changed_properties=("Rate",),
        )
    )
    sampler = FakeSampler((1_000_000,))

    session.sample(sampler)

    assert sampler.reasons == [ObservationReason.SEEK]


def test_metadata_and_disappearance_request_reload_and_new_source_resets() -> None:
    snapshot = fixture_snapshot("stage2/youtube_jesskah.json")
    session = PlaybackSyncSession(PlaybackClock(lambda: 0), snapshot, "track-a")
    session.sample(FakeSampler((0,)))
    session.handle_event(
        PlayerEvent(PlayerEventKind.METADATA_CHANGED, snapshot.service_name)
    )
    assert session.requires_reload is True

    session.replace_source(snapshot, "track-a")
    assert session.requires_reload is False
    session.handle_event(
        PlayerEvent(PlayerEventKind.PLAYER_DISAPPEARED, snapshot.service_name)
    )
    assert session.requires_reload is True

    session.replace_source(snapshot, "track-b")
    sampler = FakeSampler((100_000,))
    update = session.sample(sampler)
    assert update.kind is ClockUpdateKind.RESET
    assert sampler.reasons == [ObservationReason.TRACK]


def test_suspend_resume_invalidates_old_events_and_requests_fresh_anchor() -> None:
    snapshot = replace(fixture_snapshot("stage2/youtube_jesskah.json"), rate=1.0)
    clock = PlaybackClock(lambda: 5_000_000_000)
    session = PlaybackSyncSession(clock, snapshot, "track-a")
    session.sample(FakeSampler((1_000_000,)))

    session.system_resumed()

    assert session.requires_reload is True
    estimate = session.estimate(duration_us=None)
    assert estimate is not None
    assert estimate.diagnostics.health.value == "Discontinuity"
    session.handle_event(
        PlayerEvent(
            PlayerEventKind.SEEKED,
            snapshot.service_name,
            position_us=99_000_000,
        )
    )
    after_old_seek = session.estimate(duration_us=None)
    assert after_old_seek is not None
    assert after_old_seek.position_us != 99_000_000

    session.replace_source(snapshot, "track-a")
    sampler = FakeSampler((1_250_000,))
    session.sample(sampler)
    assert sampler.reasons == [ObservationReason.SUSPEND_RESUME]
    estimate = session.estimate(duration_us=None)
    assert estimate is not None
    assert estimate.diagnostics.discontinuity_count == 1


def test_disappeared_player_can_reappear_as_a_new_source_generation() -> None:
    snapshot = fixture_snapshot("stage2/youtube_jesskah.json")
    session = PlaybackSyncSession(PlaybackClock(lambda: 0), snapshot, "track-a")
    original_generation = session.generation
    session.handle_event(
        PlayerEvent(PlayerEventKind.PLAYER_DISAPPEARED, snapshot.service_name)
    )

    session.replace_source(snapshot, "track-b")

    assert session.requires_reload is False
    assert session.generation > original_generation
    assert session.snapshot.service_name == snapshot.service_name


def test_other_player_lifecycle_requests_selection_reevaluation() -> None:
    snapshot = fixture_snapshot("stage2/youtube_jesskah.json")
    other = replace(snapshot, service_name="org.mpris.MediaPlayer2.strawberry")
    session = PlaybackSyncSession(PlaybackClock(lambda: 0), snapshot, "track-a")

    session.handle_event(
        PlayerEvent(
            PlayerEventKind.PLAYBACK_STATUS_CHANGED,
            other.service_name,
            playback_status="Playing",
        )
    )

    assert session.requires_reload is True
    generation = session.generation
    session.replace_source(other, "track-b")
    assert session.requires_reload is False
    assert session.generation == generation + 1
    assert session.snapshot.service_name == other.service_name


def test_diagnostic_and_unrelated_seek_events_do_not_wake_sync_state() -> None:
    snapshot = fixture_snapshot("stage2/youtube_jesskah.json")
    session = PlaybackSyncSession(PlaybackClock(lambda: 0), snapshot, "track-a")

    diagnostic_changed = session.handle_event(
        PlayerEvent(PlayerEventKind.DIAGNOSTIC, snapshot.service_name)
    )
    unrelated_seek_changed = session.handle_event(
        PlayerEvent(
            PlayerEventKind.SEEKED,
            "org.mpris.MediaPlayer2.other",
            position_us=1_000_000,
        )
    )

    assert diagnostic_changed is False
    assert unrelated_seek_changed is False
    assert session.requires_reload is False
