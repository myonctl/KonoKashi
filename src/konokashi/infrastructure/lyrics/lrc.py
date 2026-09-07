"""Bounded LRC/plain-text parsing with exact integer-millisecond timing."""

from __future__ import annotations

import re
from hashlib import sha256

from konokashi.domain.lyrics import (
    LyricLine,
    LyricsTextParseStatus,
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
_ENHANCED_TIMESTAMP = re.compile(r"<\d{1,3}:[0-5]\d[.:]\d{2,3}>")


def _line_id(
    checksum: str, source_position: int, copy: int, start_ms: int | None
) -> str:
    payload = f"{checksum}:{source_position}:{copy}:{start_ms}".encode()
    return f"line-{sha256(payload).hexdigest()[:20]}"


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
    timed: list[tuple[int, int, int, str]] = []
    plain_source: list[tuple[int, str]] = []
    offset_ms = 0
    invalid_timing = False

    for source_position, source_line in enumerate(normalized.split("\n")):
        remaining = source_line
        timestamps: list[int] = []
        while match := _TIMESTAMP.match(remaining):
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            fraction = match.group(3)
            milliseconds = int(fraction) * (10 if len(fraction) == 2 else 1)
            timestamps.append((minutes * 60 + seconds) * 1000 + milliseconds)
            remaining = remaining[match.end() :]

        if timestamps:
            if _ENHANCED_TIMESTAMP.search(remaining):
                diagnostics.append(
                    f"line {source_position + 1}: enhanced word timing retained as text"
                )
            for copy, timestamp in enumerate(timestamps):
                timed.append((timestamp, source_position, copy, remaining))
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
        adjusted: list[tuple[int, int, int, str]] = []
        for timestamp, source_position, copy, line_text in timed:
            start_ms = timestamp + offset_ms
            if start_ms < 0:
                diagnostics.append(
                    f"line {source_position + 1}: offset produced a negative timestamp"
                )
                invalid_timing = True
                continue
            if start_ms > MAX_LYRIC_TIMESTAMP_MS:
                diagnostics.append(
                    f"line {source_position + 1}: timestamp is outside the supported "
                    "integer range"
                )
                invalid_timing = True
                continue
            if duration_ms is not None and start_ms > duration_ms + 2_000:
                diagnostics.append(
                    f"line {source_position + 1}: timestamp exceeds track duration"
                )
            adjusted.append((start_ms, source_position, copy, line_text))
        if invalid_timing:
            return ParsedLyricsText(
                LyricsTextParseStatus.INVALID,
                metadata=tuple(metadata),
                diagnostics=tuple(dict.fromkeys(diagnostics)),
                normalized_text=normalized,
                raw_text_checksum=checksum,
            )
        source_order = [item[0] for item in adjusted]
        if source_order != sorted(source_order):
            diagnostics.append("out-of-order timestamps were ordered chronologically")
        starts = [item[0] for item in adjusted]
        if len(starts) != len(set(starts)):
            diagnostics.append("duplicate timestamps were preserved as distinct lines")
        adjusted.sort(key=lambda item: (item[0], item[1], item[2]))
        lines = tuple(
            LyricLine(
                _line_id(checksum, source_position, copy, start_ms),
                line_text,
                start_ms=start_ms,
                timing_provenance=timing_provenance,
            )
            for start_ms, source_position, copy, line_text in adjusted
        )
        return ParsedLyricsText(
            LyricsTextParseStatus.SYNCED,
            lines,
            tuple(metadata),
            tuple(dict.fromkeys(diagnostics)),
            normalized,
            checksum,
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
    lines = tuple(
        LyricLine(_line_id(checksum, source_position, 0, None), line_text)
        for source_position, line_text in plain_source
    )
    return ParsedLyricsText(
        LyricsTextParseStatus.PLAIN,
        lines,
        tuple(metadata),
        tuple(dict.fromkeys(diagnostics)),
        normalized,
        checksum,
    )
