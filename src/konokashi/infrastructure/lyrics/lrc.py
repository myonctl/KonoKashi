"""Native LRC parsing behind the stable domain-facing Python API."""

from __future__ import annotations

from konokashi import _lrc_native as _native
from konokashi.domain.lyrics import (
    LyricLine,
    LyricsTextParseStatus,
    LyricTimingLevel,
    LyricTimingSegment,
    LyricTimingUnit,
    ParsedLyricsText,
    TimingProvenance,
)
from konokashi.infrastructure.lyrics.lrc_reference import (
    MAX_LYRIC_TIMESTAMP_MS,
    MAX_LYRICS_TEXT_CHARS,
)


def _native_duration(duration_ms: int | None) -> int | None:
    """Map arbitrary Python integers to the native parser's bounded comparison."""

    if duration_ms is None:
        return None
    if duration_ms < -2_000:
        # Every valid non-negative LRC timestamp exceeds this threshold.
        return -2_001
    if duration_ms > MAX_LYRIC_TIMESTAMP_MS - 2_000:
        # No valid LRC timestamp can exceed duration + the diagnostic grace.
        return None
    return duration_ms


def parse_lyrics_text(
    text: str,
    *,
    timing_provenance: TimingProvenance | None = TimingProvenance.PROVIDER,
    duration_ms: int | None = None,
) -> ParsedLyricsText:
    """Parse ordinary LRC or retain plain lyrics without manufacturing timing."""

    text_size = len(text)
    native_duration = _native_duration(duration_ms)
    parsed = (
        _native.parse(text, native_duration)
        if text_size > MAX_LYRICS_TEXT_CHARS
        else _native.parse(text.encode("utf-8"), native_duration, text)
    )
    normalized_text = (
        text if parsed.normalized_text_from_source else parsed.normalized_text
    )
    native_lines = parsed.lines
    plain_lines = parsed.plain_lines
    source_lines = (
        (normalized_text,)
        if len(plain_lines) == 1
        and plain_lines[0][1] == 0
        and "\n" not in normalized_text
        else normalized_text.split("\n")
        if plain_lines
        else ()
    )
    plain_domain_buffer: list[LyricLine] = []
    while plain_lines:
        line_id, source_position = plain_lines.pop()
        plain_domain_buffer.append(LyricLine(line_id, source_lines[source_position]))
    plain_domain_buffer.reverse()
    source_lines = ()
    plain_domain_lines = tuple(plain_domain_buffer)
    timed_domain_lines = tuple(
        LyricLine(
            line.line_id,
            line.text,
            line.start_ms,
            timing_provenance=(
                timing_provenance if line.start_ms is not None else None
            ),
            timing_segments=tuple(
                LyricTimingSegment(
                    segment.segment_id,
                    segment.text,
                    segment.start_ms,
                    segment.end_ms,
                    LyricTimingUnit.WORD,
                    timing_provenance,
                )
                for segment in line.segments
            ),
        )
        for line in native_lines
    )
    return ParsedLyricsText(
        LyricsTextParseStatus(parsed.status),
        plain_domain_lines or timed_domain_lines,
        tuple(parsed.metadata),
        tuple(parsed.diagnostics),
        normalized_text,
        parsed.raw_text_checksum,
        LyricTimingLevel(parsed.timing_level),
    )


__all__ = [
    "MAX_LYRICS_TEXT_CHARS",
    "MAX_LYRIC_TIMESTAMP_MS",
    "parse_lyrics_text",
]
