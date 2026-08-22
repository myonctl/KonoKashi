"""Presentation-neutral Desktop MVP lifecycle and stale-state regressions."""

from dataclasses import replace

import pytest

from lyricflow.application.desktop_state import (
    DesktopLyricsState,
    DesktopStateController,
)
from lyricflow.application.lyrics_sync import synchronize
from lyricflow.application.sync_state import build_sync_snapshot
from lyricflow.domain.identity import YouTubeIdentity
from lyricflow.domain.lyrics import (
    LyricDocumentKind,
    LyricsResolutionResult,
    LyricsResolutionStatus,
)
from lyricflow.domain.representations import RepresentationDisplaySettings
from lyricflow.domain.synchronization import (
    AudioOutputLatency,
    LyricTimingCalibration,
    PlaybackState,
    SynchronizationCalibration,
)
from lyricflow.domain.tracks import Confidence, ResolvedTrack, TrackCandidate
from tests.stage2_helpers import fixture_snapshot
from tests.test_lyrics_sync import document, estimate


def _track(video_id: str, title: str) -> ResolvedTrack:
    raw = replace(
        fixture_snapshot("stage2/youtube_jesskah.json"),
        playback_status="Playing",
        rate=1.0,
    )
    return ResolvedTrack(
        raw,
        YouTubeIdentity(video_id),
        TrackCandidate(title, ("Artist",), "Album", 5_000_000),
        Confidence.HIGH,
    )


def _snapshot(track: ResolvedTrack, generation: int):  # type: ignore[no-untyped-def]
    lyric_document = document()
    current_estimate = estimate()
    calibration = SynchronizationCalibration(
        AudioOutputLatency(0, 0, "test"),
        LyricTimingCalibration(0),
    )
    return build_sync_snapshot(
        generation=generation,
        track=track,
        document=lyric_document,
        estimate=current_estimate,
        frame=synchronize(lyric_document, current_estimate, calibration),
        calibration=calibration,
        lyrics_match_confidence="High",
    )


def test_no_player_appears_later_and_disappears_without_restart() -> None:
    controller = DesktopStateController()
    assert controller.state.state is DesktopLyricsState.WAITING
    assert controller.state.status_message == "Waiting for media…"

    track = _track("xa4WrgqI7q0", "A")
    token = controller.begin_resolution(track)
    assert controller.state.state is DesktopLyricsState.RESOLVING
    assert controller.state.title == "A"

    assert controller.accept_resolution(
        token,
        LyricsResolutionResult(
            track.source_identity,
            LyricsResolutionStatus.FOUND_TIMED,
            document=document(),
        ),
    )
    assert controller.accept_snapshot(_snapshot(track, token.generation))
    assert controller.state.active

    controller.no_player()
    assert controller.state.state is DesktopLyricsState.WAITING
    assert controller.state.active == ()
    assert controller.state.title is None


def test_track_change_invalidates_old_lyrics_and_rejects_stale_completion() -> None:
    controller = DesktopStateController()
    track_a = _track("xa4WrgqI7q0", "Track A")
    track_b = _track("kFqGyp60d8s", "Track B")
    token_a = controller.begin_resolution(track_a)
    assert controller.accept_resolution(
        token_a,
        LyricsResolutionResult(
            track_a.source_identity,
            LyricsResolutionStatus.FOUND_TIMED,
            document=document(),
        ),
    )
    assert controller.accept_snapshot(_snapshot(track_a, token_a.generation))

    controller.source_changed()
    assert controller.state.state is DesktopLyricsState.RESOLVING
    assert controller.state.active == ()
    token_b = controller.begin_resolution(track_b)
    assert controller.state.title == "Track B"
    assert controller.state.active == ()

    assert controller.accept_snapshot(_snapshot(track_a, token_a.generation)) is False
    assert (
        controller.accept_resolution(
            token_a,
            LyricsResolutionResult(
                track_a.source_identity,
                LyricsResolutionStatus.NO_RESULT,
            ),
        )
        is False
    )
    assert controller.state.title == "Track B"
    assert controller.state.state is DesktopLyricsState.RESOLVING
    assert token_b.generation > token_a.generation


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        (LyricsResolutionStatus.AMBIGUOUS, DesktopLyricsState.AMBIGUOUS),
        (LyricsResolutionStatus.NO_RESULT, DesktopLyricsState.NO_RESULT),
        (LyricsResolutionStatus.OFFLINE_MISS, DesktopLyricsState.OFFLINE),
        (
            LyricsResolutionStatus.PROVIDER_UNAVAILABLE,
            DesktopLyricsState.PROVIDER_FAILURE,
        ),
        (LyricsResolutionStatus.RATE_LIMITED, DesktopLyricsState.PROVIDER_FAILURE),
        (
            LyricsResolutionStatus.INVALID_PROVIDER_RESPONSE,
            DesktopLyricsState.PROVIDER_FAILURE,
        ),
        (LyricsResolutionStatus.INVALID_LOCAL_LYRICS, DesktopLyricsState.ERROR),
    ),
)
def test_expected_lyrics_outcomes_are_deliberate_states(
    status: LyricsResolutionStatus,
    expected: DesktopLyricsState,
) -> None:
    controller = DesktopStateController()
    track = _track("xa4WrgqI7q0", "Track")
    token = controller.begin_resolution(track)
    assert controller.accept_resolution(
        token,
        LyricsResolutionResult(track.source_identity, status),
    )
    assert controller.state.state is expected
    assert controller.state.status_message
    assert controller.state.active == ()


