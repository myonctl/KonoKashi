from typing import Final, overload

class SegmentRecord:
    segment_id: Final[str]
    text: Final[str]
    start_ms: Final[int]
    end_ms: Final[int | None]

class LineRecord:
    line_id: Final[str]
    text: Final[str]
    start_ms: Final[int | None]
    segments: Final[list[SegmentRecord]]
    source_position: Final[int]
    text_from_source: Final[bool]

class ParseResult:
    status: Final[str]
    lines: Final[list[LineRecord]]
    plain_lines: Final[list[tuple[str, int]]]
    metadata: Final[list[tuple[str, str]]]
    diagnostics: Final[list[str]]
    normalized_text: Final[str]
    normalized_text_from_source: Final[bool]
    raw_text_checksum: Final[str | None]
    timing_level: Final[str]

@overload
def parse(text: str, duration_ms: int | None = None) -> ParseResult: ...
@overload
def parse(text: bytes, duration_ms: int | None = None) -> ParseResult: ...
@overload
def parse(text: bytes, duration_ms: int | None, source: str) -> ParseResult: ...
