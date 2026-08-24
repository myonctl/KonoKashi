"""SQLite persistence for separate document and output timing corrections."""

from __future__ import annotations

from lyriflux.domain.synchronization import (
    LyricDocumentTiming,
    OutputDeviceCalibration,
)
from lyriflux.infrastructure.storage.errors import (
    InvalidStoredDataError,
    StorageValidationError,
)
from lyriflux.infrastructure.storage.sqlite import SQLiteDatabase, utc_now_text


class SQLiteTimingCalibrationRepository:
    """Store resettable timing layers without mutating provider lyric evidence."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def get_document_timing(self, document_id: str) -> LyricDocumentTiming:
        """Return one durable delay or an explicit zero-delay value."""

        if not document_id.strip():
            raise StorageValidationError("lyric document ID must not be blank")
        with self._database.connection(readonly=True) as connection:
            row = connection.execute(
                """
                SELECT lyrics_display_delay_us FROM lyric_document_timing
                WHERE document_id = ?
                """,
                (document_id,),
            ).fetchone()
        if row is None:
            return LyricDocumentTiming(document_id)
        try:
            return LyricDocumentTiming(document_id, int(row[0]))
        except (TypeError, ValueError) as error:
            raise InvalidStoredDataError(
                "stored lyric document timing is invalid"
            ) from error

    def put_document_timing(self, timing: LyricDocumentTiming) -> None:
        """Insert or replace only one document-wide display delay."""

        try:
            validated = LyricDocumentTiming(
                timing.document_id, timing.lyrics_display_delay_us
            )
        except ValueError as error:
            raise StorageValidationError(str(error)) from error
        with self._database.transaction() as connection:
            exists = connection.execute(
                "SELECT 1 FROM lyrics_documents WHERE document_id = ?",
                (validated.document_id,),
            ).fetchone()
            if exists is None:
                raise StorageValidationError(
                    "document timing references an unknown lyric document"
                )
            connection.execute(
                """
                INSERT INTO lyric_document_timing(
                    document_id, lyrics_display_delay_us, updated_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    lyrics_display_delay_us = excluded.lyrics_display_delay_us,
                    updated_at = excluded.updated_at
                """,
                (
                    validated.document_id,
                    validated.lyrics_display_delay_us,
                    utc_now_text(),
                ),
            )

    def delete_document_timing(self, document_id: str) -> bool:
        """Remove only one document delay."""

        if not document_id.strip():
            raise StorageValidationError("lyric document ID must not be blank")
        with self._database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM lyric_document_timing WHERE document_id = ?",
                (document_id,),
            )
        return cursor.rowcount > 0

    def get_output_calibration(self, device_key: str) -> OutputDeviceCalibration | None:
        """Return only the residual belonging to the exact stable device key."""

        if not device_key.strip():
            raise StorageValidationError("output device key must not be blank")
        with self._database.connection(readonly=True) as connection:
            row = connection.execute(
                """
                SELECT device_label, residual_delay_us
                FROM audio_output_calibrations WHERE device_key = ?
                """,
                (device_key,),
            ).fetchone()
        if row is None:
            return None
        try:
            return OutputDeviceCalibration(device_key, str(row[0]), int(row[1]))
        except (TypeError, ValueError) as error:
            raise InvalidStoredDataError(
                "stored output-device calibration is invalid"
            ) from error

    def put_output_calibration(self, calibration: OutputDeviceCalibration) -> None:
        """Insert or replace a residual under one deliberate stable identity."""

        try:
            validated = OutputDeviceCalibration(
                calibration.device_key,
                calibration.device_label,
                calibration.residual_delay_us,
            )
        except ValueError as error:
            raise StorageValidationError(str(error)) from error
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO audio_output_calibrations(
                    device_key, device_label, residual_delay_us, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(device_key) DO UPDATE SET
                    device_label = excluded.device_label,
                    residual_delay_us = excluded.residual_delay_us,
                    updated_at = excluded.updated_at
                """,
                (
                    validated.device_key,
                    validated.device_label,
                    validated.residual_delay_us,
                    utc_now_text(),
                ),
            )

    def delete_output_calibration(self, device_key: str) -> bool:
        """Remove only one exact output-device residual."""

        if not device_key.strip():
            raise StorageValidationError("output device key must not be blank")
        with self._database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM audio_output_calibrations WHERE device_key = ?",
                (device_key,),
            )
        return cursor.rowcount > 0
