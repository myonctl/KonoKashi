"""Bounded LRC and first-class plain lyrics parsing regressions."""

from konokashi.domain.lyrics import (
    LyricsTextParseStatus,
    LyricTimingLevel,
    LyricTimingUnit,
    TimingProvenance,
)
from konokashi.infrastructure.lyrics.lrc import parse_lyrics_text


def test_lrc_hundredths_milliseconds_bom_crlf_and_unicode() -> None:
    parsed = parse_lyrics_text(
        "\ufeff[ar:歌手]\r\n[ti:題名]\r\n[00:17.12]君の声\r\n[00:18.120]が聞こえる"
    )

    assert parsed.status is LyricsTextParseStatus.SYNCED
    assert [(line.start_ms, line.text) for line in parsed.lines] == [
        (17_120, "君の声"),
        (18_120, "が聞こえる"),
    ]
    assert parsed.metadata == (("ar", "歌手"), ("ti", "題名"))
    assert parsed.normalized_text.startswith("[ar:歌手]\n")
    assert parsed.timing_level is LyricTimingLevel.LINE


def test_multiple_timestamps_create_distinct_stable_line_instances() -> None:
    text = "[00:01.00][00:02.000]Chorus\n[00:03.00]Chorus"

    first = parse_lyrics_text(text)
    second = parse_lyrics_text(text)

    assert [line.text for line in first.lines] == ["Chorus", "Chorus", "Chorus"]
    assert len({line.line_id for line in first.lines}) == 3
    assert [line.line_id for line in first.lines] == [
        line.line_id for line in second.lines
    ]
    assert first.timing_level is LyricTimingLevel.LINE


def test_valid_offset_uses_integer_milliseconds() -> None:
    parsed = parse_lyrics_text("[offset:125]\n[00:01.25]Line")

    assert parsed.status is LyricsTextParseStatus.SYNCED
    assert parsed.lines[0].start_ms == 1_375


def test_negative_offset_result_is_invalid_not_clamped() -> None:
    parsed = parse_lyrics_text("[offset:-2000]\n[00:01.00]Line")

    assert parsed.status is LyricsTextParseStatus.INVALID
    assert any("negative timestamp" in item for item in parsed.diagnostics)
    assert parsed.lines == ()


def test_malformed_timestamp_is_not_reinterpreted_as_plain_text() -> None:
    parsed = parse_lyrics_text("[00:17.1]Convincing but malformed\nordinary line")

    assert parsed.status is LyricsTextParseStatus.INVALID
    assert any("malformed timestamp" in item for item in parsed.diagnostics)


def test_malformed_offset_is_controlled() -> None:
    parsed = parse_lyrics_text("[offset:later]\n[00:01.00]Line")

    assert parsed.status is LyricsTextParseStatus.INVALID
    assert any("malformed offset" in item for item in parsed.diagnostics)


def test_offset_outside_durable_integer_range_is_controlled() -> None:
    parsed = parse_lyrics_text(f"[offset:{10**100}]\n[00:01.00]Line")

    assert parsed.status is LyricsTextParseStatus.INVALID
    assert any("supported integer range" in item for item in parsed.diagnostics)


def test_out_of_order_and_duplicate_timestamps_are_preserved_and_diagnosed() -> None:
    parsed = parse_lyrics_text("[00:03.00]Third\n[00:01.00]First\n[00:01.00]Also first")

    assert [(line.start_ms, line.text) for line in parsed.lines] == [
        (1_000, "First"),
        (1_000, "Also first"),
        (3_000, "Third"),
    ]
    assert any("out-of-order" in item for item in parsed.diagnostics)
    assert any("duplicate timestamps" in item for item in parsed.diagnostics)


def test_timestamp_outside_duration_remains_visible_with_diagnostic() -> None:
    parsed = parse_lyrics_text("[03:30.00]Late", duration_ms=180_000)

    assert parsed.status is LyricsTextParseStatus.SYNCED
    assert parsed.lines[0].start_ms == 210_000
    assert any("exceeds track duration" in item for item in parsed.diagnostics)


