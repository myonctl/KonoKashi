"""Deterministic Stage 6 playback-clock tests."""

import pytest

from lyriflux.application.playback_clock import PlaybackClock
from lyriflux.domain.synchronization import (
    ClockHealth,
    ClockQuality,
    ClockUpdateKind,
    ObservationReason,
    PlaybackState,
    PositionObservation,
)


class FakeMonotonic:
    def __init__(self, now_ns: int = 0) -> None:
        self.now_ns = now_ns

    def __call__(self) -> int:
        return self.now_ns


def observation(
    position_us: int,
    midpoint_ns: int,
    *,
    session_id: str = "track-a",
    state: PlaybackState = PlaybackState.PLAYING,
    rate: float = 1.0,
    half_round_trip_ns: int = 0,
    reason: ObservationReason = ObservationReason.PERIODIC,
) -> PositionObservation:
    return PositionObservation(
        session_id,
        position_us,
        state,
        rate,
        midpoint_ns - half_round_trip_ns,
        midpoint_ns + half_round_trip_ns,
        reason,
    )


def test_clock_interpolates_from_response_midpoint_and_reports_call_bound() -> None:
    monotonic = FakeMonotonic(501_000_000)
    clock = PlaybackClock(monotonic)

    update = clock.observe(
        observation(1_000_000, 1_000_000, half_round_trip_ns=1_000_000)
    )
    estimate = clock.estimate()

    assert update.kind is ClockUpdateKind.INITIALIZED
    assert estimate is not None
    assert estimate.position_us == 1_500_000
    assert estimate.diagnostics.last_round_trip_us == 1_000
    assert estimate.diagnostics.last_correction_class is not None
    assert estimate.diagnostics.observed_error_bound_us >= 1_000
    assert estimate.diagnostics.unquantified_error_sources


def test_pause_resume_seek_and_track_change_reset_without_running_while_held() -> None:
    monotonic = FakeMonotonic()
    clock = PlaybackClock(monotonic)
    clock.observe(observation(1_000_000, 0))

    pause = clock.observe(
        observation(
            1_500_000,
            500_000_000,
            state=PlaybackState.PAUSED,
            reason=ObservationReason.STATUS,
        )
    )
    monotonic.now_ns = 9_000_000_000
    paused = clock.estimate()
    assert pause.kind is ClockUpdateKind.RESET
    assert paused is not None
    assert paused.position_us == 1_500_000
    assert paused.diagnostics.quality is ClockQuality.HELD

    resume = clock.observe(
        observation(
            1_500_000,
            9_000_000_000,
            state=PlaybackState.PLAYING,
            reason=ObservationReason.STATUS,
        )
    )
    seek = clock.observe(
        observation(
            20_000_000,
            9_100_000_000,
            reason=ObservationReason.SEEK,
        )
    )
    track = clock.observe(
        observation(
            100_000,
            9_200_000_000,
            session_id="track-b",
            reason=ObservationReason.TRACK,
        )
    )

    assert resume.kind is ClockUpdateKind.RESET
    assert seek.kind is ClockUpdateKind.RESET
    assert track.kind is ClockUpdateKind.RESET
    assert clock.estimate(now_ns=9_300_000_000).position_us == 200_000  # type: ignore[union-attr]


def test_stale_observation_is_rejected_and_cannot_rewind_clock() -> None:
    clock = PlaybackClock(lambda: 3_000_000_000)
    clock.observe(observation(1_000_000, 1_000_000_000))
    clock.observe(observation(2_000_000, 2_000_000_000))

    stale = clock.observe(observation(200_000, 1_500_000_000))

    assert stale.kind is ClockUpdateKind.REJECTED_STALE
    estimate = clock.estimate()
    assert estimate is not None
    assert estimate.position_us == 3_000_000


def test_small_phase_error_is_bounded_and_large_jump_is_a_seek_reset() -> None:
    clock = PlaybackClock(lambda: 1_000_000_000)
    clock.observe(observation(0, 0))

    corrected = clock.observe(observation(1_200_000, 1_000_000_000))
    reset = clock.observe(observation(2_000_000, 1_100_000_000))

    assert corrected.kind is ClockUpdateKind.CORRECTED
    assert corrected.residual_us == 200_000
    assert corrected.scheduled_correction_us == 50_000
    assert reset.kind is ClockUpdateKind.RESET
    assert "discontinuity" in reset.reason


