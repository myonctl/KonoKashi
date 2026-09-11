"""Conservative TTML timing parser for provider-owned rich lyric documents."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from decimal import ROUND_HALF_UP, Decimal, DecimalException
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

MAX_TTML_CHARS = 2_000_000
MAX_TTML_NODES = 100_000
MAX_TTML_TIMESTAMP_MS = 2**63 - 1
_CLOCK_TIME = re.compile(r"^(?:(\d+):)?([0-5]?\d):([0-5]\d)(?:\.(\d{1,9}))?$")
_OFFSET_SECONDS = re.compile(r"^(\d+(?:\.\d{1,9})?)s$")
_BARE_SECONDS = re.compile(r"^(\d+(?:\.\d{1,9})?)$")


def parse_ttml_text(text: str) -> ParsedLyricsText:
    """Parse media-clock TTML into stable line and word timing identities."""

    if len(text) > MAX_TTML_CHARS:
        return _invalid("TTML lyrics exceed the bounded parser size")
    normalized = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    checksum = sha256(normalized.encode("utf-8")).hexdigest()
    try:
        root = ET.fromstring(normalized)
    except (ET.ParseError, RecursionError):
        return _invalid(
            "TTML lyrics contain malformed XML",
            normalized=normalized,
            checksum=checksum,
        )
    nodes = tuple(root.iter())
    if len(nodes) > MAX_TTML_NODES:
        return _invalid(
            "TTML lyrics exceed the bounded node count",
            normalized=normalized,
            checksum=checksum,
        )
    if _local_name(root.tag) != "tt":
        return _invalid(
            "TTML root element is not tt",
            normalized=normalized,
            checksum=checksum,
        )
    time_base = _attribute(root, "timeBase")
    if time_base not in {None, "media"}:
        return _invalid(
            f"TTML time base {time_base!r} is unsupported",
            normalized=normalized,
            checksum=checksum,
        )

    lines: list[LyricLine] = []
    diagnostics: list[str] = []
    previous_start = -1
    for source_position, paragraph in enumerate(
        node for node in nodes if _local_name(node.tag) == "p"
    ):
        try:
            start_ms = _time_ms(_attribute(paragraph, "begin"))
            end_ms = _optional_time_ms(_attribute(paragraph, "end"))
        except ValueError as error:
            return _invalid(
                f"TTML line {source_position + 1}: {error}",
                normalized=normalized,
                checksum=checksum,
            )
        if end_ms is not None and end_ms < start_ms:
            return _invalid(
                f"TTML line {source_position + 1}: end precedes start",
                normalized=normalized,
                checksum=checksum,
            )
        if start_ms < previous_start:
            diagnostics.append("out-of-order TTML lines were ordered chronologically")
        previous_start = start_ms
        line_text = "".join(paragraph.itertext()).strip()
        source_line_id = _attribute(paragraph, "id")
        line_id = _line_id(checksum, source_position, start_ms, source_line_id)
        segments: list[LyricTimingSegment] = []
        for element_position, span in enumerate(_timed_leaf_spans(paragraph)):
            try:
                segment_start = _time_ms(_attribute(span, "begin"))
                segment_end = _optional_time_ms(_attribute(span, "end"))
            except ValueError as error:
                return _invalid(
                    f"TTML line {source_position + 1}, element "
                    f"{element_position + 1}: {error}",
                    normalized=normalized,
                    checksum=checksum,
                )
            if segment_end is not None and segment_end < segment_start:
                diagnostics.append(
                    f"TTML line {source_position + 1}: invalid element timing was "
                    "discarded; line timing remains available"
                )
                segments.clear()
                break
            segment_text = "".join(span.itertext()) + _semantic_tail(span.tail)
            segments.append(
                LyricTimingSegment(
                    _segment_id(line_id, element_position, segment_start),
                    segment_text,
                    segment_start,
                    segment_end,
                    LyricTimingUnit.WORD,
                    TimingProvenance.PROVIDER,
                )
            )
        starts = [segment.start_ms for segment in segments]
        if starts and min(starts) < start_ms:
            diagnostics.append(
                "TTML line start was extended to retain an earlier timed element"
            )
            start_ms = min(starts)
        if starts != sorted(starts):
            diagnostics.append(
                "parallel or out-of-order TTML elements were preserved in source "
                "text order"
            )
        segment_ends = tuple(
            segment.end_ms for segment in segments if segment.end_ms is not None
        )
        if segment_ends and (end_ms is None or max(segment_ends) > end_ms):
            diagnostics.append(
                "TTML line end was extended to retain a later timed element"
            )
            end_ms = max(segment_ends)
        lines.append(
            LyricLine(
                line_id,
                line_text,
                start_ms,
                end_ms,
                TimingProvenance.PROVIDER,
                None,
                tuple(segments),
            )
        )
    if not lines:
        return _invalid(
            "TTML lyrics contain no timed lines",
            normalized=normalized,
            checksum=checksum,
        )
    lines.sort(key=lambda line: (line.start_ms or 0, line.line_id))
    timing_level = (
        LyricTimingLevel.WORD
        if any(line.timing_segments for line in lines)
        else LyricTimingLevel.LINE
    )
    return ParsedLyricsText(
        LyricsTextParseStatus.SYNCED,
        tuple(lines),
        diagnostics=tuple(dict.fromkeys(diagnostics)),
        normalized_text=normalized,
        raw_text_checksum=checksum,
        timing_level=timing_level,
    )


def _timed_leaf_spans(paragraph: ET.Element) -> tuple[ET.Element, ...]:
    result: list[ET.Element] = []
    for span in paragraph.iter():
        if span is paragraph or _local_name(span.tag) != "span":
            continue
        if _attribute(span, "begin") is None:
            continue
        if any(
            descendant is not span
            and _local_name(descendant.tag) == "span"
            and _attribute(descendant, "begin") is not None
            for descendant in span.iter()
        ):
            continue
        result.append(span)
    return tuple(result)


def _semantic_tail(value: str | None) -> str:
    if value is None:
        return ""
    if "\n" in value or "\r" in value:
        return " " if value.strip() else ""
    return value


def _time_ms(value: str | None) -> int:
    if value is None:
        raise ValueError("begin time is missing")
    match = _CLOCK_TIME.fullmatch(value.strip())
    try:
        if match is not None:
            hours = int(match.group(1) or 0)
            minutes = int(match.group(2))
            seconds = int(match.group(3))
            fraction = match.group(4) or ""
            milliseconds = int((fraction + "000")[:3])
            result = ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds
        else:
            offset = _OFFSET_SECONDS.fullmatch(
                value.strip()
            ) or _BARE_SECONDS.fullmatch(value.strip())
            if offset is None:
                raise ValueError("time expression is unsupported")
            result = int(
                (Decimal(offset.group(1)) * 1000).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
    except (DecimalException, OverflowError) as error:
        raise ValueError("time expression is invalid") from error
    if not 0 <= result <= MAX_TTML_TIMESTAMP_MS:
        raise ValueError("time expression is outside the supported range")
    return result


def _optional_time_ms(value: str | None) -> int | None:
    return None if value is None else _time_ms(value)


def _attribute(element: ET.Element, local_name: str) -> str | None:
    return next(
        (
            value
            for name, value in element.attrib.items()
            if _local_name(name) == local_name
        ),
        None,
    )


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _line_id(
    checksum: str, position: int, start_ms: int, source_line_id: str | None
) -> str:
    payload = f"{checksum}:{position}:{start_ms}:{source_line_id or ''}".encode()
    return f"line-{sha256(payload).hexdigest()[:20]}"


def _segment_id(line_id: str, position: int, start_ms: int) -> str:
    payload = f"{line_id}:{position}:{start_ms}".encode()
    return f"segment-{sha256(payload).hexdigest()[:20]}"


def _invalid(
    diagnostic: str,
    *,
    normalized: str = "",
    checksum: str | None = None,
) -> ParsedLyricsText:
    return ParsedLyricsText(
        LyricsTextParseStatus.INVALID,
        diagnostics=(diagnostic,),
        normalized_text=normalized,
        raw_text_checksum=checksum,
    )
