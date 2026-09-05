"""Stage 8 reusable review, correction, persistence, and reset tests."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lyriflux.application.review_corrections import (
    ReviewCorrectionError,
    ReviewCorrectionService,
)
from lyriflux.domain.identity import (
    GenericMprisIdentity,
    LocalFileIdentity,
    YouTubeIdentity,
)
from lyriflux.domain.lyrics import (
    ContentProvenance,
    LyricsAlternative,
    LyricsAlternativeResult,
    LyricsMatch,
    LyricsMatchConfidence,
    LyricsMatchDecision,
    LyricsProviderCandidate,
    LyricsResolutionResult,
    LyricsResolutionStatus,
)
from lyriflux.domain.models import PlayerCapabilities, PlayerSnapshot, RawTrackMetadata
from lyriflux.domain.synchronization import LyricDocumentTiming
from lyriflux.domain.tracks import Confidence, ResolvedTrack, TrackCandidate
from lyriflux.infrastructure.lyrics.provider_documents import (
    ProviderLyricDocumentBuilder,
)
from lyriflux.infrastructure.storage.bootstrap import open_storage
from lyriflux.infrastructure.storage.errors import StorageError

NOW = datetime(2026, 8, 23, 18, tzinfo=UTC)


def _track(*, generic: bool = False) -> ResolvedTrack:
    snapshot = PlayerSnapshot(
        "plasma-browser-integration",
        "org.mpris.MediaPlayer2.plasma-browser-integration",
        "Firefox",
        "firefox",
        "Playing",
        RawTrackMetadata(
            title='Artist - "Raw Song" (Official Video)',
            artists=("Uploader",),
            album="Album",
            url="https://www.youtube.com/watch?v=xa4WrgqI7q0&list=private",
            duration_us=180_000_000,
        ),
        1_000_000,
        PlayerCapabilities(),
    )
    automatic = TrackCandidate(
        "Raw Song",
        ("Artist",),
        "Album",
        180_000_000,
        ("artist/title parsed from video title",),
        ("removed presentation suffix",),
    )
    return ResolvedTrack(
        snapshot,
        (
            GenericMprisIdentity("service", "/track/1", None)
            if generic
            else YouTubeIdentity("xa4WrgqI7q0")
        ),
        automatic,
        Confidence.HIGH,
        ("stable YouTube video identity", *automatic.evidence),
        ("uploader preserved as raw evidence",),
    )


def _candidate(record_id: str, title: str = "Raw Song") -> LyricsProviderCandidate:
    return LyricsProviderCandidate(
        "LRCLIB",
        record_id,
        title,
        "Artist",
        "Album",
        180_000,
        False,
        "First\nSecond",
        "[00:01.00]First\n[00:02.00]Second",
    )


def _service(path: Path) -> tuple[ReviewCorrectionService, object]:
    storage = open_storage(path)
    return (
        ReviewCorrectionService(
            track_overrides=storage.track_overrides,
            lyrics=storage.lyrics,
            matches=storage.lyrics_matches,
            provider_documents=ProviderLyricDocumentBuilder(),
            timing=storage.timing_calibrations,
            now=lambda: NOW,
        ),
        storage,
    )


def test_snapshot_keeps_raw_automatic_effective_and_provider_evidence_separate(
    tmp_path: Path,
) -> None:
    service, storage = _service(tmp_path / "audit.sqlite3")
    track = _track()
    builder = ProviderLyricDocumentBuilder()
    document, diagnostics = builder.build(_candidate("current"), NOW)
    assert document is not None
    assert diagnostics == ()
    storage.lyrics.put(document)  # type: ignore[attr-defined]
    storage.lyrics_matches.put(  # type: ignore[attr-defined]
        track.source_identity,
        LyricsMatch(
            document.document_id,
            LyricsMatchDecision.CANDIDATE,
            ContentProvenance.PROVIDER,
            NOW,
            LyricsMatchConfidence.HIGH,
            ("normalized title matches",),
        ),
    )
    resolution = LyricsResolutionResult(
        track.source_identity,
        LyricsResolutionStatus.FOUND_TIMED,
        document,
        "LRCLIB",
        LyricsMatchConfidence.HIGH,
        ("normalized title matches",),
    )
    alternative_candidate = _candidate("alternative", "Raw Song (Live)")
    alternative = LyricsAlternative(
        builder.document_id(alternative_candidate),
        alternative_candidate,
        LyricsMatchConfidence.LOW,
        ("recording version markers conflict",),
    )

    snapshot = service.snapshot(
        track,
        resolution,
        LyricsAlternativeResult(track.source_identity, (alternative,)),
    )

    assert snapshot.durable
    assert snapshot.track.raw_title == 'Artist - "Raw Song" (Official Video)'
    assert snapshot.track.raw_artists == ("Uploader",)
    assert snapshot.track.automatic_title == "Raw Song"
    assert snapshot.track.effective_title == "Raw Song"
    assert snapshot.current_document_id == document.document_id
    assert snapshot.current_provider_title == "Raw Song"
    assert snapshot.current_provider_artist == "Artist"
    assert snapshot.current_provider_duration_ms == 180_000
    assert snapshot.current_match_decision is LyricsMatchDecision.CANDIDATE
    assert snapshot.current_match_evidence == ("normalized title matches",)
    assert snapshot.alternatives == (alternative,)


def test_corrections_persist_across_restart_and_reset_independently(
    tmp_path: Path,
) -> None:
    path = tmp_path / "persistent.sqlite3"
    service, storage = _service(path)
    track = _track()
    builder = ProviderLyricDocumentBuilder()
    document, _ = builder.build(_candidate("current"), NOW)
    assert document is not None
    storage.lyrics.put(document)  # type: ignore[attr-defined]
    resolution = LyricsResolutionResult(
        track.source_identity,
        LyricsResolutionStatus.FOUND_TIMED,
        document,
        "LRCLIB",
        LyricsMatchConfidence.HIGH,
        ("duration differs by 0 ms",),
    )

    service.put_track_override(track, title="Correct Title", artists=("Correct",))
    service.approve_current(track, resolution)
    service.set_display_delay(track, resolution, 125_000)

    restarted = open_storage(path)
    override = restarted.track_overrides.get(track.source_identity)
    match = restarted.lyrics_matches.get(track.source_identity)
    assert override is not None and override.title == "Correct Title"
    assert match is not None
    assert match.document_id == document.document_id
    assert match.decision is LyricsMatchDecision.APPROVED
    assert match.confidence is LyricsMatchConfidence.APPROVED
    assert restarted.timing_calibrations.get_document_timing(
        document.document_id
    ) == LyricDocumentTiming(document.document_id, 125_000)
    restored = restarted.lyrics.get(document.document_id)
    assert restored is not None
    assert restored.representations[0].lines[0].start_ms == 1_000

    assert service.reset_track_override(track)
    assert service.reset_match(track)
    assert service.reset_display_delay(track, resolution)
    clean = open_storage(path)
    assert clean.track_overrides.get(track.source_identity) is None
    assert clean.lyrics_matches.get(track.source_identity) is None
    assert (
        clean.timing_calibrations.get_document_timing(
            document.document_id
        ).lyrics_display_delay_us
        == 0
    )
    assert clean.lyrics.get(document.document_id) is not None


def test_reject_and_choose_alternative_change_only_match_decision(
    tmp_path: Path,
) -> None:
    service, storage = _service(tmp_path / "choices.sqlite3")
    track = _track()
    builder = ProviderLyricDocumentBuilder()
    current, _ = builder.build(_candidate("current"), NOW)
    assert current is not None
    storage.lyrics.put(current)  # type: ignore[attr-defined]
    resolution = LyricsResolutionResult(
        track.source_identity,
        LyricsResolutionStatus.FOUND_TIMED,
        current,
        "LRCLIB",
        LyricsMatchConfidence.HIGH,
    )

    service.reject_current(track, resolution)
    rejected = storage.lyrics_matches.get(track.source_identity)  # type: ignore[attr-defined]
    assert rejected is not None
    assert rejected.decision is LyricsMatchDecision.REJECTED
    assert storage.lyrics_matches.rejections(track.source_identity) == (  # type: ignore[attr-defined]
        rejected,
    )
    assert storage.lyrics.get(current.document_id) == current  # type: ignore[attr-defined]

    candidate = _candidate("chosen", "User Selected Version")
    alternative = LyricsAlternative(
        builder.document_id(candidate),
        candidate,
        LyricsMatchConfidence.LOW,
        ("normalized title differs",),
        rejected=True,
    )
    service.choose_alternative(track, alternative)
    chosen = storage.lyrics_matches.get(track.source_identity)  # type: ignore[attr-defined]
    assert chosen is not None
    assert chosen.document_id == alternative.document_id
    assert chosen.decision is LyricsMatchDecision.APPROVED
    rejections = storage.lyrics_matches.rejections(track.source_identity)  # type: ignore[attr-defined]
    assert tuple(item.document_id for item in rejections) == (current.document_id,)
    assert storage.lyrics.get(alternative.document_id) is not None  # type: ignore[attr-defined]

    assert service.reset_match(track)
    assert storage.lyrics_matches.get(track.source_identity) is None  # type: ignore[attr-defined]
    assert storage.lyrics_matches.rejections(track.source_identity) == ()  # type: ignore[attr-defined]


def test_reject_rolls_back_history_when_current_decision_cannot_be_saved(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reject-atomic.sqlite3"
    service, storage = _service(path)
    track = _track()
    builder = ProviderLyricDocumentBuilder()
    document, _ = builder.build(_candidate("current"), NOW)
    assert document is not None
    storage.lyrics.put(document)  # type: ignore[attr-defined]
    original = LyricsMatch(
        document.document_id,
        LyricsMatchDecision.CANDIDATE,
        ContentProvenance.PROVIDER,
        NOW,
        LyricsMatchConfidence.HIGH,
    )
    storage.lyrics_matches.put(track.source_identity, original)  # type: ignore[attr-defined]
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_write_failure
            BEFORE UPDATE OF decision ON lyrics_matches
            WHEN NEW.decision = 'rejected'
            BEGIN
                SELECT RAISE(ABORT, 'simulated rejected-current failure');
            END
            """
        )
    resolution = LyricsResolutionResult(
        track.source_identity,
        LyricsResolutionStatus.FOUND_TIMED,
        document,
        "LRCLIB",
        LyricsMatchConfidence.HIGH,
    )

    with pytest.raises(StorageError, match="simulated rejected-current failure"):
        service.reject_current(track, resolution)

    assert storage.lyrics_matches.get(track.source_identity) == original  # type: ignore[attr-defined]
    assert storage.lyrics_matches.rejections(track.source_identity) == ()  # type: ignore[attr-defined]


