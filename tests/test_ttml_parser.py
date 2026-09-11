"""Synthetic TTML rich-timing parsing without third-party lyric fixtures."""

from __future__ import annotations

from konokashi.domain.lyrics import (
    LyricsTextParseStatus,
    LyricTimingLevel,
    TimingProvenance,
)
from konokashi.infrastructure.lyrics.ttml import parse_ttml_text


def _document(body: str, *, time_base: str = "media") -> str:
    return (
        '<tt xmlns="http://www.w3.org/ns/ttml" '
        'xmlns:ttp="http://www.w3.org/ns/ttml#parameter" '
        f'ttp:timeBase="{time_base}"><body><div>{body}</div></body></tt>'
    )


def test_word_timing_preserves_lines_segments_and_ends() -> None:
    text = _document(
        '<p xml:id="source-a" begin="0:01.250" end="0:03.500">'
        '<span begin="0:01.250" end="0:02.000">Synthetic</span> '
        '<span begin="0:02.000" end="0:03.500">words</span></p>'
    )

    parsed = parse_ttml_text(text)

    assert parsed.status is LyricsTextParseStatus.SYNCED
    assert parsed.timing_level is LyricTimingLevel.WORD
    assert parsed.normalized_text == text
    line = parsed.lines[0]
    assert (line.text, line.start_ms, line.end_ms, line.source_line_id) == (
        "Synthetic words",
        1_250,
        3_500,
        None,
    )
    assert [segment.text for segment in line.timing_segments] == [
        "Synthetic ",
        "words",
    ]
    assert [segment.start_ms for segment in line.timing_segments] == [1_250, 2_000]
    assert [segment.end_ms for segment in line.timing_segments] == [2_000, 3_500]
    assert all(
        segment.timing_provenance is TimingProvenance.PROVIDER
        for segment in line.timing_segments
    )


def test_nested_background_role_uses_only_timed_leaf_spans() -> None:
    parsed = parse_ttml_text(
        _document(
            '<p begin="1s" end="4s">Lead '
            '<span role="x-bg">('
            '<span begin="2s" end="3s">background</span>)</span></p>'
        )
    )

    assert parsed.status is LyricsTextParseStatus.SYNCED
    assert parsed.lines[0].text == "Lead (background)"
    assert len(parsed.lines[0].timing_segments) == 1
    assert parsed.lines[0].timing_segments[0].text == "background)"


def test_parallel_source_order_and_bare_seconds_are_retained() -> None:
    parsed = parse_ttml_text(
        _document(
            '<p begin="1.250" end="2.000">'
            '<span begin="1.500" end="2.500">Lead</span> '
            '<span role="x-bg">('
            '<span begin="1.000" end="1.750">echo</span>)</span></p>'
        )
    )

    assert parsed.status is LyricsTextParseStatus.SYNCED
    assert [item.start_ms for item in parsed.lines[0].timing_segments] == [1500, 1000]
    assert parsed.lines[0].start_ms == 1000
    assert parsed.lines[0].end_ms == 2500
    assert any("source text order" in item for item in parsed.diagnostics)
    assert any("line end was extended" in item for item in parsed.diagnostics)
    assert any("line start was extended" in item for item in parsed.diagnostics)


def test_line_only_ttml_degrades_honestly_to_line_timing() -> None:
    parsed = parse_ttml_text(
        _document('<p begin="0:01.000" end="0:02.000">Synthetic line</p>')
    )

    assert parsed.status is LyricsTextParseStatus.SYNCED
    assert parsed.timing_level is LyricTimingLevel.LINE
    assert parsed.lines[0].timing_segments == ()


def test_invalid_element_timing_degrades_only_that_line() -> None:
    parsed = parse_ttml_text(
        _document(
            '<p begin="2s" end="3s"><span begin="2.5s" end="2s">Bad element</span></p>'
        )
    )

    assert parsed.status is LyricsTextParseStatus.SYNCED
    assert parsed.timing_level is LyricTimingLevel.LINE
    assert parsed.lines[0].timing_segments == ()
    assert any("line timing remains" in item for item in parsed.diagnostics)


def test_out_of_order_lines_are_sorted_with_diagnostic() -> None:
    parsed = parse_ttml_text(
        _document(
            '<p begin="0:02.000" end="0:03.000">Second</p>'
            '<p begin="0:01.000" end="0:02.000">First</p>'
        )
    )

    assert [line.text for line in parsed.lines] == ["First", "Second"]
    assert "out-of-order TTML lines" in parsed.diagnostics[0]


def test_malformed_unsupported_and_out_of_bounds_timing_is_invalid() -> None:
    values = (
        "not XML",
        _document('<p begin="0:01.000">Line</p>', time_base="smpte"),
        _document('<p begin="later">Line</p>'),
        _document('<p begin="2s" end="1s">Line</p>'),
    )

    assert all(
        parse_ttml_text(value).status is LyricsTextParseStatus.INVALID
        for value in values
    )
