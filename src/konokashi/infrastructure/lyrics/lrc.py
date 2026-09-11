"""Bounded LRC/plain-text parsing with exact integer-millisecond timing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256

from konokashi.domain.lyrics import (
    LyricLine,
    LyricsTextParseStatus,
    LyricTimingLevel,
    LyricTimingSegment,
    LyricTimingUnit,
    ParsedLyricsText,
    TimingProvenance,
)

MAX_LYRICS_TEXT_CHARS = 2_000_000
MAX_LYRIC_TIMESTAMP_MS = 2**63 - 1

_TIMESTAMP = re.compile(r"\[(\d{1,3}):([0-5]\d)[.:](\d{2,3})\]")
_TIMESTAMP_LIKE = re.compile(r"^\[\d{1,3}:[^\]]*\]")
_METADATA = re.compile(r"^\[([A-Za-z][A-Za-z0-9_-]*):(.*)\]$")
_SUPPORTED_METADATA = frozenset({"ar", "ti", "al", "by", "re", "ve", "length"})
_MALFORMED_KNOWN_METADATA = re.compile(
    r"^\[(ar|ti|al|by|re|ve|length|offset)(?:\s+[^\]]*)?\]$", re.IGNORECASE
)
_ENHANCED_TIMESTAMP = re.compile(r"<(\d{1,3}):([0-5]\d)[.:](\d{2,3})>")


@dataclass(frozen=True, slots=True)
class _EnhancedPart:
    """One source-order enhanced-LRC text part and its absolute marker."""

    start_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class _TimedSourceLine:
    """One expanded line timestamp before offset and range validation."""

    start_ms: int
    source_position: int
    copy: int
    text: str
    enhanced_parts: tuple[_EnhancedPart, ...] = ()


def _line_id(
    checksum: str, source_position: int, copy: int, start_ms: int | None
) -> str:
    payload = f"{checksum}:{source_position}:{copy}:{start_ms}".encode()
    return f"line-{sha256(payload).hexdigest()[:20]}"


def _timestamp_ms(match: re.Match[str]) -> int:
    """Convert one validated LRC timestamp match to exact milliseconds."""

    fraction = match.group(3)
    milliseconds = int(fraction) * (10 if len(fraction) == 2 else 1)
    return (int(match.group(1)) * 60 + int(match.group(2))) * 1000 + milliseconds


def _enhanced_parts(text: str) -> tuple[str, tuple[_EnhancedPart, ...]]:
    """Remove enhanced markers while preserving every source text character."""

    matches = tuple(_ENHANCED_TIMESTAMP.finditer(text))
    if not matches:
        return text, ()
    prefix = text[: matches[0].start()]
    parts = [
        _EnhancedPart(
            _timestamp_ms(match),
            text[match.end() : matches[index + 1].start()]
            if index + 1 < len(matches)
            else text[match.end() :],
        )
        for index, match in enumerate(matches)
    ]
    if prefix:
        parts[0] = _EnhancedPart(parts[0].start_ms, prefix + parts[0].text)
    return "".join(part.text for part in parts), tuple(parts)


def _segment_id(line_id: str, position: int, start_ms: int) -> str:
    payload = f"{line_id}:{position}:{start_ms}".encode()
    return f"segment-{sha256(payload).hexdigest()[:20]}"


def parse_lyrics_text(
    text: str,
    *,
    timing_provenance: TimingProvenance | None = TimingProvenance.PROVIDER,
    duration_ms: int | None = None,
) -> ParsedLyricsText:
    """Parse ordinary LRC or retain plain lyrics without manufacturing timing."""

    if len(text) > MAX_LYRICS_TEXT_CHARS:
        return ParsedLyricsText(
            LyricsTextParseStatus.INVALID,
            diagnostics=("lyrics text exceeds the bounded parser size",),
        )
    normalized = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    checksum = sha256(normalized.encode("utf-8")).hexdigest()
    metadata: list[tuple[str, str]] = []
    diagnostics: list[str] = []
    timed: list[_TimedSourceLine] = []
    plain_source: list[tuple[int, str]] = []
    offset_ms = 0
    invalid_timing = False

    for source_position, source_line in enumerate(normalized.split("\n")):
        remaining = source_line
        timestamps: list[int] = []
        while match := _TIMESTAMP.match(remaining):
            timestamps.append(_timestamp_ms(match))
            remaining = remaining[match.end() :]

        if timestamps:
            line_text, enhanced_parts = _enhanced_parts(remaining)
            for copy, timestamp in enumerate(timestamps):
                timing_delta = timestamp - timestamps[0]
                timed.append(
                    _TimedSourceLine(
                        timestamp,
                        source_position,
                        copy,
                        line_text,
                        tuple(
                            _EnhancedPart(part.start_ms + timing_delta, part.text)
                            for part in enhanced_parts
                        ),
                    )
                )
            continue

        if _TIMESTAMP_LIKE.match(source_line):
            diagnostics.append(
                f"line {source_position + 1}: malformed timestamp was not reinterpreted"
            )
            invalid_timing = True
            continue

        metadata_match = _METADATA.match(source_line)
        if metadata_match is not None:
            key = metadata_match.group(1).casefold()
            value = metadata_match.group(2).strip()
            if key == "offset":
                try:
                    offset_ms = int(value)
                except ValueError:
                    diagnostics.append(
                        f"line {source_position + 1}: malformed offset metadata"
                    )
                    invalid_timing = True
                else:
                    if abs(offset_ms) > MAX_LYRIC_TIMESTAMP_MS:
                        diagnostics.append(
                            f"line {source_position + 1}: offset metadata is outside "
                            "the supported integer range"
                        )
                        invalid_timing = True
                    else:
                        metadata.append((key, value))
            elif key in _SUPPORTED_METADATA:
                metadata.append((key, value))
            else:
                diagnostics.append(
                    f"line {source_position + 1}: unsupported metadata tag "
                    f"{key!r} ignored"
                )
            continue
        if _MALFORMED_KNOWN_METADATA.match(source_line):
            diagnostics.append(
                f"line {source_position + 1}: malformed lyrics metadata ignored"
            )
            continue
        plain_source.append((source_position, source_line))

    if timed:
        if invalid_timing:
            return ParsedLyricsText(
                LyricsTextParseStatus.INVALID,
                metadata=tuple(metadata),
                diagnostics=tuple(dict.fromkeys(diagnostics)),
                normalized_text=normalized,
                raw_text_checksum=checksum,
            )
        adjusted: list[_TimedSourceLine] = []
        for timed_source in timed:
            start_ms = timed_source.start_ms + offset_ms
            if start_ms < 0:
                diagnostics.append(
                    f"line {timed_source.source_position + 1}: offset produced a "
                    "negative timestamp"
                )
                invalid_timing = True
                continue
            if start_ms > MAX_LYRIC_TIMESTAMP_MS:
                diagnostics.append(
                    f"line {timed_source.source_position + 1}: timestamp is outside "
                    "the supported integer range"
                )
                invalid_timing = True
                continue
            if duration_ms is not None and start_ms > duration_ms + 2_000:
                diagnostics.append(
                    f"line {timed_source.source_position + 1}: timestamp exceeds track "
                    "duration"
                )
            adjusted_parts: list[_EnhancedPart] = []
            enhanced_starts = [part.start_ms for part in timed_source.enhanced_parts]
            if enhanced_starts != sorted(enhanced_starts):
                diagnostics.append(
                    f"line {timed_source.source_position + 1}: enhanced timestamps "
                    "are out of order"
                )
                invalid_timing = True
            for part in timed_source.enhanced_parts:
                segment_start_ms = part.start_ms + offset_ms
                if not 0 <= segment_start_ms <= MAX_LYRIC_TIMESTAMP_MS:
                    diagnostics.append(
                        f"line {timed_source.source_position + 1}: enhanced timestamp "
                        "is outside the supported integer range"
                    )
                    invalid_timing = True
                    continue
                adjusted_parts.append(_EnhancedPart(segment_start_ms, part.text))
            adjusted.append(
                _TimedSourceLine(
                    start_ms,
                    timed_source.source_position,
                    timed_source.copy,
                    timed_source.text,
                    tuple(adjusted_parts),
                )
            )
        if invalid_timing:
            return ParsedLyricsText(
                LyricsTextParseStatus.INVALID,
                metadata=tuple(metadata),
                diagnostics=tuple(dict.fromkeys(diagnostics)),
                normalized_text=normalized,
                raw_text_checksum=checksum,
            )
        source_order = [item.start_ms for item in adjusted]
        if source_order != sorted(source_order):
            diagnostics.append("out-of-order timestamps were ordered chronologically")
        starts = [item.start_ms for item in adjusted]
        if len(starts) != len(set(starts)):
            diagnostics.append("duplicate timestamps were preserved as distinct lines")
        adjusted.sort(key=lambda item: (item.start_ms, item.source_position, item.copy))
        timed_lines: list[LyricLine] = []
        for item in adjusted:
            line_id = _line_id(checksum, item.source_position, item.copy, item.start_ms)
            segments = tuple(
                LyricTimingSegment(
                    segment_id=_segment_id(line_id, position, part.start_ms),
                    text=part.text,
                    start_ms=part.start_ms,
                    end_ms=(
                        item.enhanced_parts[position + 1].start_ms
                        if position + 1 < len(item.enhanced_parts)
                        else None
                    ),
                    unit=LyricTimingUnit.WORD,
                    timing_provenance=timing_provenance,
                )
                for position, part in enumerate(item.enhanced_parts)
            )
            timed_lines.append(
                LyricLine(
                    line_id,
                    item.text,
                    start_ms=item.start_ms,
                    timing_provenance=timing_provenance,
                    timing_segments=segments,
                )
            )
        timing_level = (
            LyricTimingLevel.WORD
            if any(line.timing_segments for line in timed_lines)
            else LyricTimingLevel.LINE
        )
        return ParsedLyricsText(
            LyricsTextParseStatus.SYNCED,
            tuple(timed_lines),
            tuple(metadata),
            tuple(dict.fromkeys(diagnostics)),
            normalized,
            checksum,
            timing_level,
        )

    if invalid_timing:
        return ParsedLyricsText(
            LyricsTextParseStatus.INVALID,
            metadata=tuple(metadata),
            diagnostics=tuple(dict.fromkeys(diagnostics)),
            normalized_text=normalized,
            raw_text_checksum=checksum,
        )
    if not any(line.strip() for _, line in plain_source):
        return ParsedLyricsText(
            LyricsTextParseStatus.INVALID,
            metadata=tuple(metadata),
            diagnostics=(*tuple(dict.fromkeys(diagnostics)), "lyrics contain no text"),
            normalized_text=normalized,
            raw_text_checksum=checksum,
        )
    plain_lines = tuple(
        LyricLine(_line_id(checksum, source_position, 0, None), line_text)
        for source_position, line_text in plain_source
    )
    return ParsedLyricsText(
        LyricsTextParseStatus.PLAIN,
        plain_lines,
        tuple(metadata),
        tuple(dict.fromkeys(diagnostics)),
        normalized,
        checksum,
        LyricTimingLevel.UNSYNCHRONIZED,
    )
