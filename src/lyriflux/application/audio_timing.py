"""Apply qualified audio latency and exact-device residual calibration."""

from __future__ import annotations

from collections.abc import Callable

from lyriflux.application.ports import TimingCalibrationRepositoryPort
from lyriflux.domain.synchronization import (
    AudioLatencyProbeResult,
    AudioLatencyProbeStatus,
    AudioLatencyQuality,
    AudioOutputLatency,
)


class AudioOutputTimingService:
    """Turn probe evidence into safe compensation without inventing zero."""

    def __init__(
        self,
        calibrations: TimingCalibrationRepositoryPort,
        monotonic_ns: Callable[[], int],
        *,
        stale_after_ns: int = 30_000_000_000,
    ) -> None:
        if stale_after_ns <= 0:
            raise ValueError("audio latency staleness threshold must be positive")
        self._calibrations = calibrations
        self._now = monotonic_ns
        self._stale_after_ns = stale_after_ns
        self._latest: AudioLatencyProbeResult | None = None

    def update(self, result: AudioLatencyProbeResult) -> bool:
        """Replace evidence and report whether the output device changed."""

        previous_key = None if self._latest is None else self._latest.device_key
        self._latest = result
        return previous_key is not None and previous_key != result.device_key

    def current(self) -> AudioOutputLatency:
        """Return applied compensation or an explicit qualified unknown value."""

        result = self._latest
        if result is None or result.status is AudioLatencyProbeStatus.UNAVAILABLE:
            return AudioOutputLatency(
                None,
                None,
                "audio latency unavailable",
                limitations=("no usable audio-output latency observation",),
                quality=AudioLatencyQuality.UNAVAILABLE,
            )
        residual = (
            None
            if result.device_key is None
            else self._calibrations.get_output_calibration(result.device_key)
        )
        residual_us = 0 if residual is None else residual.residual_delay_us
        stale = (
            result.measured_at_ns is None
            or self._now() - result.measured_at_ns > self._stale_after_ns
        )
        if stale:
            return AudioOutputLatency(
                None,
                None,
                result.source,
                route=result.route,
                measured_at_ns=result.measured_at_ns,
                limitations=("audio latency observation is stale", *result.diagnostics),
                residual_calibration_us=residual_us,
                device_key=result.device_key,
                minimum_us=result.minimum_us,
                maximum_us=result.maximum_us,
                quality=AudioLatencyQuality.STALE,
            )
        if (
            not result.safe_for_automatic_compensation
            or result.chosen_estimate_us is None
        ):
            return AudioOutputLatency(
                None,
                result.uncertainty_us,
                result.source,
                route=result.route,
                measured_at_ns=result.measured_at_ns,
                limitations=result.diagnostics,
                residual_calibration_us=residual_us,
                device_key=result.device_key,
                minimum_us=result.minimum_us,
                maximum_us=result.maximum_us,
                quality=AudioLatencyQuality.DIAGNOSTIC_ONLY,
            )
        quality = (
            AudioLatencyQuality.EXACT
            if result.minimum_us == result.maximum_us
            else AudioLatencyQuality.RANGE
        )
        return AudioOutputLatency(
            result.chosen_estimate_us,
            result.uncertainty_us,
            result.source,
            route=result.route,
            measured_at_ns=result.measured_at_ns,
            limitations=result.diagnostics,
            residual_calibration_us=residual_us,
            device_key=result.device_key,
            minimum_us=result.minimum_us,
            maximum_us=result.maximum_us,
            quality=quality,
        )
