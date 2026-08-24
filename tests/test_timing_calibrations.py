"""Durable, isolated Stage 6 document and output calibration tests."""

from dataclasses import replace
from pathlib import Path

import pytest

from lyriflux.domain.synchronization import (
    LyricDocumentTiming,
    OutputDeviceCalibration,
)
from lyriflux.infrastructure.storage.bootstrap import open_storage
from lyriflux.infrastructure.storage.errors import StorageValidationError
from tests.test_storage_repositories import _multilingual_document


def test_document_delay_persists_without_changing_provider_timestamps(
    tmp_path: Path,
) -> None:
    path = tmp_path / "timing.sqlite3"
    first = open_storage(path)
    document = _multilingual_document()
    first.lyrics.put(document)
    original_start = document.representations[0].lines[0].start_ms
    first.timing_calibrations.put_document_timing(
        LyricDocumentTiming(document.document_id, 250_000)
    )

    restarted = open_storage(path)

    assert restarted.timing_calibrations.get_document_timing(
        document.document_id
    ) == LyricDocumentTiming(document.document_id, 250_000)
    restored = restarted.lyrics.get(document.document_id)
    assert restored is not None
    assert restored.representations[0].lines[0].start_ms == original_start


def test_provider_refresh_preserves_document_delay_and_reset_is_independent(
    tmp_path: Path,
) -> None:
    storage = open_storage(tmp_path / "refresh.sqlite3")
    document = _multilingual_document()
    storage.lyrics.put(document)
    storage.timing_calibrations.put_document_timing(
        LyricDocumentTiming(document.document_id, -50_000)
    )

    storage.lyrics.put(document)

    assert (
        storage.timing_calibrations.get_document_timing(
            document.document_id
        ).lyrics_display_delay_us
        == -50_000
    )
    assert storage.timing_calibrations.delete_document_timing(document.document_id)
    assert (
        storage.timing_calibrations.get_document_timing(
            document.document_id
        ).lyrics_display_delay_us
        == 0
    )
    assert storage.lyrics.get(document.document_id) == document


def test_different_document_does_not_inherit_delay(tmp_path: Path) -> None:
    storage = open_storage(tmp_path / "documents.sqlite3")
    document = _multilingual_document()
    other = replace(document, document_id="document-2", provider_record_id="other")
    storage.lyrics.put(document)
    storage.lyrics.put(other)
    storage.timing_calibrations.put_document_timing(
        LyricDocumentTiming(document.document_id, 10_000)
    )

    assert (
        storage.timing_calibrations.get_document_timing(
            other.document_id
        ).lyrics_display_delay_us
        == 0
    )


def test_device_residual_is_exactly_scoped_and_restart_safe(tmp_path: Path) -> None:
    path = tmp_path / "devices.sqlite3"
    first = open_storage(path)
    calibration = OutputDeviceCalibration(
        "sha256:device-a", "Bluetooth headphones", 27_000
    )
    first.timing_calibrations.put_output_calibration(calibration)

    restarted = open_storage(path)

    assert (
        restarted.timing_calibrations.get_output_calibration("sha256:device-a")
        == calibration
    )
    assert (
        restarted.timing_calibrations.get_output_calibration("sha256:device-b") is None
    )
    assert restarted.timing_calibrations.delete_output_calibration("sha256:device-a")
    assert (
        restarted.timing_calibrations.get_output_calibration("sha256:device-a") is None
    )


def test_document_delay_rejects_unknown_document(tmp_path: Path) -> None:
    repository = open_storage(tmp_path / "unknown.sqlite3").timing_calibrations

    with pytest.raises(StorageValidationError, match="unknown"):
        repository.put_document_timing(LyricDocumentTiming("missing", 1))
