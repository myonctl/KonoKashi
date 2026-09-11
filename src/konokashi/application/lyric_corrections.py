"""Project, validate, and persist local edits over immutable lyric evidence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from konokashi.application.lyric_exchange import imported_lyric_edits
from konokashi.application.ports import LyricsCorrectionRepositoryPort
from konokashi.application.representations import original_lines
from konokashi.domain.lyric_corrections import (
    MAX_CORRECTED_LINE_CHARS,
    LyricCorrectionProjection,
    LyricEditorLine,
    LyricEditorSnapshot,
    LyricLineCorrection,
    LyricLineEdit,
)
from konokashi.domain.lyrics import (
    ApprovalState,
    ContentProvenance,
    LyricDocument,
    LyricDocumentKind,
    LyricLine,
    LyricTimingLevel,
    LyricTimingUnit,
    ParsedLyricsText,
    RepresentationKind,
    TimingProvenance,
    lyric_line_timing_start_ms,
)


class LyricCorrectionError(ValueError):
    """A controlled invalid, stale, or unsafe local lyric edit."""


class LyricCorrectionService:
    """Own the local correction layer and its effective-document projection."""

    def __init__(
        self,
        repository: LyricsCorrectionRepositoryPort,
        *,
        now: Callable[[], datetime] | None = None,
        parser: Callable[[str, int | None], ParsedLyricsText] | None = None,
    ) -> None:
        self._repository = repository
        self._now = now or (lambda: datetime.now(UTC))
        self._parser = parser

    def project(self, source: LyricDocument) -> LyricCorrectionProjection:
        """Apply only overlays whose captured source baseline still matches."""

        source_lines = original_lines(source)
        by_id = {line.line_id: line for line in source_lines}
        if len(by_id) != len(source_lines):
            raise LyricCorrectionError("duplicate original lyric line IDs are unsafe")
        corrections = self._repository.get(source.document_id)
        active: dict[str, LyricLineCorrection] = {}
        stale_ids: set[str] = set()
        diagnostics: list[str] = []
        for correction in corrections:
            line = by_id.get(correction.line_id)
            if line is None or not _baseline_matches(line, correction):
                stale_ids.add(correction.line_id)
                continue
            active[correction.line_id] = correction
        if stale_ids:
            diagnostics.append(
                f"{len(stale_ids)} local lyric correction(s) are stale and were "
                "not applied"
            )
        effective_lines = tuple(
            _apply_line(line, active.get(line.line_id)) for line in source_lines
        )
        document = _effective_document(source, effective_lines, active)
        editor_lines_values: list[LyricEditorLine] = []
        for line, effective in zip(source_lines, effective_lines, strict=True):
            applied = active.get(line.line_id)
            editor_lines_values.append(
                LyricEditorLine(
                    line.line_id,
                    line.text,
                    effective.text,
                    lyric_line_timing_start_ms(line),
                    lyric_line_timing_start_ms(effective),
                    applied is not None and applied.corrected_text is not None,
                    applied is not None and applied.corrected_start_ms is not None,
                    line.line_id in stale_ids,
                )
            )
        editor_lines = tuple(editor_lines_values)
        editor = LyricEditorSnapshot(
            source.document_id,
            source.source_name,
            source.duration_ms,
            editor_lines,
            len(active),
            len(stale_ids),
            source.kind is LyricDocumentKind.PLAIN,
            tuple(diagnostics),
        )
        return LyricCorrectionProjection(
            document,
            editor,
            len(active),
            len(stale_ids),
            tuple(diagnostics),
        )

    def replace(self, source: LyricDocument, edits: tuple[LyricLineEdit, ...]) -> int:
        """Validate a complete editor snapshot and atomically replace its overlays."""

        source_lines = original_lines(source)
        if not source_lines or source.kind is LyricDocumentKind.INSTRUMENTAL:
            raise LyricCorrectionError("instrumental or empty lyrics cannot be edited")
        validate_editor_edits(self.project(source).editor, edits)

        existing = {
            item.line_id: item for item in self._repository.get(source.document_id)
        }
        timestamp = self._now()
        corrections: list[LyricLineCorrection] = []
        for line, edit in zip(source_lines, edits, strict=True):
            baseline_start = lyric_line_timing_start_ms(line)
            corrected_text = edit.text if edit.text != line.text else None
            if baseline_start is not None and edit.start_ms is None:
                raise LyricCorrectionError(
                    "a timed source line cannot be made untimed; reset its timestamp "
                    "instead"
                )
            corrected_start = edit.start_ms if edit.start_ms != baseline_start else None
            if corrected_text is None and corrected_start is None:
                continue
            previous = existing.get(line.line_id)
            created_at = (
                previous.created_at
                if previous is not None and _baseline_matches(line, previous)
                else timestamp
            )
            corrections.append(
                LyricLineCorrection(
                    source.document_id,
                    line.line_id,
                    line.text,
                    baseline_start,
                    corrected_text,
                    corrected_start,
                    created_at,
                    timestamp,
                )
            )
        self._repository.replace(source.document_id, tuple(corrections))
        return len(corrections)

    def reset(self, source: LyricDocument) -> int:
        """Revert the complete effective view to its immutable source document."""

        return self._repository.reset(source.document_id)

    def import_text(self, source: LyricDocument, text: str) -> int:
        """Import bounded plain/LRC text through the configured parser and overlay."""

        if self._parser is None:
            raise LyricCorrectionError("lyric import parser is unavailable")
        parsed = self._parser(text, source.duration_ms)
        try:
            edits = imported_lyric_edits(source, parsed)
        except ValueError as error:
            raise LyricCorrectionError(str(error)) from error
        return self.replace(source, edits)


def _baseline_matches(line: LyricLine, correction: LyricLineCorrection) -> bool:
    return (
        line.text == correction.based_on_text
        and lyric_line_timing_start_ms(line) == correction.based_on_start_ms
    )


def _apply_line(line: LyricLine, correction: LyricLineCorrection | None) -> LyricLine:
    if correction is None:
        return line
    text = line.text if correction.corrected_text is None else correction.corrected_text
    baseline_start = lyric_line_timing_start_ms(line)
    timing_changed = correction.corrected_start_ms is not None
    start_ms = line.start_ms
    end_ms = line.end_ms
    segments = line.timing_segments
    provenance = line.timing_provenance
    if timing_changed:
        assert correction.corrected_start_ms is not None
        start_ms = correction.corrected_start_ms
        delta = 0 if baseline_start is None else start_ms - baseline_start
        end_ms = None if line.end_ms is None else line.end_ms + delta
        segments = tuple(
            replace(
                segment,
                start_ms=segment.start_ms + delta,
                end_ms=(None if segment.end_ms is None else segment.end_ms + delta),
                timing_provenance=TimingProvenance.USER_EDITED,
            )
            for segment in segments
        )
        provenance = TimingProvenance.USER_EDITED
    if correction.corrected_text is not None and segments:
        # Existing word/element spans no longer prove alignment to edited text.
        if start_ms is None:
            start_ms = baseline_start
            provenance = segments[0].timing_provenance
        if end_ms is None:
            bounded_ends = tuple(
                segment.end_ms for segment in segments if segment.end_ms is not None
            )
            end_ms = max(bounded_ends) if bounded_ends else None
        segments = ()
    return replace(
        line,
        text=text,
        start_ms=start_ms,
        end_ms=end_ms,
        timing_provenance=provenance,
        timing_segments=segments,
    )


def _effective_document(
    source: LyricDocument,
    lines: tuple[LyricLine, ...],
    active: dict[str, LyricLineCorrection],
) -> LyricDocument:
    if not active:
        return source
    text_changed = any(item.corrected_text is not None for item in active.values())
    representations = tuple(
        replace(
            representation,
            lines=lines,
            provenance=(
                ContentProvenance.USER if text_changed else representation.provenance
            ),
            approval_state=(
                ApprovalState.APPROVED
                if text_changed
                else representation.approval_state
            ),
        )
        if representation.kind is RepresentationKind.ORIGINAL
        else representation
        for representation in source.representations
    )
    segments = tuple(segment for line in lines for segment in line.timing_segments)
    if segments:
        timing_level = (
            LyricTimingLevel.WORD
            if all(segment.unit is LyricTimingUnit.WORD for segment in segments)
            else LyricTimingLevel.ELEMENT
        )
    elif any(lyric_line_timing_start_ms(line) is not None for line in lines):
        timing_level = LyricTimingLevel.LINE
    else:
        timing_level = LyricTimingLevel.UNSYNCHRONIZED
    return replace(
        source,
        kind=(
            LyricDocumentKind.SYNCED
            if timing_level is not LyricTimingLevel.UNSYNCHRONIZED
            else LyricDocumentKind.PLAIN
        ),
        original_text="\n".join(line.text for line in lines),
        raw_text_checksum=None if text_changed else source.raw_text_checksum,
        approval_state=ApprovalState.APPROVED,
        representations=representations,
        timing_level=timing_level,
    )


def validate_editor_edits(
    snapshot: LyricEditorSnapshot, edits: tuple[LyricLineEdit, ...]
) -> None:
    """Apply the exact save rules to UI preview values before persistence."""

    expected_ids = tuple(line.line_id for line in snapshot.lines)
    edit_ids = tuple(item.line_id for item in edits)
    if edit_ids != expected_ids:
        raise LyricCorrectionError(
            "lyric editor save must retain every source line in source order"
        )
    if len(set(edit_ids)) != len(edit_ids):
        raise LyricCorrectionError("lyric editor line IDs must be unique")
    for edit in edits:
        if len(edit.text) > MAX_CORRECTED_LINE_CHARS:
            raise LyricCorrectionError(
                "corrected lyric line exceeds the supported size"
            )
        if any(value in edit.text for value in ("\0", "\n", "\r")):
            raise LyricCorrectionError("corrected lyric text must remain line-aligned")
    if any(
        line.source_start_ms is not None and edit.start_ms is None
        for line, edit in zip(snapshot.lines, edits, strict=True)
    ):
        raise LyricCorrectionError(
            "a timed source line cannot be made untimed; reset its timestamp instead"
        )
    if not any(edit.text.strip() for edit in edits):
        raise LyricCorrectionError("corrected lyrics must retain visible text")
    starts = tuple(item.start_ms for item in edits)
    timing_changed = any(
        edit.start_ms != line.source_start_ms
        for line, edit in zip(snapshot.lines, edits, strict=True)
    )
    if not timing_changed:
        return
    if (
        snapshot.can_stamp_plain
        and any(value is not None for value in starts)
        and any(value is None for value in starts)
    ):
        raise LyricCorrectionError(
            "plain lyric stamping requires a timestamp for every line"
        )
    if snapshot.duration_ms is not None and any(
        value is not None and value > snapshot.duration_ms + 2_000 for value in starts
    ):
        raise LyricCorrectionError("a corrected timestamp exceeds track duration")
    if snapshot.can_stamp_plain:
        ordered_indexes = tuple(range(len(snapshot.lines)))
    else:
        ordered_indexes = tuple(
            sorted(
                range(len(snapshot.lines)),
                key=lambda index: (
                    snapshot.lines[index].source_start_ms is None,
                    snapshot.lines[index].source_start_ms or 0,
                    index,
                ),
            )
        )
    ordered = tuple(starts[index] for index in ordered_indexes)
    timed = tuple(value for value in ordered if value is not None)
    if timed != tuple(sorted(timed)):
        raise LyricCorrectionError(
            "corrected lyric timestamps would reverse the established line order"
        )