def test_negative_phase_correction_slews_without_rewinding_playback() -> None:
    clock = PlaybackClock(lambda: 0)
    clock.observe(observation(0, 0))
    before = clock.estimate(now_ns=1_000_000_000)

    update = clock.observe(observation(950_000, 1_000_000_000))
    at_update = clock.estimate(now_ns=1_000_000_000)
    halfway = clock.estimate(now_ns=2_000_000_000)

    assert before is not None and at_update is not None and halfway is not None
    assert update.scheduled_correction_us == -50_000
    assert at_update.position_us == before.position_us
    assert halfway.position_us == 1_975_000
    assert halfway.position_us > at_update.position_us


def test_stable_repeated_samples_measure_and_correct_clock_drift() -> None:
    clock = PlaybackClock(lambda: 5_000_000_000)
    for second in range(8):
        clock.observe(
            observation(
                second * 1_001_000,
                second * 1_000_000_000,
            )
        )

    estimate = clock.estimate(now_ns=7_000_000_000)

    assert estimate is not None
    assert estimate.diagnostics.quality is ClockQuality.STABLE
    assert estimate.diagnostics.drift_correction_active is True
    assert estimate.diagnostics.drift_ppm == pytest.approx(1_000.0)
    assert estimate.effective_rate == pytest.approx(1.001)


def test_exact_player_clock_locks_without_claiming_drift_correction() -> None:
    clock = PlaybackClock(lambda: 7_000_000_000)
    for second in range(8):
        clock.observe(observation(second * 1_000_000, second * 1_000_000_000))

    estimate = clock.estimate()

    assert estimate is not None
    assert estimate.diagnostics.health is ClockHealth.LOCKED
    assert estimate.diagnostics.drift_ppm == 0.0
    assert estimate.diagnostics.drift_correction_active is False
    assert estimate.base_rate_ppb == 1_000_000_000


def test_noisy_implausible_drift_is_reported_but_not_applied() -> None:
    clock = PlaybackClock(lambda: 4_000_000_000)
    positions = tuple(second * 1_020_000 for second in range(7))
    for second, position_us in enumerate(positions):
        clock.observe(observation(position_us, second * 1_000_000_000))

    estimate = clock.estimate()

    assert estimate is not None
    assert estimate.diagnostics.quality is ClockQuality.DEGRADED
    assert estimate.diagnostics.health is ClockHealth.DEGRADED
    assert estimate.diagnostics.drift_ppm is not None
    assert abs(estimate.diagnostics.drift_ppm) > 10_000
    assert estimate.diagnostics.drift_correction_active is False
    assert estimate.base_rate == 1.0


def test_known_duration_clamps_playing_estimate() -> None:
    clock = PlaybackClock(lambda: 5_000_000_000)
    clock.observe(observation(900_000, 0))

    estimate = clock.estimate(duration_us=1_000_000)

    assert estimate is not None
    assert estimate.position_us == 1_000_000


def test_rate_change_resets_and_uses_new_rate() -> None:
    clock = PlaybackClock(lambda: 2_000_000_000)
    clock.observe(observation(0, 0))

    update = clock.observe(
        observation(
            1_000_000,
            1_000_000_000,
            rate=1.5,
            reason=ObservationReason.RATE,
        )
    )
    estimate = clock.estimate()

    assert update.kind is ClockUpdateKind.RESET
    assert estimate is not None
    assert estimate.position_us == 2_500_000
    assert estimate.effective_rate == 1.5


@pytest.mark.parametrize(
    ("rate", "expected_us"),
    ((0.5, 500_000), (1.0, 1_000_000), (1.5, 1_500_000), (2.0, 2_000_000)),
)
def test_supported_rates_use_exact_anchor_derived_integer_math(
    rate: float, expected_us: int
) -> None:
    clock = PlaybackClock(lambda: 1_000_000_000)
    clock.observe(observation(0, 0, rate=rate))

    estimate = clock.estimate()

    assert estimate is not None
    assert estimate.position_us == expected_us


def test_slow_rtt_outlier_is_rejected_without_jerking_clock() -> None:
    clock = PlaybackClock(lambda: 7_000_000_000)
    for second in range(5):
        clock.observe(
            observation(
                second * 1_000_000,
                (second + 1) * 1_000_000_000,
                half_round_trip_ns=500_000,
            )
        )

    rejected = clock.observe(
        observation(
            99_000_000,
            6_000_000_000,
            half_round_trip_ns=250_000_000,
        )
    )
    estimate = clock.estimate()

    assert rejected.kind is ClockUpdateKind.REJECTED_OUTLIER
    assert estimate is not None
    assert estimate.position_us == 6_000_000
    assert estimate.diagnostics.rejected_sample_count == 1
    assert estimate.diagnostics.maximum_recent_rtt_us == 500_000