def test_live_snapshots_follow_pause_resume_seek_and_rapid_seek() -> None:
    controller = DesktopStateController()
    track = _track("xa4WrgqI7q0", "Track")
    token = controller.begin_resolution(track)
    base = _snapshot(track, token.generation)

    assert controller.accept_snapshot(base)
    initial_active = controller.state.active
    paused = replace(base, playback_status=PlaybackState.PAUSED)
    assert controller.accept_snapshot(paused)
    assert controller.state.active == initial_active
    assert controller.state.playback_state is PlaybackState.PAUSED

    resumed = replace(paused, playback_status=PlaybackState.PLAYING)
    assert controller.accept_snapshot(resumed)
    forward = replace(
        resumed,
        previous=resumed.active,
        active=resumed.next,
        next=(),
        disciplined_player_position_us=4_500_000,
    )
    assert controller.accept_snapshot(forward)
    assert controller.state.active != initial_active

    backward = replace(
        forward,
        previous=(),
        active=base.active,
        next=base.next,
        disciplined_player_position_us=2_150_000,
    )
    assert controller.accept_snapshot(backward)
    assert controller.state.active == initial_active
    for position in (4_900_000, 100_000, 4_000_000, 500_000):
        assert controller.accept_snapshot(
            replace(backward, disciplined_player_position_us=position)
        )
    assert controller.state.generation == token.generation


def test_representation_toggles_never_invent_or_duplicate_layers() -> None:
    controller = DesktopStateController()
    track = _track("xa4WrgqI7q0", "Track")
    token = controller.begin_resolution(track)
    controller.set_representation_settings(
        RepresentationDisplaySettings(True, False, True)
    )
    snapshot = _snapshot(track, token.generation)
    assert controller.accept_snapshot(snapshot)
    assert all(
        group.romanized_or_transliterated is None
        for group in (*controller.state.previous, *controller.state.active)
    )


def test_untimed_and_instrumental_are_normal_readable_states() -> None:
    controller = DesktopStateController()
    track = _track("xa4WrgqI7q0", "Track")
    plain = replace(document(), kind=LyricDocumentKind.PLAIN)
    token = controller.begin_resolution(track)
    assert controller.accept_resolution(
        token,
        LyricsResolutionResult(
            track.source_identity,
            LyricsResolutionStatus.FOUND_UNTIMED,
            document=plain,
        ),
    )
    assert controller.state.state is DesktopLyricsState.UNTIMED
    assert controller.state.static_lines
    assert controller.state.active == ()

    token = controller.begin_resolution(track)
    assert controller.accept_resolution(
        token,
        LyricsResolutionResult(
            track.source_identity,
            LyricsResolutionStatus.INSTRUMENTAL,
            document=replace(document(), kind=LyricDocumentKind.INSTRUMENTAL),
        ),
    )
    assert controller.state.state is DesktopLyricsState.INSTRUMENTAL
    assert controller.state.status_message == "Instrumental recording"


def test_progress_handles_stopped_and_unknown_duration_without_fake_value() -> None:
    controller = DesktopStateController()
    track = _track("xa4WrgqI7q0", "Track")
    token = controller.begin_resolution(track)
    assert controller.update_playback(
        token.generation,
        PlaybackState.STOPPED,
        12_000_000,
        None,
    )
    assert controller.state.playback_state is PlaybackState.STOPPED
    assert controller.state.progress_fraction is None
    assert controller.state.position_us == 12_000_000
