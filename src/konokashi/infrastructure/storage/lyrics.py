"""SQLite repository for provider-neutral lyric documents and aligned layers."""

from __future__ import annotations

from datetime import UTC, datetime

from konokashi.domain.lyrics import (
    ApprovalState,
    ContentProvenance,
    LyricDocument,
    LyricDocumentKind,
    LyricLine,
    LyricLineAlignment,
    LyricRepresentation,
    RepresentationKind,
    TimingProvenance,
)
from konokashi.infrastructure.storage.errors import (
    InvalidStoredDataError,
    StorageValidationError,
)
from konokashi.infrastructure.storage.sqlite import SQLiteDatabase


def _datetime_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StorageValidationError("persisted timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _validate(document: LyricDocument) -> None:
    if not document.document_id:
        raise StorageValidationError("lyric document ID must not be blank")
    representation_ids = [item.representation_id for item in document.representations]
    if len(set(representation_ids)) != len(representation_ids):
        raise StorageValidationError("lyric representation IDs must be unique")
    line_ids = [
        line.line_id
        for representation in document.representations
        for line in representation.lines
    ]
    if any(not line_id for line_id in line_ids) or len(set(line_ids)) != len(line_ids):
        raise StorageValidationError(
            "lyric line IDs must be non-blank and unique within a document"
        )
    known_lines = set(line_ids)
    for representation in document.representations:
        for line in representation.lines:
            if line.start_ms is not None and line.start_ms < 0:
                raise StorageValidationError(
                    "lyric start timestamps must be non-negative"
                )
            if line.end_ms is not None and line.end_ms < 0:
                raise StorageValidationError(
                    "lyric end timestamps must be non-negative"
                )
            if (
                line.start_ms is not None
                and line.end_ms is not None
                and line.end_ms < line.start_ms
            ):
                raise StorageValidationError("lyric end timestamps precede their start")
            if (
                line.source_line_id is not None
                and line.source_line_id not in known_lines
            ):
                raise StorageValidationError(
                    "lyric representation line references an unknown source line"
                )
    for alignment in document.alignments:
        if (
            alignment.source_line_id not in known_lines
            or alignment.target_line_id not in known_lines
        ):
            raise StorageValidationError("lyric alignment references an unknown line")
    _datetime_text(document.retrieved_at)


class SQLiteLyricsRepository:
    """Atomically store documents while preserving text, instances, and provenance."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def put(self, document: LyricDocument) -> None:
        """Insert or deliberately replace one whole typed lyric document."""

        _validate(document)
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO lyrics_documents(
                    document_id, document_kind, source_name, original_text,
                    raw_text_checksum, provider_record_id, language, script,
                    duration_ms, approval_state, retrieved_at, source_title,
                    source_artist, source_album
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    document_kind = excluded.document_kind,
                    source_name = excluded.source_name,
                    original_text = excluded.original_text,
                    raw_text_checksum = excluded.raw_text_checksum,
                    provider_record_id = excluded.provider_record_id,
                    language = excluded.language,
                    script = excluded.script,
                    duration_ms = excluded.duration_ms,
                    approval_state = excluded.approval_state,
                    retrieved_at = excluded.retrieved_at,
                    source_title = excluded.source_title,
                    source_artist = excluded.source_artist,
                    source_album = excluded.source_album
                """,
                (
                    document.document_id,
                    document.kind.value,
                    document.source_name,
                    document.original_text,
                    document.raw_text_checksum,
                    document.provider_record_id,
                    document.language,
                    document.script,
                    document.duration_ms,
                    document.approval_state.value,
                    _datetime_text(document.retrieved_at),
                    document.source_title,
                    document.source_artist,
                    document.source_album,
                ),
            )
            connection.execute(
                "DELETE FROM lyric_representations WHERE document_id = ?",
                (document.document_id,),
            )
            for representation_position, representation in enumerate(
                document.representations
            ):
                connection.execute(
                    """
                    INSERT INTO lyric_representations(
                        document_id, representation_id, representation_kind,
                        language, script, provenance, approval_state,
                        generator_name, generator_version, position
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document.document_id,
                        representation.representation_id,
                        representation.kind.value,
                        representation.language,
                        representation.script,
                        representation.provenance.value,
                        representation.approval_state.value,
                        representation.generator_name,
                        representation.generator_version,
                        representation_position,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO lyric_lines(
                        document_id, representation_id, line_id, position,
                        line_text, start_ms, end_ms, timing_provenance, source_line_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            document.document_id,
                            representation.representation_id,
                            line.line_id,
                            line_position,
                            line.text,
                            line.start_ms,
                            line.end_ms,
                            (
                                None
                                if line.timing_provenance is None
                                else line.timing_provenance.value
                            ),
                            line.source_line_id,
                        )
                        for line_position, line in enumerate(representation.lines)
                    ),
                )
            connection.executemany(
                """
                INSERT INTO lyric_line_alignments(
                    document_id, source_line_id, target_line_id, position
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    (
                        document.document_id,
                        alignment.source_line_id,
                        alignment.target_line_id,
                        position,
                    )
                    for position, alignment in enumerate(document.alignments)
                ),
            )

    def get(self, document_id: str) -> LyricDocument | None:
        """Reconstruct a complete typed document rather than returning SQL rows."""

        with self._database.connection(readonly=True) as connection:
            document_row = connection.execute(
                "SELECT * FROM lyrics_documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if document_row is None:
                return None
            representation_rows = connection.execute(
                """
                SELECT * FROM lyric_representations
                WHERE document_id = ? ORDER BY position
                """,
                (document_id,),
            ).fetchall()
            line_rows = connection.execute(
                """
                SELECT * FROM lyric_lines
                WHERE document_id = ? ORDER BY representation_id, position
                """,
                (document_id,),
            ).fetchall()
            alignment_rows = connection.execute(
                """
                SELECT source_line_id, target_line_id FROM lyric_line_alignments
                WHERE document_id = ? ORDER BY position
                """,
                (document_id,),
            ).fetchall()
        lines_by_representation: dict[str, list[LyricLine]] = {}
        try:
            for row in line_rows:
                timing_value = row["timing_provenance"]
                lines_by_representation.setdefault(
                    str(row["representation_id"]), []
                ).append(
                    LyricLine(
                        line_id=str(row["line_id"]),
                        text=str(row["line_text"]),
                        start_ms=(
                            None if row["start_ms"] is None else int(row["start_ms"])
                        ),
                        end_ms=None if row["end_ms"] is None else int(row["end_ms"]),
                        timing_provenance=(
                            None
                            if timing_value is None
                            else TimingProvenance(str(timing_value))
                        ),
                        source_line_id=(
                            None
                            if row["source_line_id"] is None
                            else str(row["source_line_id"])
                        ),
                    )
                )
            representations = tuple(
                LyricRepresentation(
                    representation_id=str(row["representation_id"]),
                    kind=RepresentationKind(str(row["representation_kind"])),
                    provenance=ContentProvenance(str(row["provenance"])),
                    approval_state=ApprovalState(str(row["approval_state"])),
                    lines=tuple(
                        lines_by_representation.get(str(row["representation_id"]), [])
                    ),
                    language=None if row["language"] is None else str(row["language"]),
                    script=None if row["script"] is None else str(row["script"]),
                    generator_name=(
                        None
                        if row["generator_name"] is None
                        else str(row["generator_name"])
                    ),
                    generator_version=(
                        None
                        if row["generator_version"] is None
                        else str(row["generator_version"])
                    ),
                )
                for row in representation_rows
            )
            return LyricDocument(
                document_id=str(document_row["document_id"]),
                kind=LyricDocumentKind(str(document_row["document_kind"])),
                source_name=str(document_row["source_name"]),
                original_text=(
                    None
                    if document_row["original_text"] is None
                    else str(document_row["original_text"])
                ),
                raw_text_checksum=(
                    None
                    if document_row["raw_text_checksum"] is None
                    else str(document_row["raw_text_checksum"])
                ),
                approval_state=ApprovalState(str(document_row["approval_state"])),
                retrieved_at=datetime.fromisoformat(str(document_row["retrieved_at"])),
                representations=representations,
                alignments=tuple(
                    LyricLineAlignment(
                        str(row["source_line_id"]), str(row["target_line_id"])
                    )
                    for row in alignment_rows
                ),
                provider_record_id=(
                    None
                    if document_row["provider_record_id"] is None
                    else str(document_row["provider_record_id"])
                ),
                language=(
                    None
                    if document_row["language"] is None
                    else str(document_row["language"])
                ),
                script=(
                    None
                    if document_row["script"] is None
                    else str(document_row["script"])
                ),
                duration_ms=(
                    None
                    if document_row["duration_ms"] is None
                    else int(document_row["duration_ms"])
                ),
                source_title=(
                    None
                    if document_row["source_title"] is None
                    else str(document_row["source_title"])
                ),
                source_artist=(
                    None
                    if document_row["source_artist"] is None
                    else str(document_row["source_artist"])
                ),
                source_album=(
                    None
                    if document_row["source_album"] is None
                    else str(document_row["source_album"])
                ),
            )
        except (TypeError, ValueError) as error:
            raise InvalidStoredDataError("stored lyric document is invalid") from error

    def delete(self, document_id: str) -> bool:
        """Explicitly delete one document; approved match references prevent loss."""

        with self._database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM lyrics_documents WHERE document_id = ?", (document_id,)
            )
            return cursor.rowcount > 0
