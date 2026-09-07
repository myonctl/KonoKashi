"""Suspend detection and adaptive authoritative-position sampling policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from konokashi.application.ports import ClockPort
from konokashi.domain.synchronization import (
    ClockHealth,
    ClockUpdate,
    ClockUpdateKind,
    ObservationReason,
    PlaybackState,
)


@dataclass(frozen=True, slots=True)
class SuspendObservation:
    """One comparison between ordinary monotonic and Linux boottime clocks."""

    supported: bool
    resumed: bool
    suspended_duration_ns: int = 0


class SuspendResumeDetector:
    """Detect suspend by growth in boottime-minus-monotonic, never wall time."""

    def __init__(self, clock: ClockPort, *, threshold_ns: int = 250_000_000) -> None:
        if threshold_ns <= 0:
            raise ValueError("suspend threshold must be positive")
        self._clock = clock
        self._threshold_ns = threshold_ns
        self._last_monotonic_ns: int | None = None
        self._last_boottime_ns: int | None = None

    def observe(self) -> SuspendObservation:
        """Return resume evidence and establish the next comparison anchor."""

        monotonic_ns = self._clock.monotonic_ns()
        boottime_ns = self._clock.boottime_ns()
        if boottime_ns is None:
            self._last_monotonic_ns = monotonic_ns
            self._last_boottime_ns = None
            return SuspendObservation(False, False)
        resumed = False
        suspended_ns = 0
        if self._last_monotonic_ns is not None and self._last_boottime_ns is not None:
            monotonic_delta = max(0, monotonic_ns - self._last_monotonic_ns)
            boottime_delta = max(0, boottime_ns - self._last_boottime_ns)
            suspended_ns = max(0, boottime_delta - monotonic_delta)
            resumed = suspended_ns >= self._threshold_ns
        self._last_monotonic_ns = monotonic_ns
        self._last_boottime_ns = boottime_ns
        return SuspendObservation(True, resumed, suspended_ns)


class SamplingMode(Enum):
    """Current authoritative resampling cadence."""

    CONVERGING = "converging"
    STABLE = "stable"
    DEGRADED = "degraded"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class AdaptiveSamplingPolicy:
    """Bounded cadence separating clock discipline from frontend redraw."""

    convergence_interval_ms: int = 250
    convergence_samples: int = 8
    stable_interval_ms: int = 2_000
    degraded_interval_ms: int = 750
    paused_verification_interval_ms: int = 30_000

    def __post_init__(self) -> None:
        values = (
            self.convergence_interval_ms,
            self.convergence_samples,
            self.stable_interval_ms,
            self.degraded_interval_ms,
            self.paused_verification_interval_ms,
        )
        if any(value <= 0 for value in values):
            raise ValueError("adaptive sampling policy values must be positive")


class AdaptiveResampler:
    """Choose the next D-Bus sample time from lifecycle and clock evidence."""

    def __init__(self, policy: AdaptiveSamplingPolicy | None = None) -> None:
        self._policy = policy or AdaptiveSamplingPolicy()
        self._mode = SamplingMode.CONVERGING
        self._convergence_remaining = self._policy.convergence_samples
        self._next_due_ns = 0

    @property
    def mode(self) -> SamplingMode:
        return self._mode

    @property
    def next_due_ns(self) -> int:
        return self._next_due_ns

    def trigger(self, reason: ObservationReason, now_ns: int) -> None:
        """Enter convergence after lifecycle/discontinuity events."""

        if reason is not ObservationReason.PERIODIC:
            self._mode = SamplingMode.CONVERGING
            self._convergence_remaining = self._policy.convergence_samples
            self._next_due_ns = now_ns

    def record(
        self,
        update: ClockUpdate,
        state: PlaybackState,
        now_ns: int,
        health: ClockHealth | None = None,
    ) -> None:
        """Schedule one future authoritative read; never create a busy loop."""

        if state is not PlaybackState.PLAYING:
            self._mode = SamplingMode.PAUSED
            interval_ms = self._policy.paused_verification_interval_ms
        elif update.kind in {
            ClockUpdateKind.REJECTED_OUTLIER,
            ClockUpdateKind.REJECTED_STALE,
            ClockUpdateKind.REJECTED_DUPLICATE,
        } or health in {ClockHealth.DEGRADED, ClockHealth.STALE}:
            self._mode = SamplingMode.DEGRADED
            interval_ms = self._policy.degraded_interval_ms
        elif update.kind in {ClockUpdateKind.INITIALIZED, ClockUpdateKind.RESET}:
            self._mode = SamplingMode.CONVERGING
            self._convergence_remaining = self._policy.convergence_samples - 1
            interval_ms = self._policy.convergence_interval_ms
        elif self._convergence_remaining > 0:
            self._mode = SamplingMode.CONVERGING
            self._convergence_remaining -= 1
            interval_ms = self._policy.convergence_interval_ms
        else:
            self._mode = SamplingMode.STABLE
            interval_ms = self._policy.stable_interval_ms
        self._next_due_ns = now_ns + interval_ms * 1_000_000

    def due(self, now_ns: int) -> bool:
        """Return whether an authoritative sample should start now."""

        return now_ns >= self._next_due_ns

    def delay_ms(self, now_ns: int) -> int:
        """Return an integer event-loop delay rounded upward."""

        remaining_ns = max(0, self._next_due_ns - now_ns)
        return (remaining_ns + 999_999) // 1_000_000
