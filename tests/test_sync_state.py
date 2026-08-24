"""Presentation-neutral synchronization snapshot and subscription tests."""

from dataclasses import replace

from lyriflux.application.lyrics_sync import synchronize
from lyriflux.application.sync_state import (
    SourceGenerationGuard,
    SynchronizationPublisher,
    build_sync_snapshot,
)
from lyriflux.domain.identity import YouTubeIdentity
from lyriflux.domain.lyrics import ApprovalState, ContentProvenance, RepresentationKind
from lyriflux.domain.representations import (
    EffectiveRepresentationLine,
    RepresentationUncertainty,
)
from lyriflux.domain.synchronization import (
    AudioOutputLatency,
    LyricTimingCalibration,
    SynchronizationCalibration,
)
from lyriflux.domain.tracks import Confidence, ResolvedTrack, TrackCandidate
from tests.stage2_helpers import fixture_snapshot
from tests.test_lyrics_sync import document, estimate


def test_snapshot_exposes_all_frontend_timing_and_aligned_text_layers() -> None:
    lyric_document = document()
    original = lyric_document.representations[0].lines[1]
    representation = EffectiveRepresentationLine(
        original,
        RepresentationKind.ROMANIZED,
        "second romaji",
        ContentProvenance.USER,
        ApprovalState.APPROVED,
        "user",
        None,
        RepresentationUncertainty.NONE,
        inherited_start_ms=original.start_ms,
    )
    raw = replace(fixture_snapshot("stage2/youtube_jesskah.json"), rate=1.0)
    track = ResolvedTrack(
        raw,
        YouTubeIdentity("xa4WrgqI7q0"),
        TrackCandidate("Track", ("Artist",), None, 5_000_000),
        Confidence.HIGH,
    )
    calibration = SynchronizationCalibration(
        AudioOutputLatency(100_000, 5_000, "test", route="headphones"),
        LyricTimingCalibration(50_000, 10_000),
    )
    current_estimate = estimate()
    frame = synchronize(lyric_document, current_estimate, calibration)

    snapshot = build_sync_snapshot(
        generation=7,
        track=track,
        document=lyric_document,
        estimate=current_estimate,
        frame=frame,
        calibration=calibration,
        representations=(representation,),
        lyrics_match_confidence="High",
    )

    assert snapshot.generation == 7
    assert snapshot.source_identity == track.source_identity
    assert snapshot.player_service == raw.service_name
    assert snapshot.reported_mpris_position_us == raw.position_us
    assert snapshot.disciplined_player_position_us == 2_150_000
    assert snapshot.progress_fraction == 0.43
    assert snapshot.estimated_audible_position_us == 2_050_000
    assert snapshot.clock is current_estimate.diagnostics
    assert snapshot.audio_output.route == "headphones"
    assert snapshot.lyrics_display_delay_us == 50_000
    assert snapshot.active[0].line_id == "two-a"
    assert snapshot.active[0].original_index == 1
    assert snapshot.active[0].romanized_or_transliterated == "second romaji"
    assert snapshot.active[0].source_timestamp_us == 2_000_000
    assert snapshot.active[0].effective_transition_us == 2_050_000
    assert snapshot.next_transition_monotonic_ns is not None
    assert snapshot.time_until_next_transition_us is not None


def test_snapshot_retains_explained_missing_representation_diagnostics() -> None:
    lyric_document = document()
    original = lyric_document.representations[0].lines[1]
    missing = EffectiveRepresentationLine(
        original,
        RepresentationKind.ROMANIZED,
        None,
        ContentProvenance.GENERATED,
        ApprovalState.UNREVIEWED,
        "LyriFlux language routing",
        "1",
        RepresentationUncertainty.AMBIGUOUS,
        ("Han-only document lacks sufficient language evidence",),
        inherited_start_ms=original.start_ms,
    )
    raw = replace(fixture_snapshot("stage2/youtube_jesskah.json"), rate=1.0)
    track = ResolvedTrack(
        raw,
        YouTubeIdentity("xa4WrgqI7q0"),
        TrackCandidate("Track", ("Artist",), None, 5_000_000),
        Confidence.HIGH,
    )
    calibration = SynchronizationCalibration(AudioOutputLatency(0, 0, "test"))
    current_estimate = estimate()

    snapshot = build_sync_snapshot(
        generation=1,
        track=track,
        document=lyric_document,
        estimate=current_estimate,
        frame=synchronize(lyric_document, current_estimate, calibration),
        calibration=calibration,
        representations=(missing,),
    )

    assert (
        "Han-only document lacks sufficient language evidence" in snapshot.diagnostics
    )