def test_approve_rolls_back_current_when_rejection_cannot_be_cleared(
    tmp_path: Path,
) -> None:
    path = tmp_path / "approve-atomic.sqlite3"
    service, storage = _service(path)
    track = _track()
    builder = ProviderLyricDocumentBuilder()
    document, _ = builder.build(_candidate("current"), NOW)
    assert document is not None
    storage.lyrics.put(document)  # type: ignore[attr-defined]
    resolution = LyricsResolutionResult(
        track.source_identity,
        LyricsResolutionStatus.FOUND_TIMED,
        document,
        "LRCLIB",
        LyricsMatchConfidence.HIGH,
    )
    service.reject_current(track, resolution)
    rejected = storage.lyrics_matches.get(track.source_identity)  # type: ignore[attr-defined]
    assert rejected is not None
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TRIGGER approve_delete_failure
            BEFORE DELETE ON lyrics_match_rejections
            BEGIN
                SELECT RAISE(ABORT, 'simulated rejection-delete failure');
            END
            """
        )

    with pytest.raises(StorageError, match="simulated rejection-delete failure"):
        service.approve_current(track, resolution)

    assert storage.lyrics_matches.get(track.source_identity) == rejected  # type: ignore[attr-defined]
    assert storage.lyrics_matches.rejections(track.source_identity) == (  # type: ignore[attr-defined]
        rejected,
    )


def test_reset_rolls_back_current_when_rejections_cannot_be_cleared(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reset-atomic.sqlite3"
    service, storage = _service(path)
    track = _track()
    builder = ProviderLyricDocumentBuilder()
    document, _ = builder.build(_candidate("current"), NOW)
    assert document is not None
    storage.lyrics.put(document)  # type: ignore[attr-defined]
    resolution = LyricsResolutionResult(
        track.source_identity,
        LyricsResolutionStatus.FOUND_TIMED,
        document,
        "LRCLIB",
        LyricsMatchConfidence.HIGH,
    )
    service.reject_current(track, resolution)
    rejected = storage.lyrics_matches.get(track.source_identity)  # type: ignore[attr-defined]
    assert rejected is not None
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reset_delete_failure
            BEFORE DELETE ON lyrics_match_rejections
            BEGIN
                SELECT RAISE(ABORT, 'simulated reset failure');
            END
            """
        )

    with pytest.raises(StorageError, match="simulated reset failure"):
        service.reset_match(track)

    assert storage.lyrics_matches.get(track.source_identity) == rejected  # type: ignore[attr-defined]
    assert storage.lyrics_matches.rejections(track.source_identity) == (  # type: ignore[attr-defined]
        rejected,
    )