def test_plain_lyrics_preserve_unicode_empty_and_repeated_lines_without_timing() -> (
    None
):
    parsed = parse_lyrics_text("君の声\n\n君の声")

    assert parsed.status is LyricsTextParseStatus.PLAIN
    assert [line.text for line in parsed.lines] == ["君の声", "", "君の声"]
    assert all(line.start_ms is None for line in parsed.lines)
    assert len({line.line_id for line in parsed.lines}) == 3
    assert parsed.timing_level is LyricTimingLevel.UNSYNCHRONIZED


def test_metadata_only_or_empty_text_is_invalid() -> None:
    assert parse_lyrics_text("[ar:Artist]\n[ti:Title]").status is (
        LyricsTextParseStatus.INVALID
    )
    assert parse_lyrics_text("\n\n").status is LyricsTextParseStatus.INVALID


def test_malformed_known_metadata_is_not_reinterpreted_as_plain_lyrics() -> None:
    parsed = parse_lyrics_text("[ar Artist]\nActual lyric")

    assert parsed.status is LyricsTextParseStatus.PLAIN
    assert tuple(line.text for line in parsed.lines) == ("Actual lyric",)
    assert any("malformed lyrics metadata" in item for item in parsed.diagnostics)


def test_unknown_metadata_and_enhanced_timing_are_structured() -> None:
    parsed = parse_lyrics_text(
        "[foo:bar]\n[00:01.00]<00:01.10>君の <00:01.80>声",
        timing_provenance=TimingProvenance.PROVIDER,
    )

    assert parsed.status is LyricsTextParseStatus.SYNCED
    assert parsed.timing_level is LyricTimingLevel.WORD
    assert parsed.lines[0].text == "君の 声"
    assert parsed.normalized_text.endswith("<00:01.10>君の <00:01.80>声")
    assert [segment.text for segment in parsed.lines[0].timing_segments] == [
        "君の ",
        "声",
    ]
    assert [segment.start_ms for segment in parsed.lines[0].timing_segments] == [
        1_100,
        1_800,
    ]
    assert parsed.lines[0].timing_segments[0].end_ms == 1_800
    assert parsed.lines[0].timing_segments[1].end_ms is None
    assert all(
        segment.unit is LyricTimingUnit.WORD
        and segment.timing_provenance is TimingProvenance.PROVIDER
        for segment in parsed.lines[0].timing_segments
    )
    assert any("unsupported metadata" in item for item in parsed.diagnostics)


def test_enhanced_timing_offset_and_repeated_line_timestamps_are_exact() -> None:
    parsed = parse_lyrics_text(
        "[offset:25]\n[00:01.00][00:03.00]<00:01.10>A <00:01.50>B"
    )

    assert [line.start_ms for line in parsed.lines] == [1_025, 3_025]
    assert [
        [segment.start_ms for segment in line.timing_segments] for line in parsed.lines
    ] == [[1_125, 1_525], [3_125, 3_525]]
    assert (
        len(
            {
                segment.segment_id
                for line in parsed.lines
                for segment in line.timing_segments
            }
        )
        == 4
    )


def test_out_of_order_enhanced_timestamps_are_rejected() -> None:
    parsed = parse_lyrics_text("[00:01.00]<00:01.80>later <00:01.20>earlier")

    assert parsed.status is LyricsTextParseStatus.INVALID
    assert any(
        "enhanced timestamps are out of order" in item for item in parsed.diagnostics
    )


def test_timing_provenance_keeps_estimates_distinct_from_supplied_timing() -> None:
    assert {item.value for item in TimingProvenance} == {
        "provider",
        "imported",
        "user-approved",
        "user-edited",
        "generated",
        "estimated",
    }


def test_oversized_text_is_rejected() -> None:
    parsed = parse_lyrics_text("x" * 2_000_001)

    assert parsed.status is LyricsTextParseStatus.INVALID
    assert any("bounded parser size" in item for item in parsed.diagnostics)
