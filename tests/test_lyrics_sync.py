"""Active-line, calibration, and deadline tests for Stage 6."""

from dataclasses import replace
from datetime import UTC, datetime

from konokashi.application.lyrics_sync import active_lyrics_at, synchronize
from konokashi.domain.lyrics import (
    ApprovalState,
    ContentProvenance,
    LyricDocument,
    LyricDocumentKind,
    LyricLine,
    LyricRepresentation,
    RepresentationKind,
)
from konokashi.domain.synchronization import (
    AudioOutputLatency,
    ClockQuality,
    LineTimingCalibration,
    LyricTimingCalibration,
    PlaybackClockDiagnostics,
    PlaybackPositionEstimate,
    PlaybackState,
    PresentationLatency,
    SynchronizationCalibration,
)


def document() -> LyricDocument:
    lines = (
        LyricLine("one", "first", 1_000),
        LyricLine("two-a", "second A", 2_000),
        LyricLine("two-b", "second B", 2_000),
        LyricLine("three", "third", 3_000),
    )
    original = LyricRepresentation(
        "original",
        RepresentationKind.ORIGINAL,
        ContentProvenance.PROVIDER,
        ApprovalState.UNREVIEWED,
        lines,
    )
    return LyricDocument(
        "document",
        LyricDocumentKind.SYNCED,
        "fixture",
        None,
        None,
        ApprovalState.UNREVIEWED,
        datetime(2026, 8, 22, tzinfo=UTC),
        (original,),
    )


def estimate(position_us: int = 2_150_000) -> PlaybackPositionEstimate:
    diagnostics = PlaybackClockDiagnostics(
        ClockQuality.STABLE,
        8,
        10_000,
        1_000,
        2_000,
        3_000,
        0,
        4_000,
        100.0,
        True,
        ("player sampling semantics",),
    )
    return PlaybackPositionEstimate(
        "track",
        position_us,
        PlaybackState.PLAYING,
        1.0,
        1.0,
        1.0,
        0,
        0,
        10_000_000_000,
        diagnostics,
    )


def test_active_lookup_handles_leading_gap_and_simultaneous_lines() -> None:
    before = active_lyrics_at(document(), 999_999)
    simultaneous = active_lyrics_at(document(), 2_000_000)

    assert before.active == ()
    assert tuple(line.line_id for line in before.next) == (
        "one",
        "two-a",
        "two-b",
        "three",
    )
    assert tuple(line.line_id for line in simultaneous.active) == ("two-a", "two-b")
    assert tuple(line.line_id for line in simultaneous.previous) == ("one",)
    assert tuple(line.line_id for line in simultaneous.next) == ("three",)


def test_one_bad_line_can_shift_without_moving_the_document_or_its_peer() -> None:
    calibration = LyricTimingCalibration(
        line_adjustments=(LineTimingCalibration("two-a", 100_000),)
    )

    at_two_seconds = active_lyrics_at(document(), 2_000_000, calibration)
    at_two_point_one = active_lyrics_at(document(), 2_100_000, calibration)

    assert tuple(line.line_id for line in at_two_seconds.active) == ("two-b",)
    assert tuple(line.line_id for line in at_two_seconds.next) == ("two-a", "three")
    assert at_two_seconds.next_start_us == 2_100_000
    assert at_two_seconds.next_line_shifts_us == (
        ("two-a", 100_000),
        ("three", 0),
    )
    assert tuple(line.line_id for line in at_two_point_one.active) == ("two-a",)
    assert tuple(line.line_id for line in at_two_point_one.previous) == (
        "one",
        "two-b",
    )


def test_context_is_bounded_to_eight_whole_timestamp_groups() -> None:
    lines = tuple(
        LyricLine(f"line-{index}", f"line {index}", index * 1_000)
        for index in range(12)
    )
    base = document()
    original = replace(base.representations[0], lines=lines)
    expanded = replace(base, representations=(original,))

    context = active_lyrics_at(expanded, 9_000_000)

    assert tuple(line.line_id for line in context.active) == ("line-9",)
    assert tuple(line.line_id for line in context.previous) == tuple(
        f"line-{index}" for index in range(1, 9)
    )
    assert tuple(line.line_id for line in context.next) == ("line-10", "line-11")


