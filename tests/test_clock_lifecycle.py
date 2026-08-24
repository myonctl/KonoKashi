"""Deterministic suspend and adaptive-resampling policy tests."""

from lyriflux.application.clock_lifecycle import (
    AdaptiveResampler,
    SamplingMode,
    SuspendResumeDetector,
)
from lyriflux.domain.synchronization import (
    ClockCorrectionClass,
    ClockHealth,
    ClockUpdate,
    ClockUpdateKind,
    ObservationReason,
    PlaybackState,
)


class FakeClock:
    def __init__(self) -> None:
        self.monotonic = 0
        self.boottime: int | None = 0

    def monotonic_ns(self) -> int:
        return self.monotonic

    def boottime_ns(self) -> int | None:
        return self.boottime


def update(kind: ClockUpdateKind) -> ClockUpdate:
    return ClockUpdate(kind, "test", correction_class=ClockCorrectionClass.INITIAL)


def test_suspend_detector_uses_boottime_minus_monotonic_delta() -> None:
    clock = FakeClock()
    detector = SuspendResumeDetector(clock)
    assert detector.observe().resumed is False

    clock.monotonic = 1_000_000_000
    clock.boottime = 1_000_000_000
    ordinary_gap = detector.observe()
    clock.monotonic = 2_000_000_000
    clock.boottime = 12_000_000_000
    resume = detector.observe()

    assert ordinary_gap.resumed is False
    assert resume.resumed is True
    assert resume.suspended_duration_ns == 10_000_000_000


def test_suspend_detector_reports_unsupported_without_wall_clock_guess() -> None:
    clock = FakeClock()
    clock.boottime = None
    detector = SuspendResumeDetector(clock)

    assert detector.observe().supported is False


def test_adaptive_sampling_converges_then_returns_to_low_frequency() -> None:
    scheduler = AdaptiveResampler()
    scheduler.trigger(ObservationReason.INITIAL, 0)
    assert scheduler.due(0)

    scheduler.record(update(ClockUpdateKind.INITIALIZED), PlaybackState.PLAYING, 0)
    assert scheduler.mode is SamplingMode.CONVERGING
    assert scheduler.delay_ms(0) == 250
    now_ns = 0
    for _ in range(8):
        now_ns += 250_000_000
        scheduler.record(
            update(ClockUpdateKind.CORRECTED), PlaybackState.PLAYING, now_ns
        )

    assert scheduler.mode is SamplingMode.STABLE
    assert scheduler.delay_ms(now_ns) == 2_000


def test_seek_resume_and_failure_reenter_faster_cadence() -> None:
    scheduler = AdaptiveResampler()
    scheduler.record(update(ClockUpdateKind.CORRECTED), PlaybackState.PLAYING, 0)
    scheduler.trigger(ObservationReason.SEEK, 1_000)
    assert scheduler.mode is SamplingMode.CONVERGING
    assert scheduler.due(1_000)

    scheduler.record(update(ClockUpdateKind.REJECTED_OUTLIER), PlaybackState.PLAYING, 0)
    assert scheduler.mode is SamplingMode.DEGRADED
    assert scheduler.delay_ms(0) == 750

    scheduler.record(
        update(ClockUpdateKind.CORRECTED),
        PlaybackState.PLAYING,
        1_000,
        ClockHealth.DEGRADED,
    )
    assert scheduler.mode is SamplingMode.DEGRADED
    assert scheduler.delay_ms(1_000) == 750


def test_paused_state_avoids_rapid_position_polling() -> None:
    scheduler = AdaptiveResampler()

    scheduler.record(update(ClockUpdateKind.RESET), PlaybackState.PAUSED, 0)

    assert scheduler.mode is SamplingMode.PAUSED
    assert scheduler.delay_ms(0) == 30_000


def test_very_large_scheduler_gap_becomes_due_without_catch_up_burst() -> None:
    scheduler = AdaptiveResampler()
    scheduler.record(update(ClockUpdateKind.CORRECTED), PlaybackState.PLAYING, 0)

    gap_ns = 86_400_000_000_000
    assert scheduler.due(gap_ns) is True
    scheduler.record(update(ClockUpdateKind.CORRECTED), PlaybackState.PLAYING, gap_ns)

    assert scheduler.due(gap_ns) is False
    assert scheduler.delay_ms(gap_ns) > 0
