"""Synthetic TTML rich-timing parsing without third-party lyric fixtures."""

from __future__ import annotations

from konokashi.domain.lyrics import (
    LyricsTextParseStatus,
    LyricTimingLevel,
    LyricTimingUnit,
    TimingProvenance,
)
from konokashi.infrastructure.lyrics.ttml import parse_ttml_text


def _document(
    body: str,
    *,
    time_base: str = "media",
    timing: str | None = None,
) -> str:
    timing_attribute = "" if timing is None else f' itunes:timing="{timing}"'
    return (
        '<tt xmlns="http://www.w3.org/ns/ttml" '
        'xmlns:ttp="http://www.w3.org/ns/ttml#parameter" '
        'xmlns:itunes="http://music.apple.com/lyric-ttml-internal" '
        'xmlns:ext="urn:synthetic:timing-semantics" '
        f'ttp:timeBase="{time_base}"{timing_attribute}>'
        f"<body><div>{body}</div></body></tt>"
    )


def test_word_timing_preserves_lines_segments_and_ends() -> None:
    text = _document(
        '<p xml:id="source-a" begin="0:01.250" end="0:03.500">'
        '<span ext:unit="word" begin="0:01.250" '
        'end="0:02.000">Synthetic</span> '
        '<span ext:unit="word" begin="0:02.000" '
        'end="0:03.500">words</span></p>'
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
    assert all(segment.unit is LyricTimingUnit.WORD for segment in line.timing_segments)
    assert all(segment.provider_unit is None for segment in line.timing_segments)


def test_apple_word_mode_does_not_mislabel_ambiguous_leaf_spans() -> None:
    parsed = parse_ttml_text(
        _document(
            '<p begin="1s" end="3s">'
            '<span begin="1s" end="2s">Synthetic</span> '
            '<span begin="2s" end="3s">span</span></p>',
            timing="Word",
        )
    )

    assert parsed.timing_level is LyricTimingLevel.ELEMENT
    assert [segment.unit for segment in parsed.lines[0].timing_segments] == [
        LyricTimingUnit.PROVIDER_ELEMENT,
        LyricTimingUnit.PROVIDER_ELEMENT,
    ]
    assert {segment.provider_unit for segment in parsed.lines[0].timing_segments} == {
        "itunes-word-span"
    }


def test_explicit_syllable_and_grapheme_units_remain_distinct() -> None:
    parsed = parse_ttml_text(
        _document(
            '<p begin="1s" end="3s">'
            '<span ext:unit="syllable" begin="1s" end="2s">Syn</span>'
            '<span ext:unit="grapheme" begin="2s" end="3s">字</span></p>'
        )
    )

    assert parsed.timing_level is LyricTimingLevel.ELEMENT
    assert [segment.unit for segment in parsed.lines[0].timing_segments] == [
        LyricTimingUnit.SYLLABLE,
        LyricTimingUnit.GRAPHEME,
    ]


def test_nested_word_and_syllable_timing_retains_parent_identity() -> None:
    parsed = parse_ttml_text(
        _document(
            '<p begin="1s" end="3s">'
            '<span ext:unit="word" begin="1s" end="3s">'
            '<span ext:unit="syllable" begin="1s" end="2s">Syn</span>'
            '<span ext:unit="syllable" begin="2s" end="3s">thetic</span>'
            "</span></p>"
        )
    )

    assert parsed.timing_level is LyricTimingLevel.ELEMENT
    parent, first, second = parsed.lines[0].timing_segments
    assert parent.unit is LyricTimingUnit.WORD
    assert first.unit is second.unit is LyricTimingUnit.SYLLABLE
    assert first.parent_segment_id == parent.segment_id
    assert second.parent_segment_id == parent.segment_id


def test_timed_background_container_retains_provider_parent_identity() -> None:
    parsed = parse_ttml_text(
        _document(
            '<p begin="1s" end="3s">Lead '
            '<span role="x-bg" begin="1s" end="3s">'
            '<span begin="1s" end="2s">background</span>'
            '<span begin="2s" end="3s"> vocal</span>'
            "</span></p>"
        )
    )

    parent, first, second = parsed.lines[0].timing_segments
    assert parent.unit is LyricTimingUnit.PROVIDER_ELEMENT
    assert parent.provider_unit == "x-bg"
    assert first.parent_segment_id == parent.segment_id
    assert second.parent_segment_id == parent.segment_id


def test_unknown_explicit_provider_unit_is_retained_without_guessing() -> None:
    parsed = parse_ttml_text(
        _document(
            '<p begin="1s" end="2s">'
            '<span ext:unit="phoneme" begin="1s" end="2s">Synthetic</span>'
            "</p>"
        )
    )

    segment = parsed.lines[0].timing_segments[0]
    assert parsed.timing_level is LyricTimingLevel.ELEMENT
    assert segment.unit is LyricTimingUnit.PROVIDER_ELEMENT
    assert segment.provider_unit == "phoneme"


def test_declared_line_timing_ignores_nested_span_timestamps() -> None:
    parsed = parse_ttml_text(
        _document(
            '<p begin="1s" end="2s">'
            '<span begin="1s" end="2s">Synthetic line</span></p>',
            timing="Line",
        )
    )

    assert parsed.timing_level is LyricTimingLevel.LINE
    assert parsed.lines[0].timing_segments == ()


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
            '<p begin="2s" end="3s">'
            '<span ext:unit="word" begin="2s" end="3s">'
            '<span ext:unit="syllable" begin="2.5s" end="2s">Bad child</span>'
            "</span></p>"
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
