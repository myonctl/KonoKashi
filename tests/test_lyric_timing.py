"""Canonical durable plus invocation-only lyric timing composition."""

from konokashi.application.lyric_timing import effective_lyric_timing
from konokashi.domain.synchronization import LyricDocumentTiming


def test_zero_offset_preserves_durable_delay() -> None:
    timing = LyricDocumentTiming("document", 125_000)

    effective = effective_lyric_timing(timing)

    assert effective.shift_us == 125_000
    assert effective.source == "durable per-document display delay"
    assert timing.lyrics_display_delay_us == 125_000


def test_positive_and_negative_offsets_compose_exactly_once() -> None:
    timing = LyricDocumentTiming("document", 125_000)

    positive = effective_lyric_timing(timing, invocation_offset_us=350_000)
    negative = effective_lyric_timing(timing, invocation_offset_us=-200_000)

    assert positive.shift_us == 475_000
    assert negative.shift_us == -75_000
    assert "temporary invocation offset" in positive.source
    assert "temporary invocation offset" in negative.source


def test_temporary_offset_without_document_timing_stays_ephemeral() -> None:
    effective = effective_lyric_timing(None, invocation_offset_us=350_000)
    restarted = effective_lyric_timing(None)

    assert effective.shift_us == 350_000
    assert effective.source == "temporary invocation offset"
    assert restarted.shift_us == 0