def test_snapshot_uses_transliteration_when_romanized_placeholder_has_no_text() -> None:
    lyric_document = document()
    original = lyric_document.representations[0].lines[1]
    missing_romanized = EffectiveRepresentationLine(
        original,
        RepresentationKind.ROMANIZED,
        None,
        None,
        None,
        None,
        None,
        None,
        inherited_start_ms=original.start_ms,
    )
    cyrillic = EffectiveRepresentationLine(
        original,
        RepresentationKind.TRANSLITERATED,
        "Ona budto muzyka tišiny",
        ContentProvenance.GENERATED,
        ApprovalState.UNREVIEWED,
        "Unicode ICU",
        "test",
        RepresentationUncertainty.NONE,
        inherited_start_ms=original.start_ms,
    )
    raw = replace(fixture_snapshot("stage2/youtube_jesskah.json"), rate=1.0)
    track = ResolvedTrack(
        raw,
        YouTubeIdentity("xa4WrgqI7q0"),
        TrackCandidate("Track", ("Artist",), None, 5_000_000),
        Confidence.HIGH,
    )
    calibration = SynchronizationCalibration(AudioOutputLatency(0, 0, "test"))
    current_estimate = estimate()

    snapshot = build_sync_snapshot(
        generation=1,
        track=track,
        document=lyric_document,
        estimate=current_estimate,
        frame=synchronize(lyric_document, current_estimate, calibration),
        calibration=calibration,
        representations=(missing_romanized, cyrillic),
    )

    assert snapshot.active[0].romanized_or_transliterated == cyrillic.text
    assert snapshot.active[0].representation_provenance == ("generated",)


def test_publisher_suppresses_identical_state_and_unsubscribes_cleanly() -> None:
    lyric_document = document()
    raw = replace(fixture_snapshot("stage2/youtube_jesskah.json"), rate=1.0)
    track = ResolvedTrack(
        raw,
        YouTubeIdentity("xa4WrgqI7q0"),
        TrackCandidate("Track", ("Artist",), None, 5_000_000),
        Confidence.HIGH,
    )
    calibration = SynchronizationCalibration(AudioOutputLatency(0, 0, "test"))
    current_estimate = estimate()
    frame = synchronize(lyric_document, current_estimate, calibration)
    snapshot = build_sync_snapshot(
        generation=1,
        track=track,
        document=lyric_document,
        estimate=current_estimate,
        frame=frame,
        calibration=calibration,
    )
    received = []
    publisher = SynchronizationPublisher()
    subscription = publisher.subscribe(received.append)

    assert publisher.publish(snapshot) is True
    assert publisher.publish(snapshot) is False
    assert received == [snapshot]
    assert publisher.subscriber_count == 1

    locally_interpolated = replace(
        snapshot,
        disciplined_player_position_us=snapshot.disciplined_player_position_us + 10,
        time_until_next_transition_us=snapshot.time_until_next_transition_us - 10,  # type: ignore[operator]
    )
    assert publisher.publish(locally_interpolated) is False
    assert publisher.current is locally_interpolated
    assert received == [snapshot]

    active_transition = replace(
        locally_interpolated,
        previous=locally_interpolated.active,
        active=locally_interpolated.next,
        next=(),
    )
    assert publisher.publish(active_transition) is True
    assert received == [snapshot, active_transition]

    subscription.close()
    subscription.close()
    assert publisher.subscriber_count == 0
    publisher.publish(replace(snapshot, generation=2))
    assert received == [snapshot, active_transition]


def test_generation_guard_rejects_a_after_b_and_rapid_a_b_c_results() -> None:
    guard = SourceGenerationGuard()
    source_a = YouTubeIdentity("xa4WrgqI7q0")
    source_b = YouTubeIdentity("kFqGyp60d8s")
    source_c = YouTubeIdentity("XIMLoLxmTDw")
    token_a = guard.select(source_a)
    token_b = guard.select(source_b)
    token_c = guard.select(source_c)

    assert guard.accepts(token_a) is False
    assert guard.accepts(token_b) is False
    assert guard.accepts(token_c) is True


def test_same_title_different_stable_identity_is_a_new_generation() -> None:
    guard = SourceGenerationGuard()
    first = guard.select(YouTubeIdentity("xa4WrgqI7q0"))
    second = guard.select(YouTubeIdentity("kFqGyp60d8s"))

    assert first.generation != second.generation
    assert guard.accepts(first) is False
    assert guard.accepts(second) is True
    guard.invalidate()
    assert guard.accepts(second) is False