def test_session_only_and_stale_source_corrections_are_rejected(tmp_path: Path) -> None:
    service, _storage = _service(tmp_path / "guards.sqlite3")
    generic = _track(generic=True)

    with pytest.raises(ReviewCorrectionError, match="session-only"):
        service.put_track_override(generic, title="Title", artists=("Artist",))
    with pytest.raises(ReviewCorrectionError, match="session-only"):
        service.set_display_delay(
            generic,
            LyricsResolutionResult(
                generic.source_identity, LyricsResolutionStatus.NO_RESULT
            ),
            125_000,
        )

    stable = _track()
    stale_resolution = LyricsResolutionResult(
        YouTubeIdentity("XIMLoLxmTDw"), LyricsResolutionStatus.NO_RESULT
    )
    with pytest.raises(ReviewCorrectionError, match="another source"):
        service.snapshot(
            stable,
            stale_resolution,
            LyricsAlternativeResult(stable.source_identity),
        )
    with pytest.raises(ReviewCorrectionError, match="another source"):
        service.set_display_delay(stable, stale_resolution, 125_000)


def test_blank_track_correction_is_controlled(tmp_path: Path) -> None:
    service, _storage = _service(tmp_path / "blank.sqlite3")
    with pytest.raises(ReviewCorrectionError, match="non-blank"):
        service.put_track_override(_track(), title=" ", artists=("Artist",))