def test_audio_lyric_and_presentation_terms_have_distinct_signs_and_deadline() -> None:
    calibration = SynchronizationCalibration(
        AudioOutputLatency(100_000, 5_000, "manual", route="headphones"),
        LyricTimingCalibration(50_000, 6_000, "manual document shift"),
        PresentationLatency(50_000, 7_000, "test-ui"),
    )

    frame = synchronize(document(), estimate(), calibration)

    assert frame.audible_position_us == 2_050_000
    assert frame.lyrics.timeline_position_us == 2_000_000
    assert tuple(line.line_id for line in frame.lyrics.active) == ("two-a", "two-b")
    assert frame.deadline is not None
    assert frame.deadline.target_media_position_us == 3_150_000
    assert frame.deadline.display_deadline_ns == 10_950_000_000
    assert frame.error_budget.mpris_clock_us == 4_000
    assert frame.error_budget.audio_output_us == 100_000
    assert frame.error_budget.lyric_shift_us == 50_000
    assert frame.error_budget.presentation_us == 50_000
    assert frame.error_budget.combined_uncertainty_us == 22_000


def test_unknown_provider_and_output_error_prevent_false_total_bound() -> None:
    frame = synchronize(document(), estimate(), SynchronizationCalibration())

    assert frame.error_budget.combined_uncertainty_us is None
    assert (
        "actual output path latency is unmeasured" in frame.error_budget.unknown_sources
    )
    assert "provider lyric timestamp error" in frame.error_budget.unknown_sources
    assert "presentation latency uncertainty" in frame.error_budget.unknown_sources


def test_long_deadline_integrates_temporary_slew_then_uses_base_rate() -> None:
    current = replace(
        estimate(),
        effective_rate=1.025,
        base_rate=1.0,
        slew_remaining_us=50_000,
        slew_remaining_duration_us=2_000_000,
    )
    calibration = SynchronizationCalibration(
        lyrics=LyricTimingCalibration(10_000_000, 0, "test")
    )

    frame = synchronize(document(), current, calibration)

    assert frame.deadline is not None
    assert frame.deadline.target_media_position_us == 11_000_000
    assert frame.deadline.display_deadline_ns == 18_800_000_000


def test_paused_clock_has_no_future_deadline() -> None:
    current = estimate()
    paused = PlaybackPositionEstimate(
        current.session_id,
        current.position_us,
        PlaybackState.PAUSED,
        current.reported_rate,
        current.effective_rate,
        current.base_rate,
        current.slew_remaining_us,
        current.slew_remaining_duration_us,
        current.monotonic_ns,
        current.diagnostics,
    )

    assert (
        synchronize(document(), paused, SynchronizationCalibration()).deadline is None
    )


def test_exact_boundaries_one_microsecond_before_and_after_final_line() -> None:
    before = active_lyrics_at(document(), 2_999_999)
    exact = active_lyrics_at(document(), 3_000_000)
    much_later = active_lyrics_at(document(), 90_000_000)

    assert tuple(line.line_id for line in before.active) == ("two-a", "two-b")
    assert tuple(line.line_id for line in exact.active) == ("three",)
    assert tuple(line.line_id for line in much_later.active) == ("three",)
    assert much_later.next == ()


def test_positive_and_negative_document_delay_do_not_change_source_timestamps() -> None:
    original_start = document().representations[0].lines[0].start_ms
    positive = synchronize(
        document(),
        estimate(1_100_000),
        SynchronizationCalibration(lyrics=LyricTimingCalibration(250_000)),
    )
    negative = synchronize(
        document(),
        estimate(950_000),
        SynchronizationCalibration(lyrics=LyricTimingCalibration(-50_000)),
    )

    assert positive.lyrics.active == ()
    assert tuple(line.line_id for line in negative.lyrics.active) == ("one",)
    assert document().representations[0].lines[0].start_ms == original_start


