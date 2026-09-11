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
    LyricTimingLevel,
    LyricTimingSegment,
    LyricTimingUnit,
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


def _legacy_timing_provenance(value: TimingProvenance | None) -> str | None:
    """Populate the pre-rich column while the append-only schema retains it."""

    if value is None or value is TimingProvenance.IMPORTED:
        return None
    if value in (TimingProvenance.USER_APPROVED, TimingProvenance.USER_EDITED):
        return "manual"
    if value is TimingProvenance.ESTIMATED:
        return "generated"
    return str(value.value)


def _legacy_timing_detail(value: object) -> str | None:
    """Map historical rows into the unambiguous rich provenance vocabulary."""

    if value is None:
        return None
    text = str(value)
    return "user-edited" if text == "manual" else text


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
    segments = [
        (line.line_id, segment)
        for representation in document.representations
        for line in representation.lines
        for segment in line.timing_segments
    ]
    segment_ids = [segment.segment_id for _line_id, segment in segments]
    if any(not segment_id for segment_id in segment_ids) or len(
        set(segment_ids)
    ) != len(segment_ids):
        raise StorageValidationError(
            "lyric timing segment IDs must be non-blank and unique within a document"
        )
    segment_lines = {segment.segment_id: line_id for line_id, segment in segments}
    segment_parents = {
        segment.segment_id: segment.parent_segment_id for _line_id, segment in segments
    }
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
            for segment in line.timing_segments:
                if segment.start_ms < 0:
                    raise StorageValidationError(
                        "lyric timing segment starts must be non-negative"
                    )
                if segment.end_ms is not None and segment.end_ms < segment.start_ms:
                    raise StorageValidationError(
                        "lyric timing segment ends precede their start"
                    )
                if (
                    segment.parent_segment_id is not None
                    and segment_lines.get(segment.parent_segment_id) != line.line_id
                ):
                    raise StorageValidationError(
                        "lyric timing segment parent must exist in the same line"
                    )
                if (
                    segment.unit is not LyricTimingUnit.PROVIDER_ELEMENT
                    and segment.provider_unit is not None
                ):
                    raise StorageValidationError(
                        "only provider timing elements may carry a provider unit"
                    )
    for segment_id in segment_ids:
        visited: set[str] = set()
        current: str | None = segment_id
        while current is not None:
            if current in visited:
                raise StorageValidationError(
                    "lyric timing segment parents must not form a cycle"
                )
            visited.add(current)
            current = segment_parents.get(current)
    original_lines = tuple(
        line
        for representation in document.representations
        if representation.kind is RepresentationKind.ORIGINAL
        for line in representation.lines
    )
    has_line_timing = any(line.start_ms is not None for line in original_lines)
    original_segments = tuple(
        segment for line in original_lines for segment in line.timing_segments
    )
    if document.timing_level is LyricTimingLevel.UNSYNCHRONIZED and (
        has_line_timing or original_segments
    ):
        raise StorageValidationError(
            "unsynchronized lyric documents must not carry original timing"
        )
    if document.timing_level is not LyricTimingLevel.UNSYNCHRONIZED and not (
        has_line_timing or original_segments
    ):
        raise StorageValidationError("timed lyric documents must carry original timing")
    if document.timing_level is LyricTimingLevel.WORD and any(
        segment.unit is not LyricTimingUnit.WORD for segment in original_segments
    ):
        raise StorageValidationError("word timing must contain only word segments")
    if document.timing_level is LyricTimingLevel.WORD and not original_segments:
        raise StorageValidationError("word timing must contain timing segments")
    if document.timing_level is LyricTimingLevel.LINE and original_segments:
        raise StorageValidationError("line timing must not hide richer timing segments")
    if document.timing_level is LyricTimingLevel.ELEMENT and not original_segments:
        raise StorageValidationError("element timing must contain timing segments")
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
                    source_artist, source_album, timing_level
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    source_album = excluded.source_album,
                    timing_level = excluded.timing_level
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
                    document.timing_level.value,
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
                        line_text, start_ms, end_ms, timing_provenance,
                        source_line_id, timing_provenance_detail
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            (_legacy_timing_provenance(line.timing_provenance)),
                            line.source_line_id,
                            (
                                None
                                if line.timing_provenance is None
                                else line.timing_provenance.value
                            ),
                        )
                        for line_position, line in enumerate(representation.lines)
                    ),
                )
                for line in representation.lines:
                    connection.executemany(
                        """
                        INSERT INTO lyric_timing_segments(
                            document_id, line_id, segment_id, position,
                            segment_text, start_ms, end_ms, timing_unit,
                            timing_provenance, parent_segment_id, provider_unit
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            (
                                document.document_id,
                                line.line_id,
                                segment.segment_id,
                                segment_position,
                                segment.text,
                                segment.start_ms,
                                segment.end_ms,
                                segment.unit.value,
                                (
                                    None
                                    if segment.timing_provenance is None
                                    else segment.timing_provenance.value
                                ),
                                segment.parent_segment_id,
                                segment.provider_unit,
                            )
                            for segment_position, segment in enumerate(
                                line.timing_segments
                            )
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
            segment_rows = connection.execute(
                """
                SELECT * FROM lyric_timing_segments
                WHERE document_id = ? ORDER BY line_id, position
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
            segments_by_line: dict[str, list[LyricTimingSegment]] = {}
            for row in segment_rows:
                provenance_value = row["timing_provenance"]
                segments_by_line.setdefault(str(row["line_id"]), []).append(
                    LyricTimingSegment(
                        segment_id=str(row["segment_id"]),
                        text=str(row["segment_text"]),
                        start_ms=int(row["start_ms"]),
                        end_ms=(None if row["end_ms"] is None else int(row["end_ms"])),
                        unit=LyricTimingUnit(str(row["timing_unit"])),
                        timing_provenance=(
                            None
                            if provenance_value is None
                            else TimingProvenance(str(provenance_value))
                        ),
                        parent_segment_id=(
                            None
                            if row["parent_segment_id"] is None
                            else str(row["parent_segment_id"])
                        ),
                        provider_unit=(
                            None
                            if row["provider_unit"] is None
                            else str(row["provider_unit"])
                        ),
                    )
                )
            for row in line_rows:
                timing_value = row["timing_provenance_detail"]
                if timing_value is None:
                    timing_value = _legacy_timing_detail(row["timing_provenance"])
                line_id = str(row["line_id"])
                lines_by_representation.setdefault(
                    str(row["representation_id"]), []
                ).append(
                    LyricLine(
                        line_id=line_id,
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
                        timing_segments=tuple(segments_by_line.get(line_id, [])),
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
            document = LyricDocument(
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
                timing_level=LyricTimingLevel(str(document_row["timing_level"])),
            )
            _validate(document)
            return document
        except (StorageValidationError, TypeError, ValueError) as error:
            raise InvalidStoredDataError("stored lyric document is invalid") from error

    def delete(self, document_id: str) -> bool:
        """Explicitly delete one document; approved match references prevent loss."""

        with self._database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM lyrics_documents WHERE document_id = ?", (document_id,)
            )
            return cursor.rowcount > 0
