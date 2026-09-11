"""Explicit local lyric correction values independent of source documents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from konokashi.domain.lyrics import MAX_SEMANTIC_LYRICS_DURATION_MS, LyricDocument

MAX_CORRECTED_LINE_CHARS = 10_000


@dataclass(frozen=True, slots=True)
class LyricLineCorrection:
    """One resettable local overlay anchored to immutable source-line evidence."""

    document_id: str
    line_id: str
    based_on_text: str
    based_on_start_ms: int | None
    corrected_text: str | None
    corrected_start_ms: int | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.document_id.strip() or not self.line_id.strip():
            raise ValueError("lyric correction requires document and line IDs")
        if self.corrected_text is None and self.corrected_start_ms is None:
            raise ValueError("lyric correction must change text or timing")
        if self.corrected_text is not None:
            if len(self.corrected_text) > MAX_CORRECTED_LINE_CHARS:
                raise ValueError("corrected lyric line exceeds the supported size")
            if any(value in self.corrected_text for value in ("\0", "\n", "\r")):
                raise ValueError("corrected lyric line must remain one text line")
        if self.corrected_start_ms is not None and not (
            0 <= self.corrected_start_ms <= MAX_SEMANTIC_LYRICS_DURATION_MS
        ):
            raise ValueError("corrected lyric timestamp is outside the supported range")
        for timestamp in (self.created_at, self.updated_at):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("lyric correction timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class LyricLineEdit:
    """One editor value; unchanged fields are removed from durable overlays."""

    line_id: str
    text: str
    start_ms: int | None


@dataclass(frozen=True, slots=True)
class LyricEditorLine:
    """Source and effective values for one stable line in the compact editor."""

    line_id: str
    source_text: str
    effective_text: str
    source_start_ms: int | None
    effective_start_ms: int | None
    text_corrected: bool = False
    timing_corrected: bool = False
    stale_correction: bool = False


@dataclass(frozen=True, slots=True)
class LyricEditorSnapshot:
    """Bounded immutable editor input with explicit source/effective provenance."""

    document_id: str
    source_name: str
    duration_ms: int | None
    lines: tuple[LyricEditorLine, ...]
    corrected_lines: int
    stale_corrections: int
    can_stamp_plain: bool
    diagnostics: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class LyricCorrectionProjection:
    """Effective lyric view plus evidence about applied and stale overlays."""

    document: LyricDocument
    editor: LyricEditorSnapshot
    applied_corrections: int
    stale_corrections: int
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