def test_snapshot_marks_existing_override(tmp_path: Path) -> None:
    service, _storage = _service(tmp_path / "override.sqlite3")
    track = _track()
    service.put_track_override(track, title="Edited", artists=("Artist",))
    approved_track = replace(track, user_approved=True, confidence=Confidence.APPROVED)
    snapshot = service.snapshot(
        approved_track,
        LyricsResolutionResult(track.source_identity, LyricsResolutionStatus.NO_RESULT),
        LyricsAlternativeResult(track.source_identity),
    )
    assert snapshot.has_track_override


def test_rejected_match_without_visible_document_exposes_reset_only(
    tmp_path: Path,
) -> None:
    service, storage = _service(tmp_path / "rejected-reset.sqlite3")
    track = _track()
    builder = ProviderLyricDocumentBuilder()
    document, _ = builder.build(_candidate("rejected"), NOW)
    assert document is not None
    storage.lyrics.put(document)  # type: ignore[attr-defined]
    rejected = LyricsMatch(
        document.document_id,
        LyricsMatchDecision.REJECTED,
        ContentProvenance.USER,
        NOW,
        LyricsMatchConfidence.HIGH,
        ("explicitly rejected by the user",),
    )
    storage.lyrics_matches.put(track.source_identity, rejected)  # type: ignore[attr-defined]
    storage.lyrics_matches.put_rejection(track.source_identity, rejected)  # type: ignore[attr-defined]

    snapshot = service.snapshot(
        track,
        LyricsResolutionResult(track.source_identity, LyricsResolutionStatus.NO_RESULT),
        LyricsAlternativeResult(track.source_identity),
    )

    assert snapshot.current_document_id is None
    assert snapshot.current_match_decision is LyricsMatchDecision.REJECTED
    assert snapshot.current_match_evidence == ("explicitly rejected by the user",)


def test_every_correction_leaves_local_audio_bytes_unchanged(tmp_path: Path) -> None:
    audio_path = tmp_path / "recording.flac"
    original_audio = b"fLaC\x00sanitized-test-audio-payload"
    audio_path.write_bytes(original_audio)
    track = replace(_track(), source_identity=LocalFileIdentity(str(audio_path)))
    service, storage = _service(tmp_path / "no-tag-writes.sqlite3")
    builder = ProviderLyricDocumentBuilder()
    document, _ = builder.build(_candidate("current"), NOW)
    assert document is not None
    storage.lyrics.put(document)  # type: ignore[attr-defined]
    resolution = LyricsResolutionResult(
        track.source_identity,
        LyricsResolutionStatus.FOUND_TIMED,
        document,
        "LRCLIB",
        LyricsMatchConfidence.HIGH,
    )
    alternative_candidate = _candidate("alternative", "Alternate recording")
    alternative = LyricsAlternative(
        builder.document_id(alternative_candidate),
        alternative_candidate,
        LyricsMatchConfidence.LOW,
    )

    service.put_track_override(track, title="Corrected", artists=("Artist",))
    service.approve_current(track, resolution)
    service.set_display_delay(track, resolution, 125_000)
    service.reject_current(track, resolution)
    service.choose_alternative(track, alternative)
    service.reset_track_override(track)
    service.reset_match(track)
    service.reset_display_delay(track, resolution)

    assert audio_path.read_bytes() == original_audio
