"""SQLite persistence for resettable local lyric line correction overlays."""

from __future__ import annotations

from datetime import UTC, datetime

from konokashi.domain.lyric_corrections import LyricLineCorrection
from konokashi.infrastructure.storage.errors import (
    InvalidStoredDataError,
    StorageValidationError,
)
from konokashi.infrastructure.storage.sqlite import SQLiteDatabase


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StorageValidationError(
            "lyric correction timestamps must be timezone-aware"
        )
    return value.astimezone(UTC).isoformat()


class SQLiteLyricsCorrectionRepository:
    """Store overlays separately so provider/import source rows remain immutable."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def get(self, document_id: str) -> tuple[LyricLineCorrection, ...]:
        """Return every overlay, including stale evidence, in stable order."""

        if not document_id.strip():
            raise StorageValidationError("lyric document ID must not be blank")
        with self._database.connection(readonly=True) as connection:
            rows = connection.execute(
                """
                SELECT document_id, line_id, based_on_text, based_on_start_ms,
                       corrected_text, corrected_start_ms, created_at, updated_at
                FROM lyric_line_corrections
                WHERE document_id = ? ORDER BY line_id
                """,
                (document_id,),
            ).fetchall()
        try:
            return tuple(
                LyricLineCorrection(
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                    None if row[3] is None else int(row[3]),
                    None if row[4] is None else str(row[4]),
                    None if row[5] is None else int(row[5]),
                    datetime.fromisoformat(str(row[6])),
                    datetime.fromisoformat(str(row[7])),
                )
                for row in rows
            )
        except (TypeError, ValueError) as error:
            raise InvalidStoredDataError(
                "stored lyric line correction is invalid"
            ) from error

    def replace(
        self, document_id: str, corrections: tuple[LyricLineCorrection, ...]
    ) -> None:
        """Atomically replace only the selected document's correction layer."""

        if not document_id.strip():
            raise StorageValidationError("lyric document ID must not be blank")
        line_ids = [item.line_id for item in corrections]
        if len(line_ids) != len(set(line_ids)):
            raise StorageValidationError("lyric corrections require unique line IDs")
        if any(item.document_id != document_id for item in corrections):
            raise StorageValidationError("lyric correction belongs to another document")
        try:
            rows = tuple(
                (
                    item.document_id,
                    item.line_id,
                    item.based_on_text,
                    item.based_on_start_ms,
                    item.corrected_text,
                    item.corrected_start_ms,
                    _timestamp(item.created_at),
                    _timestamp(item.updated_at),
                )
                for item in corrections
            )
        except ValueError as error:
            raise StorageValidationError(str(error)) from error
        with self._database.transaction() as connection:
            exists = connection.execute(
                "SELECT 1 FROM lyrics_documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if exists is None:
                raise StorageValidationError(
                    "lyric corrections reference an unknown source document"
                )
            connection.execute(
                "DELETE FROM lyric_line_corrections WHERE document_id = ?",
                (document_id,),
            )
            connection.executemany(
                """
                INSERT INTO lyric_line_corrections(
                    document_id, line_id, based_on_text, based_on_start_ms,
                    corrected_text, corrected_start_ms, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def reset(self, document_id: str) -> int:
        """Delete only one document's local overlays."""

        if not document_id.strip():
            raise StorageValidationError("lyric document ID must not be blank")
        with self._database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM lyric_line_corrections WHERE document_id = ?",
                (document_id,),
            )
        return cursor.rowcount
