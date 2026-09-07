"""Qualified automatic audio latency and device residual composition tests."""

from konokashi.application.audio_timing import AudioOutputTimingService
from konokashi.domain.synchronization import (
    AudioLatencyProbeResult,
    AudioLatencyProbeStatus,
    AudioLatencyQuality,
    LyricDocumentTiming,
    OutputDeviceCalibration,
)


class Calibrations:
    def __init__(self) -> None:
        self.outputs: dict[str, OutputDeviceCalibration] = {}

    def get_document_timing(self, document_id: str) -> LyricDocumentTiming:
        return LyricDocumentTiming(document_id)

    def put_document_timing(self, timing: LyricDocumentTiming) -> None:
        del timing

    def delete_document_timing(self, document_id: str) -> bool:
        del document_id
        return False

    def get_output_calibration(self, device_key: str) -> OutputDeviceCalibration | None:
        return self.outputs.get(device_key)

    def put_output_calibration(self, calibration: OutputDeviceCalibration) -> None:
        self.outputs[calibration.device_key] = calibration

    def delete_output_calibration(self, device_key: str) -> bool:
        return self.outputs.pop(device_key, None) is not None


def result(
    device: str,
    minimum_us: int,
    maximum_us: int,
    *,
    safe: bool = True,
    measured_at_ns: int = 1_000,
) -> AudioLatencyProbeResult:
    return AudioLatencyProbeResult(
        AudioLatencyProbeStatus.REPORTED,
        f"Output {device}",
        minimum_us,
        maximum_us,
        None,
        safe,
        (),
        device,
        measured_at_ns,
    )


def test_no_latency_data_stays_unknown_instead_of_zero() -> None:
    service = AudioOutputTimingService(Calibrations(), lambda: 1_000)

    current = service.current()

    assert current.total_compensation_us is None
    assert current.quality is AudioLatencyQuality.UNAVAILABLE


def test_exact_and_range_latency_preserve_quality_and_uncertainty() -> None:
    service = AudioOutputTimingService(Calibrations(), lambda: 1_000)
    service.update(result("a", 40_000, 40_000))
    exact = service.current()
    service.update(result("a", 31_000, 42_000))
    ranged = service.current()

    assert exact.total_compensation_us == 40_000
    assert exact.quality is AudioLatencyQuality.EXACT
    assert ranged.estimate_us == 36_500
    assert ranged.uncertainty_us == 5_500
    assert ranged.minimum_us == 31_000
    assert ranged.maximum_us == 42_000
    assert ranged.quality is AudioLatencyQuality.RANGE


def test_device_change_never_applies_another_device_residual() -> None:
    calibrations = Calibrations()
    calibrations.put_output_calibration(OutputDeviceCalibration("a", "A", 27_000))
    service = AudioOutputTimingService(calibrations, lambda: 1_000)
    service.update(result("a", 100_000, 100_000))
    device_a = service.current()

    changed = service.update(result("b", 50_000, 50_000))
    device_b = service.current()

    assert device_a.total_compensation_us == 127_000
    assert changed is True
    assert device_b.total_compensation_us == 50_000
    assert device_b.residual_calibration_us == 0


def test_unreliable_or_stale_measurement_is_visible_but_not_applied() -> None:
    service = AudioOutputTimingService(
        Calibrations(), lambda: 40_000_000_000, stale_after_ns=30_000_000_000
    )
    service.update(
        result(
            "a",
            100_000,
            120_000,
            safe=False,
            measured_at_ns=20_000_000_000,
        )
    )
    unreliable = service.current()
    service.update(result("a", 100_000, 120_000, measured_at_ns=1_000))
    stale = service.current()

    assert unreliable.total_compensation_us is None
    assert unreliable.quality is AudioLatencyQuality.DIAGNOSTIC_ONLY
    assert stale.total_compensation_us is None
    assert stale.quality is AudioLatencyQuality.STALE


def test_negative_residual_reduces_only_its_exact_device_compensation() -> None:
    calibrations = Calibrations()
    calibrations.put_output_calibration(OutputDeviceCalibration("a", "A", -25_000))
    service = AudioOutputTimingService(calibrations, lambda: 1_000)

    service.update(result("a", 100_000, 100_000))

    current = service.current()
    assert current.residual_calibration_us == -25_000
    assert current.total_compensation_us == 75_000