def test_duplicate_response_timestamp_is_rejected() -> None:
    clock = PlaybackClock(lambda: 0)
    first = observation(0, 0)
    clock.observe(first)

    duplicate = clock.observe(first)

    assert duplicate.kind is ClockUpdateKind.REJECTED_DUPLICATE


def test_negative_player_clock_skew_is_measured_without_float_accumulation() -> None:
    clock = PlaybackClock(lambda: 7_000_000_000)
    for second in range(8):
        clock.observe(observation(second * 999_000, second * 1_000_000_000))

    estimate = clock.estimate()

    assert estimate is not None
    assert estimate.diagnostics.drift_ppm == pytest.approx(-1_000.0)
    assert estimate.base_rate_ppb == 999_000_000


def test_seek_clears_drift_fit_and_reenters_convergence() -> None:
    clock = PlaybackClock(lambda: 8_000_000_000)
    for second in range(8):
        clock.observe(observation(second * 1_001_000, second * 1_000_000_000))
    assert clock.estimate().diagnostics.drift_correction_active  # type: ignore[union-attr]

    clock.observe(
        observation(
            1_000_000,
            8_000_000_000,
            reason=ObservationReason.SEEK,
        )
    )
    estimate = clock.estimate()

    assert estimate is not None
    assert estimate.diagnostics.drift_ppm is None
    assert estimate.diagnostics.health is ClockHealth.DISCONTINUITY


def test_ancient_playing_sample_reports_stale_health_and_growing_uncertainty() -> None:
    clock = PlaybackClock(lambda: 20_000_000_000)
    clock.observe(observation(0, 0))

    estimate = clock.estimate()

    assert estimate is not None
    assert estimate.diagnostics.health is ClockHealth.STALE
    assert estimate.diagnostics.quality is ClockQuality.DEGRADED
    assert estimate.diagnostics.observed_error_bound_us >= 100_000


@pytest.mark.parametrize("state", [PlaybackState.STOPPED, PlaybackState.UNKNOWN])
def test_stopped_and_unknown_states_hold_and_report_unavailable(
    state: PlaybackState,
) -> None:
    monotonic = FakeMonotonic(10_000_000_000)
    clock = PlaybackClock(monotonic)
    clock.observe(observation(2_000_000, 0, state=state))

    estimate = clock.estimate()

    assert estimate is not None
    assert estimate.position_us == 2_000_000
    assert estimate.diagnostics.health is ClockHealth.UNAVAILABLE
    assert estimate.diagnostics.quality is ClockQuality.UNAVAILABLE


def test_track_change_resets_drift_history_and_sample_counters() -> None:
    clock = PlaybackClock(lambda: 8_000_000_000)
    for second in range(8):
        clock.observe(observation(second * 1_001_000, second * 1_000_000_000))
    assert clock.estimate().diagnostics.drift_correction_active  # type: ignore[union-attr]

    clock.observe(
        observation(
            500_000,
            8_000_000_000,
            session_id="track-b",
            reason=ObservationReason.TRACK,
        )
    )
    estimate = clock.estimate()

    assert estimate is not None
    assert estimate.session_id == "track-b"
    assert estimate.diagnostics.drift_ppm is None
    assert estimate.diagnostics.sample_count == 1
    assert estimate.diagnostics.accepted_sample_count == 1


def test_pause_samples_do_not_create_a_drift_fit() -> None:
    clock = PlaybackClock(lambda: 8_000_000_000)
    for second in range(8):
        clock.observe(
            observation(
                1_000_000,
                second * 1_000_000_000,
                state=PlaybackState.PAUSED,
            )
        )

    estimate = clock.estimate()

    assert estimate is not None
    assert estimate.diagnostics.drift_ppm is None
    assert estimate.diagnostics.sample_count == 0


def test_sampling_failure_keeps_anchor_but_marks_clock_degraded() -> None:
    clock = PlaybackClock(lambda: 1_000_000_000)
    clock.observe(observation(0, 0))

    update = clock.mark_sampling_failure("D-Bus timeout")
    estimate = clock.estimate()

    assert update.kind is ClockUpdateKind.REJECTED_OUTLIER
    assert estimate is not None
    assert estimate.position_us == 1_000_000
    assert estimate.latest_authoritative_position_us == 0
    assert estimate.diagnostics.health is ClockHealth.DEGRADED
    assert estimate.diagnostics.rejected_sample_count == 1