def test_automatic_audio_and_device_residual_compose_with_document_delay() -> None:
    calibration = SynchronizationCalibration(
        audio_output=AudioOutputLatency(
            100_000,
            5_000,
            "PipeWire",
            residual_calibration_us=27_000,
            device_key="device-a",
        ),
        lyrics=LyricTimingCalibration(23_000, 10_000),
    )

    frame = synchronize(document(), estimate(2_150_000), calibration)

    assert frame.audible_position_us == 2_023_000
    assert frame.lyrics.timeline_position_us == 2_000_000
    assert frame.error_budget.audio_automatic_us == 100_000
    assert frame.error_budget.audio_residual_us == 27_000
    assert frame.error_budget.audio_output_us == 127_000


def test_unknown_audio_latency_remains_unknown_not_fake_zero() -> None:
    frame = synchronize(document(), estimate(), SynchronizationCalibration())

    assert frame.audible_position_us is None
    assert frame.error_budget.audio_output_us is None


def test_untimed_and_instrumental_documents_have_no_active_timeline() -> None:
    untimed = replace(document(), kind=LyricDocumentKind.PLAIN)
    untimed_original = replace(
        untimed.representations[0],
        lines=tuple(
            replace(line, start_ms=None) for line in untimed.representations[0].lines
        ),
    )
    untimed = replace(untimed, representations=(untimed_original,))
    instrumental = replace(
        document(), kind=LyricDocumentKind.INSTRUMENTAL, representations=()
    )

    assert active_lyrics_at(untimed, 2_000_000).active == ()
    assert active_lyrics_at(untimed, 2_000_000).next == ()
    assert active_lyrics_at(instrumental, 2_000_000).active == ()


def test_deadline_rate_math_is_integer_and_exact_for_common_rates() -> None:
    expected_deadline_ns = {
        500_000_000: 11_700_000_000,
        1_000_000_000: 10_850_000_000,
        1_500_000_000: 10_566_666_667,
        2_000_000_000: 10_425_000_000,
    }
    for rate_ppb, expected_ns in expected_deadline_ns.items():
        current = replace(
            estimate(),
            reported_rate=rate_ppb / 1_000_000_000,
            effective_rate=rate_ppb / 1_000_000_000,
            base_rate=rate_ppb / 1_000_000_000,
            base_rate_ppb=rate_ppb,
            effective_rate_ppb=rate_ppb,
        )

        deadline = synchronize(
            document(), current, SynchronizationCalibration()
        ).deadline

        assert deadline is not None
        assert deadline.display_deadline_ns == expected_ns


def test_blank_repeated_lines_and_huge_instrumental_gap_keep_distinct_ids() -> None:
    original = LyricRepresentation(
        "edge-original",
        RepresentationKind.ORIGINAL,
        ContentProvenance.PROVIDER,
        ApprovalState.UNREVIEWED,
        (
            LyricLine("repeat-a", "again", 1_000),
            LyricLine("blank", "", 2_000),
            LyricLine("repeat-b", "again", 600_000),
        ),
    )
    edge_document = replace(
        document(), document_id="edge-document", representations=(original,)
    )

    blank = active_lyrics_at(edge_document, 2_000_000)
    during_gap = active_lyrics_at(edge_document, 599_999_999)
    repeated = active_lyrics_at(edge_document, 600_000_000)

    assert tuple(line.line_id for line in blank.active) == ("blank",)
    assert blank.active[0].text == ""
    assert tuple(line.line_id for line in during_gap.active) == ("blank",)
    assert during_gap.next_start_us == 600_000_000
    assert tuple(line.line_id for line in repeated.active) == ("repeat-b",)
    assert repeated.active[0].text == "again"
