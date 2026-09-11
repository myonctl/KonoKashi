"""Portable plain/LRC exchange for corrected lyric lines."""

from __future__ import annotations

from enum import Enum

from konokashi.application.representations import original_lines
from konokashi.domain.lyric_corrections import LyricLineEdit
from konokashi.domain.lyrics import (
    LyricDocument,
    LyricsTextParseStatus,
    ParsedLyricsText,
    lyric_line_timing_start_ms,
)


class LyricExchangeFormat(Enum):
    """Formats deliberately supported by the initial compact editor."""

    PLAIN = "plain"
    LRC = "lrc"


def serialize_lyric_edits(
    edits: tuple[LyricLineEdit, ...], format: LyricExchangeFormat
) -> str:
    """Serialize editor values without embedding paths or database identity."""

    if format is LyricExchangeFormat.PLAIN:
        return "\n".join(item.text for item in edits) + "\n"
    if any(item.start_ms is None for item in edits):
        raise ValueError("LRC export requires a timestamp for every lyric line")
    return "".join(f"[{_lrc_timestamp(item.start_ms)}]{item.text}\n" for item in edits)


def imported_lyric_edits(
    source: LyricDocument, parsed: ParsedLyricsText
) -> tuple[LyricLineEdit, ...]:
    """Align a bounded LRC/plain import only when every source line maps exactly."""

    if parsed.status is LyricsTextParseStatus.INVALID:
        raise ValueError("imported lyrics are invalid")
    source_lines = original_lines(source)
    imported_lines = list(parsed.lines)
    while imported_lines and not imported_lines[-1].text:
        imported_lines.pop()
    if len(imported_lines) != len(source_lines):
        raise ValueError(
            "imported lyrics must contain exactly one line for every source line"
        )
    return tuple(
        LyricLineEdit(
            source_line.line_id,
            imported.text,
            (
                lyric_line_timing_start_ms(source_line)
                if parsed.status is LyricsTextParseStatus.PLAIN
                else lyric_line_timing_start_ms(imported)
            ),
        )
        for source_line, imported in zip(source_lines, imported_lines, strict=True)
    )


def document_lyric_edits(document: LyricDocument) -> tuple[LyricLineEdit, ...]:
    """Project one effective document into portable line-editor values."""

    return tuple(
        LyricLineEdit(line.line_id, line.text, lyric_line_timing_start_ms(line))
        for line in original_lines(document)
    )


def _lrc_timestamp(value_ms: int | None) -> str:
    assert value_ms is not None
    minutes, remainder = divmod(value_ms, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    if minutes > 999:
        raise ValueError("LRC export cannot represent timestamps beyond 999 minutes")
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
