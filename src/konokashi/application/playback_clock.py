"""Integer monotonic playback clock with robust discipline diagnostics."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import isqrt
from statistics import median

from konokashi.domain.synchronization import (
    ClockCorrectionClass,
    ClockHealth,
    ClockQuality,
    ClockUpdate,
    ClockUpdateKind,
    ObservationReason,
    PlaybackClockDiagnostics,
    PlaybackPositionEstimate,
    PlaybackState,
    PositionObservation,
    PositionSampleSource,
)

_RATE_SCALE = 1_000_000_000
_NS_RATE_DENOMINATOR = 1_000 * _RATE_SCALE


def _round_ratio(numerator: int, denominator: int) -> int:
    """Round an integer ratio to nearest, with ties away from zero."""

    if denominator <= 0:
        raise ValueError("rounding denominator must be positive")
    if numerator >= 0:
        return (numerator + denominator // 2) // denominator
    return -((-numerator + denominator // 2) // denominator)


def _percentile(values: Sequence[int], percentile: int) -> int:
    """Return a deterministic nearest-rank percentile for a bounded history."""

    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, (len(ordered) * percentile + 99) // 100)
    return ordered[min(len(ordered), rank) - 1]


@dataclass(frozen=True, slots=True)
class PlaybackClockPolicy:
    """Centralized, observable synchronization and sample-quality policy."""

    discontinuity_us: int = 250_000
    tiny_phase_floor_us: int = 2_000
    maximum_phase_step_us: int = 50_000
    phase_correction_horizon_us: int = 2_000_000
    drift_min_samples: int = 7
    drift_min_span_us: int = 6_000_000
    drift_pair_min_span_us: int = 500_000
    drift_max_abs_ppm: int = 10_000
    drift_max_residual_p95_us: int = 50_000
    warming_drift_allowance_ppm: int = 5_000
    rtt_quality_min_samples: int = 5
    slow_rtt_absolute_us: int = 100_000
    slow_rtt_median_multiplier: int = 4
    slow_rtt_minimum_multiplier: int = 8
    degraded_after_us: int = 5_000_000
    stale_after_us: int = 15_000_000
    history_size: int = 31

    def __post_init__(self) -> None:
        positive = (
            self.discontinuity_us,
            self.tiny_phase_floor_us,
            self.maximum_phase_step_us,
            self.phase_correction_horizon_us,
            self.drift_min_samples,
            self.drift_min_span_us,
            self.drift_pair_min_span_us,
            self.drift_max_abs_ppm,
            self.drift_max_residual_p95_us,
            self.warming_drift_allowance_ppm,
            self.rtt_quality_min_samples,
            self.slow_rtt_absolute_us,
            self.slow_rtt_median_multiplier,
            self.slow_rtt_minimum_multiplier,
            self.degraded_after_us,
            self.stale_after_us,
            self.history_size,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("playback clock policy values must be positive")
        if self.degraded_after_us >= self.stale_after_us:
            raise ValueError("stale threshold must follow degraded threshold")


@dataclass(frozen=True, slots=True)
class _DriftFit:
    drift_ppm: int
    effective_rate_ppb: int
    residual_p95_us: int
    residual_rms_us: int
    trusted: bool


class PlaybackClock:
    """Derive media position from an integer anchor and sparse observations.

    No wall clock, D-Bus, sleep, or frame-delta accumulator enters this class.
    Production supplies ``time.monotonic_ns``; tests supply a fake callable.
    """

    def __init__(
        self,
        monotonic_ns: Callable[[], int],
        policy: PlaybackClockPolicy | None = None,
    ) -> None:
        self._now = monotonic_ns
        self._policy = policy or PlaybackClockPolicy()
        self._session_id: str | None = None
        self._state = PlaybackState.UNKNOWN
        self._reported_rate_ppb = _RATE_SCALE
        self._base_rate_ppb = _RATE_SCALE
        self._anchor_position_us = 0
        self._anchor_ns = 0
        self._latest_response_ns = -1
        self._last_sample_ns = 0
        self._last_half_rtt_us = 0
        self._last_rtt_us = 0
        self._last_residual_us: int | None = None
        self._last_observed_position_us: int | None = None
        self._last_source: PositionSampleSource | None = None
        self._last_correction_class: ClockCorrectionClass | None = None
        self._phase_residual_us = 0
        self._phase_correction_us = 0
        self._samples: deque[tuple[int, int]] = deque(maxlen=self._policy.history_size)
        self._residuals: deque[int] = deque(maxlen=self._policy.history_size)
        self._rtts: deque[int] = deque(maxlen=self._policy.history_size)
        self._fit: _DriftFit | None = None
        self._accepted_count = 0
        self._rejected_count = 0
        self._discontinuity_count = 0
        self._converging = True
        self._degraded_by_rejection = False
        self._discontinuity_pending = False

    @property
    def available(self) -> bool:
        """Return whether at least one position observation was accepted."""

        return self._session_id is not None

    @property
    def policy(self) -> PlaybackClockPolicy:
        """Expose immutable policy values for diagnostics and tests."""

        return self._policy

    def observe(self, observation: PositionObservation) -> ClockUpdate:
        """Classify and discipline from one quality-bearing observation."""

        if (
            self.available
            and observation.response_received_ns < self._latest_response_ns
        ):
            return self._reject(
                ClockUpdateKind.REJECTED_STALE,
                "observation response predates the newest accepted response",
            )
        if (
            self.available
            and observation.response_received_ns == self._latest_response_ns
        ):
            return self._reject(
                ClockUpdateKind.REJECTED_DUPLICATE,
                "observation duplicates the newest accepted response timestamp",
            )

        new_source_series = self.available and (
            observation.session_id != self._session_id
            or observation.reason is ObservationReason.TRACK
        )
        if new_source_series:
            self._rtts.clear()
            self._accepted_count = 0
            self._rejected_count = 0
            self._discontinuity_count = 0
        self._rtts.append(observation.round_trip_us)
        if not observation.trusted:
            return self._reject(
                ClockUpdateKind.REJECTED_OUTLIER,
                "observation source marked the sample untrusted",
            )
        if self.available and self._is_slow_rtt(observation.round_trip_us):
            return self._reject(
                ClockUpdateKind.REJECTED_OUTLIER,
                "request latency is an outlier relative to recent samples",
            )

        session_changed = observation.session_id != self._session_id
        confirms_pending_event = (
            self.available
            and not session_changed
            and self._discontinuity_pending
            and observation.reason
            in {ObservationReason.SEEK, ObservationReason.SUSPEND_RESUME}
        )
        explicit_discontinuity = not confirms_pending_event and observation.reason in {
            ObservationReason.SEEK,
            ObservationReason.TRACK,
            ObservationReason.SUSPEND_RESUME,
        }
        state_changed = self.available and observation.state is not self._state
        rate_changed = (
            self.available and observation.rate_ppb != self._reported_rate_ppb
        )
        lifecycle_reanchor = (
            observation.reason
            in {
                ObservationReason.STATUS,
                ObservationReason.RATE,
            }
            or confirms_pending_event
        )
        if (
            not self.available
            or session_changed
            or explicit_discontinuity
            or state_changed
            or rate_changed
            or lifecycle_reanchor
        ):
            reason = self._reset_reason(
                observation,
                session_changed=session_changed,
                state_changed=state_changed,
                rate_changed=rate_changed,
            )
            residual = (
                None
                if not self.available
                else observation.position_us
                - self._position_at(observation.midpoint_ns)
            )
            first = not self.available
            self._reset(observation, discontinuity=explicit_discontinuity)
            correction_class = (
                ClockCorrectionClass.INITIAL
                if first
                else (
                    ClockCorrectionClass.DISCONTINUITY
                    if explicit_discontinuity
                    else ClockCorrectionClass.WITHIN_NOISE
                )
            )
            self._last_correction_class = correction_class
            return ClockUpdate(
                ClockUpdateKind.INITIALIZED if first else ClockUpdateKind.RESET,
                reason,
                residual,
                correction_class=correction_class,
            )

        midpoint_ns = observation.midpoint_ns
        predicted = self._position_at(midpoint_ns)
        residual = observation.position_us - predicted
        if abs(residual) >= self._policy.discontinuity_us:
            self._reset(observation, discontinuity=True)
            self._last_correction_class = ClockCorrectionClass.DISCONTINUITY
            return ClockUpdate(
                ClockUpdateKind.RESET,
                "unannounced position discontinuity treated as a seek",
                residual,
                correction_class=ClockCorrectionClass.DISCONTINUITY,
            )

        self._accept_metadata(observation, residual)
        if observation.state is PlaybackState.PLAYING:
            self._samples.append((midpoint_ns, observation.position_us))
            self._fit = self._fit_drift()
        fitted_rate = (
            self._fit.effective_rate_ppb
            if self._fit is not None and self._fit.trusted
            else observation.rate_ppb
        )
        noise_threshold = max(
            self._policy.tiny_phase_floor_us,
            observation.half_round_trip_us + self._residual_jitter(),
        )
        self._anchor_position_us = predicted
        self._anchor_ns = midpoint_ns
        self._base_rate_ppb = fitted_rate
        self._reported_rate_ppb = observation.rate_ppb
        self._state = observation.state
        self._phase_residual_us = residual
        if abs(residual) <= noise_threshold:
            self._phase_correction_us = 0
            self._converging = self._fit is None or not self._fit.trusted
            self._last_correction_class = ClockCorrectionClass.WITHIN_NOISE
            return ClockUpdate(
                ClockUpdateKind.CORRECTED,
                "phase residual is within measured noise; anchor retained",
                residual,
                0,
                ClockCorrectionClass.WITHIN_NOISE,
            )

        correction = max(
            -self._policy.maximum_phase_step_us,
            min(self._policy.maximum_phase_step_us, residual),
        )
        minimum_monotonic_correction = -_round_ratio(
            self._base_rate_ppb * self._policy.phase_correction_horizon_us,
            2 * _RATE_SCALE,
        )
        correction = max(minimum_monotonic_correction, correction)
        self._phase_correction_us = correction
        self._converging = True
        self._last_correction_class = ClockCorrectionClass.PHASE_SLEW
        return ClockUpdate(
            ClockUpdateKind.CORRECTED,
            "genuine phase residual scheduled as a bounded monotonic slew",
            residual,
            correction,
            ClockCorrectionClass.PHASE_SLEW,
        )

    def reanchor_seek(
        self,
        session_id: str,
        position_us: int,
        state: PlaybackState,
        rate: float,
        *,
        monotonic_ns: int | None = None,
    ) -> ClockUpdate:
        """Apply the newest valid Seeked position immediately at callback time."""

        now_ns = self._now() if monotonic_ns is None else monotonic_ns
        observation = PositionObservation(
            session_id,
            position_us,
            state,
            rate,
            now_ns,
            now_ns,
            ObservationReason.SEEK,
            PositionSampleSource.SEEKED_SIGNAL,
        )
        residual = (
            None if not self.available else position_us - self._position_at(now_ns)
        )
        first = not self.available
        self._reset(observation, discontinuity=True)
        self._last_correction_class = ClockCorrectionClass.DISCONTINUITY
        return ClockUpdate(
            ClockUpdateKind.INITIALIZED if first else ClockUpdateKind.RESET,
            "seek signal immediately replaced interpolation state",
            residual,
            correction_class=ClockCorrectionClass.DISCONTINUITY,
        )

    def transition_state(
        self,
        state: PlaybackState,
        *,
        monotonic_ns: int | None = None,
    ) -> ClockUpdate | None:
        """Freeze/resume immediately, then require authoritative convergence."""

        if not self.available or state is self._state:
            return None
        now_ns = self._now() if monotonic_ns is None else monotonic_ns
        self._anchor_position_us = self._position_at(now_ns)
        self._anchor_ns = now_ns
        self._state = state
        self._clear_discipline_window()
        self._converging = True
        self._last_correction_class = ClockCorrectionClass.WITHIN_NOISE
        return ClockUpdate(
            ClockUpdateKind.RESET,
            "playback state changed at local signal receipt; re-anchor requested",
            correction_class=ClockCorrectionClass.WITHIN_NOISE,
        )

    def transition_rate(
        self,
        rate: float,
        *,
        monotonic_ns: int | None = None,
    ) -> ClockUpdate | None:
        """Preserve phase at a Rate signal and apply the new rate thereafter."""

        if not self.available:
            return None
        probe = PositionObservation(
            self._session_id or "unavailable",
            max(0, self._anchor_position_us),
            self._state,
            rate,
            0,
            0,
            source=PositionSampleSource.LOCAL_STATE_TRANSITION,
        )
        if probe.rate_ppb == self._reported_rate_ppb:
            return None
        now_ns = self._now() if monotonic_ns is None else monotonic_ns
        self._anchor_position_us = self._position_at(now_ns)
        self._anchor_ns = now_ns
        self._reported_rate_ppb = probe.rate_ppb
        self._base_rate_ppb = probe.rate_ppb
        self._clear_discipline_window()
        self._converging = True
        self._last_correction_class = ClockCorrectionClass.WITHIN_NOISE
        return ClockUpdate(
            ClockUpdateKind.RESET,
            "playback rate changed without a phase discontinuity",
            correction_class=ClockCorrectionClass.WITHIN_NOISE,
        )

    def mark_suspend_resume(self, *, monotonic_ns: int | None = None) -> None:
        """Invalidate interpolation discipline until a fresh sample arrives."""

        if not self.available:
            return
        now_ns = self._now() if monotonic_ns is None else monotonic_ns
        self._anchor_position_us = self._position_at(now_ns)
        self._anchor_ns = now_ns
        self._clear_discipline_window()
        self._converging = True
        self._discontinuity_pending = True
        self._discontinuity_count += 1
        self._last_correction_class = ClockCorrectionClass.DISCONTINUITY

    def mark_sampling_failure(self, reason: str) -> ClockUpdate:
        """Keep interpolating temporarily but make failed authority visible."""

        detail = reason.strip() or "authoritative Position sampling failed"
        return self._reject(ClockUpdateKind.REJECTED_OUTLIER, detail)

    def estimate(
        self,
        *,
        now_ns: int | None = None,
        duration_us: int | None = None,
    ) -> PlaybackPositionEstimate | None:
        """Return the derived position with quantified and unknown error terms."""

        if self._session_id is None:
            return None
        current_ns = self._now() if now_ns is None else now_ns
        position = self._position_at(current_ns)
        effective_rate_ppb = self._rate_at(current_ns)
        slew_remaining_us, slew_remaining_duration_us = self._slew_remaining_at(
            current_ns
        )
        if duration_us is not None:
            position = min(position, max(0, duration_us))
        position = max(0, position)
        age_us = max(0, (current_ns - self._last_sample_ns) // 1_000)
        jitter_us = self._residual_jitter()
        fit = self._fit
        health = self._health(age_us)
        quality = self._quality(health, fit)
        drift_growth_us = (
            0
            if fit is not None and fit.trusted
            else age_us * self._policy.warming_drift_allowance_ppm // 1_000_000
        )
        observed_bound = (
            self._last_half_rtt_us
            + jitter_us
            + abs(self._phase_remaining_at(current_ns))
            + drift_growth_us
        )
        rtts = tuple(self._rtts)
        absolute_residuals = tuple(abs(value) for value in self._residuals)
        diagnostics = PlaybackClockDiagnostics(
            quality=quality,
            sample_count=len(self._samples),
            sample_age_us=age_us,
            last_round_trip_us=self._last_half_rtt_us,
            last_residual_us=self._last_residual_us,
            residual_jitter_us=jitter_us,
            phase_error_remaining_us=self._phase_remaining_at(current_ns),
            observed_error_bound_us=observed_bound,
            drift_ppm=None if fit is None else float(fit.drift_ppm),
            drift_correction_active=bool(
                fit is not None
                and fit.trusted
                and fit.effective_rate_ppb != self._reported_rate_ppb
            ),
            unquantified_error_sources=(
                "the MPRIS player does not specify when or how precisely "
                "Position was sampled",
                "D-Bus scheduling can be asymmetric, so half round-trip is "
                "only a lower bound",
            ),
            health=health,
            last_rtt_us=self._last_rtt_us,
            minimum_recent_rtt_us=min(rtts, default=0),
            median_recent_rtt_us=round(median(rtts)) if rtts else 0,
            p95_recent_rtt_us=_percentile(rtts, 95),
            maximum_recent_rtt_us=max(rtts, default=0),
            accepted_sample_count=self._accepted_count,
            rejected_sample_count=self._rejected_count,
            discontinuity_count=self._discontinuity_count,
            last_sample_source=self._last_source,
            median_absolute_residual_us=(
                round(median(absolute_residuals)) if absolute_residuals else 0
            ),
            p95_absolute_residual_us=_percentile(absolute_residuals, 95),
            maximum_absolute_residual_us=max(absolute_residuals, default=0),
            last_correction_class=self._last_correction_class,
        )
        return PlaybackPositionEstimate(
            self._session_id,
            position,
            self._state,
            self._reported_rate_ppb / _RATE_SCALE,
            effective_rate_ppb / _RATE_SCALE,
            self._base_rate_ppb / _RATE_SCALE,
            slew_remaining_us,
            slew_remaining_duration_us,
            current_ns,
            diagnostics,
            self._base_rate_ppb,
            effective_rate_ppb,
            self._last_observed_position_us,
        )

    def _reset_reason(
        self,
        observation: PositionObservation,
        *,
        session_changed: bool,
        state_changed: bool,
        rate_changed: bool,
    ) -> str:
        if not self.available:
            return "first position observation"
        if session_changed:
            return "track/session identity changed"
        if state_changed:
            return "playback state changed"
        if rate_changed:
            return "playback rate changed without a phase discontinuity"
        return observation.reason.value

    def _reset(self, observation: PositionObservation, *, discontinuity: bool) -> None:
        self._session_id = observation.session_id
        self._state = observation.state
        self._reported_rate_ppb = observation.rate_ppb
        self._base_rate_ppb = observation.rate_ppb
        self._anchor_position_us = observation.position_us
        self._anchor_ns = observation.midpoint_ns
        self._latest_response_ns = observation.response_received_ns
        self._last_sample_ns = observation.midpoint_ns
        self._last_half_rtt_us = observation.half_round_trip_us
        self._last_rtt_us = observation.round_trip_us
        self._last_residual_us = None
        self._last_observed_position_us = observation.position_us
        self._last_source = observation.source
        self._clear_discipline_window()
        self._accepted_count += 1
        self._degraded_by_rejection = False
        self._converging = True
        self._discontinuity_pending = discontinuity
        if discontinuity:
            self._discontinuity_count += 1
        if observation.state is PlaybackState.PLAYING:
            self._samples.append((observation.midpoint_ns, observation.position_us))

    def _accept_metadata(
        self, observation: PositionObservation, residual_us: int
    ) -> None:
        self._latest_response_ns = observation.response_received_ns
        self._last_sample_ns = observation.midpoint_ns
        self._last_half_rtt_us = observation.half_round_trip_us
        self._last_rtt_us = observation.round_trip_us
        self._last_residual_us = residual_us
        self._last_observed_position_us = observation.position_us
        self._last_source = observation.source
        self._residuals.append(residual_us)
        self._accepted_count += 1
        self._degraded_by_rejection = False
        self._discontinuity_pending = False

    def _reject(self, kind: ClockUpdateKind, reason: str) -> ClockUpdate:
        self._rejected_count += 1
        self._degraded_by_rejection = True
        self._last_correction_class = ClockCorrectionClass.REJECTED
        return ClockUpdate(
            kind,
            reason,
            correction_class=ClockCorrectionClass.REJECTED,
        )

    def _is_slow_rtt(self, rtt_us: int) -> bool:
        if len(self._rtts) < self._policy.rtt_quality_min_samples:
            return False
        prior = tuple(self._rtts)[:-1]
        if not prior:
            return False
        threshold = max(
            self._policy.slow_rtt_absolute_us,
            round(median(prior)) * self._policy.slow_rtt_median_multiplier,
            min(prior) * self._policy.slow_rtt_minimum_multiplier,
        )
        return rtt_us > threshold

    def _clear_discipline_window(self) -> None:
        self._phase_residual_us = 0
        self._phase_correction_us = 0
        self._samples.clear()
        self._residuals.clear()
        self._fit = None

    def _position_at(self, monotonic_ns: int) -> int:
        if self._state is not PlaybackState.PLAYING:
            return self._anchor_position_us
        elapsed_ns = max(0, monotonic_ns - self._anchor_ns)
        base_progress_us = _round_ratio(
            elapsed_ns * self._base_rate_ppb,
            _NS_RATE_DENOMINATOR,
        )
        horizon_ns = self._policy.phase_correction_horizon_us * 1_000
        correction_progress_us = _round_ratio(
            self._phase_correction_us * min(elapsed_ns, horizon_ns),
            horizon_ns,
        )
        return self._anchor_position_us + base_progress_us + correction_progress_us

    def _rate_at(self, monotonic_ns: int) -> int:
        if self._state is not PlaybackState.PLAYING:
            return self._base_rate_ppb
        elapsed_ns = max(0, monotonic_ns - self._anchor_ns)
        horizon_ns = self._policy.phase_correction_horizon_us * 1_000
        if elapsed_ns >= horizon_ns:
            return self._base_rate_ppb
        correction_rate_ppb = _round_ratio(
            self._phase_correction_us * _RATE_SCALE,
            self._policy.phase_correction_horizon_us,
        )
        return self._base_rate_ppb + correction_rate_ppb

    def _phase_remaining_at(self, monotonic_ns: int) -> int:
        if self._state is not PlaybackState.PLAYING:
            return self._phase_residual_us
        elapsed_ns = max(0, monotonic_ns - self._anchor_ns)
        horizon_ns = self._policy.phase_correction_horizon_us * 1_000
        completed_us = _round_ratio(
            self._phase_correction_us * min(elapsed_ns, horizon_ns),
            horizon_ns,
        )
        return self._phase_residual_us - completed_us

    def _slew_remaining_at(self, monotonic_ns: int) -> tuple[int, int]:
        if self._state is not PlaybackState.PLAYING:
            return 0, 0
        elapsed_us = max(0, monotonic_ns - self._anchor_ns) // 1_000
        remaining_duration_us = max(
            0, self._policy.phase_correction_horizon_us - elapsed_us
        )
        remaining_us = _round_ratio(
            self._phase_correction_us * remaining_duration_us,
            self._policy.phase_correction_horizon_us,
        )
        return remaining_us, remaining_duration_us

    def _residual_jitter(self) -> int:
        if not self._residuals:
            return 0
        center = round(median(self._residuals))
        deviations = tuple(abs(value - center) for value in self._residuals)
        return _percentile(deviations, 95)

    def _fit_drift(self) -> _DriftFit | None:
        if len(self._samples) < self._policy.drift_min_samples:
            return None
        first_ns = self._samples[0][0]
        span_us = (self._samples[-1][0] - first_ns) // 1_000
        if span_us < self._policy.drift_min_span_us:
            return None
        slopes: list[int] = []
        samples = tuple(self._samples)
        for left_index, (left_ns, left_position) in enumerate(samples):
            for right_ns, right_position in samples[left_index + 1 :]:
                delta_ns = right_ns - left_ns
                if delta_ns < self._policy.drift_pair_min_span_us * 1_000:
                    continue
                delta_position = right_position - left_position
                if delta_position <= 0:
                    continue
                slopes.append(
                    _round_ratio(
                        delta_position * _NS_RATE_DENOMINATOR,
                        delta_ns,
                    )
                )
        if not slopes:
            return None
        slope_ppb = round(median(slopes))
        intercepts = tuple(
            position
            - _round_ratio((timestamp_ns - first_ns) * slope_ppb, _NS_RATE_DENOMINATOR)
            for timestamp_ns, position in samples
        )
        intercept = round(median(intercepts))
        residuals = tuple(
            position
            - (
                intercept
                + _round_ratio(
                    (timestamp_ns - first_ns) * slope_ppb,
                    _NS_RATE_DENOMINATOR,
                )
            )
            for timestamp_ns, position in samples
        )
        residual_p95 = _percentile(tuple(abs(value) for value in residuals), 95)
        residual_rms = isqrt(
            sum(value * value for value in residuals) // len(residuals)
        )
        drift_ppm = _round_ratio(
            (slope_ppb - self._reported_rate_ppb) * 1_000_000,
            self._reported_rate_ppb,
        )
        trusted = (
            abs(drift_ppm) <= self._policy.drift_max_abs_ppm
            and residual_p95 <= self._policy.drift_max_residual_p95_us
        )
        return _DriftFit(
            drift_ppm,
            slope_ppb,
            residual_p95,
            residual_rms,
            trusted,
        )

    def _health(self, age_us: int) -> ClockHealth:
        if self._state is PlaybackState.PAUSED:
            return ClockHealth.PAUSED
        if self._state in {PlaybackState.STOPPED, PlaybackState.UNKNOWN}:
            return ClockHealth.UNAVAILABLE
        if age_us >= self._policy.stale_after_us:
            return ClockHealth.STALE
        if self._discontinuity_pending:
            return ClockHealth.DISCONTINUITY
        if age_us >= self._policy.degraded_after_us or self._degraded_by_rejection:
            return ClockHealth.DEGRADED
        if self._fit is not None and self._fit.trusted and not self._converging:
            return ClockHealth.LOCKED
        if self._fit is not None and self._fit.trusted:
            self._converging = False
            return ClockHealth.LOCKED
        if self._fit is not None and not self._fit.trusted:
            return ClockHealth.DEGRADED
        return ClockHealth.CONVERGING

    @staticmethod
    def _quality(health: ClockHealth, fit: _DriftFit | None) -> ClockQuality:
        if health is ClockHealth.LOCKED:
            return ClockQuality.STABLE
        if health in {ClockHealth.DEGRADED, ClockHealth.STALE}:
            return ClockQuality.DEGRADED
        if health is ClockHealth.DISCONTINUITY:
            return ClockQuality.WARMING
        if health is ClockHealth.PAUSED:
            return ClockQuality.HELD
        if health is ClockHealth.UNAVAILABLE:
            return ClockQuality.UNAVAILABLE
        if fit is not None and not fit.trusted:
            return ClockQuality.DEGRADED
        return ClockQuality.WARMING
