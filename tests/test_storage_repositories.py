"""Typed repository round-trips and Stage 2 durable integration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lyricflow.application.resolve_track import TrackResolver
from lyricflow.application.source_identity import SourceIdentityResolver
from lyricflow.domain.identity import (
    GenericMprisIdentity,
    LocalFileIdentity,
    YouTubeIdentity,
)
from lyricflow.domain.lyrics import (
    ApprovalState,
    ContentProvenance,
    LyricDocument,
    LyricDocumentKind,
    LyricLine,
    LyricLineAlignment,
    LyricRepresentation,
    LyricsMatch,
    LyricsMatchDecision,
    ProviderCacheEntry,
    RepresentationKind,
    TimingProvenance,
)
from lyricflow.domain.tracks import (
    ApprovedTrackIdentity,
    Confidence,
    PlayerSelectionConfig,
)
from lyricflow.infrastructure.storage.bootstrap import open_storage
from lyricflow.infrastructure.storage.errors import (
    InvalidStoredDataError,
    StorageValidationError,
)
from tests.stage2_helpers import PredictableLocalPaths, fixture_snapshot

NOW = datetime(2026, 8, 12, 12, 30, tzinfo=UTC)


def test_source_identities_round_trip_structured_and_unique(tmp_path: Path) -> None:
    storage = open_storage(tmp_path / "identities.sqlite3")
    identities = (
        LocalFileIdentity("/music/Unicode 日本語 song.flac"),
        YouTubeIdentity("xa4WrgqI7q0"),
        GenericMprisIdentity("radio.service", "/track/1", "https://radio.example/live"),
    )

    for identity in identities:
        assert storage.source_identities.put(identity) == identity
        assert storage.source_identities.put(identity) == identity
        assert storage.source_identities.get(identity) == identity

    with storage.database.connection(readonly=True) as connection:
        count = connection.execute("SELECT COUNT(*) FROM source_identities").fetchone()[
            0
        ]
    assert count == 3


def test_approved_override_crud_restart_unicode_and_version_marker(
    tmp_path: Path,
) -> None:
    path = tmp_path / "overrides.sqlite3"
    source = YouTubeIdentity("xa4WrgqI7q0")
    first = open_storage(path).track_overrides

    assert first.get(source) is None
    first.put(
        source,
        ApprovedTrackIdentity(
            "Every Single Day (S3RL Remix Radio Edit)",
            ("S3RL feat. JessKah 日本語",),
            "アルバム",
        ),
    )

    restarted = open_storage(path).track_overrides
    assert restarted.get(source) == ApprovedTrackIdentity(
        "Every Single Day (S3RL Remix Radio Edit)",
        ("S3RL feat. JessKah 日本語",),
        "アルバム",
    )
    restarted.put(source, ApprovedTrackIdentity("Replacement", ("Artist",)))
    assert restarted.get(source) == ApprovedTrackIdentity("Replacement", ("Artist",))
    assert restarted.delete(source) is True
    assert restarted.delete(source) is False
    assert restarted.get(source) is None


def test_blank_and_session_only_corrections_are_rejected(tmp_path: Path) -> None:
    repository = open_storage(tmp_path / "policy.sqlite3").track_overrides

    with pytest.raises(StorageValidationError, match="blank"):
        repository.put(
            YouTubeIdentity("xa4WrgqI7q0"), ApprovedTrackIdentity("", ("Artist",))
        )
    with pytest.raises(StorageValidationError, match="session-only"):
        repository.put(
            GenericMprisIdentity("radio", None, "https://radio.example/live"),
            ApprovedTrackIdentity("Song", ("Artist",)),
        )


def test_persisted_override_wins_after_fresh_resolver_objects(tmp_path: Path) -> None:
    path = tmp_path / "durable-resolution.sqlite3"
    raw = fixture_snapshot("stage2/youtube_jesskah.json")
    sources = SourceIdentityResolver(PredictableLocalPaths())
    first_storage = open_storage(path)
    first_resolver = TrackResolver(sources, first_storage.track_overrides)

    automatic = first_resolver.resolve(raw)
    assert automatic.confidence is Confidence.HIGH
    assert automatic.user_approved is False
    first_storage.track_overrides.put(
        automatic.source_identity,
        ApprovedTrackIdentity(
            "Every Single Day (User Version)", ("S3RL feat. JessKah",)
        ),
    )

    second_storage = open_storage(path)
    second_resolver = TrackResolver(
        SourceIdentityResolver(PredictableLocalPaths()),
        second_storage.track_overrides,
    )
    resolved = second_resolver.resolve(raw)

    assert resolved.confidence is Confidence.APPROVED
    assert resolved.user_approved is True
    assert resolved.candidate.title == "Every Single Day (User Version)"
    assert resolved.automatic_candidate == automatic.candidate
    assert resolved.automatic_confidence is automatic.confidence
    assert resolved.raw_snapshot is raw
    assert any("user-approved correction" in item for item in resolved.evidence)


def test_override_for_unrelated_source_never_applies(tmp_path: Path) -> None:
    storage = open_storage(tmp_path / "unrelated.sqlite3")
    storage.track_overrides.put(
        YouTubeIdentity("xa4WrgqI7q0"),
        ApprovedTrackIdentity("Corrected", ("Artist",)),
    )

    assert storage.track_overrides.get(YouTubeIdentity("kFqGyp60d8s")) is None


def test_settings_defaults_save_reload_and_independence(tmp_path: Path) -> None:
    path = tmp_path / "settings.sqlite3"
    settings = open_storage(path).settings
    assert settings.get_player_selection() == PlayerSelectionConfig()

    settings.put_player_selection(
        PlayerSelectionConfig(
            preferred_players=("strawberry", "plasma-browser-integration"),
            ignored_players=("firefox.instance_1",),
        )
    )

    assert open_storage(path).settings.get_player_selection() == PlayerSelectionConfig(
        preferred_players=("strawberry", "plasma-browser-integration"),
        ignored_players=("firefox.instance_1",),
    )
    settings.put_player_selection(
        PlayerSelectionConfig(preferred_players=("strawberry",))
    )
    assert settings.get_player_selection() == PlayerSelectionConfig(
        preferred_players=("strawberry",), ignored_players=()
    )


def test_invalid_stored_settings_are_controlled(tmp_path: Path) -> None:
    storage = open_storage(tmp_path / "invalid-settings.sqlite3")
    storage.settings.put_player_selection(PlayerSelectionConfig())
    with storage.database.transaction() as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute("UPDATE settings SET format_version = 99")

    with pytest.raises(InvalidStoredDataError, match="unsupported"):
        storage.settings.get_player_selection()


def _multilingual_document() -> LyricDocument:
    return LyricDocument(
        document_id="document-1",
        kind=LyricDocumentKind.SYNCED,
        source_name="future-provider",
        original_text="同じ行\n同じ行",
        raw_text_checksum="checksum",
        approval_state=ApprovalState.APPROVED,
        retrieved_at=NOW,
        provider_record_id="provider-id",
        language="ja",
        script="Jpan",
        duration_ms=183_771,
        representations=(
            LyricRepresentation(
                "original",
                RepresentationKind.ORIGINAL,
                ContentProvenance.USER,
                ApprovalState.APPROVED,
                (
                    LyricLine(
                        "original-1",
                        "同じ行",
                        1_001,
                        2_002,
                        TimingProvenance.MANUAL,
                    ),
                    LyricLine(
                        "original-2",
                        "同じ行",
                        2_002,
                        None,
                        TimingProvenance.PROVIDER,
                    ),
                ),
                language="ja",
                script="Jpan",
            ),
            LyricRepresentation(
                "romanized",
                RepresentationKind.ROMANIZED,
                ContentProvenance.GENERATED,
                ApprovalState.UNREVIEWED,
                (
                    LyricLine("romanized-1", "onaji gyou", source_line_id="original-1"),
                    LyricLine("romanized-2", "onaji gyou", source_line_id="original-2"),
                ),
                language="ja-Latn",
                script="Latn",
                generator_name="future-engine",
                generator_version="1",
            ),
            LyricRepresentation(
                "translation",
                RepresentationKind.TRANSLATED,
                ContentProvenance.PROVIDER,
                ApprovalState.UNREVIEWED,
                (LyricLine("translation-1", "same line", source_line_id="original-1"),),
                language="en",
                script="Latn",
            ),
        ),
        alignments=(
            LyricLineAlignment("original-1", "romanized-1"),
            LyricLineAlignment("original-2", "romanized-2"),
            LyricLineAlignment("original-1", "translation-1"),
        ),
    )


def test_lyrics_document_round_trip_preserves_all_foundation_semantics(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lyrics.sqlite3"
    document = _multilingual_document()
    repository = open_storage(path).lyrics

    repository.put(document)

    restored = open_storage(path).lyrics.get(document.document_id)
    assert restored == document
    assert restored is not None
    original_lines = restored.representations[0].lines
    assert original_lines[0].text == original_lines[1].text
    assert original_lines[0].line_id != original_lines[1].line_id
    assert original_lines[0].start_ms == 1_001
    assert original_lines[0].timing_provenance is TimingProvenance.MANUAL


def test_untimed_document_and_explicit_delete_round_trip(tmp_path: Path) -> None:
    repository = open_storage(tmp_path / "plain.sqlite3").lyrics
    document = LyricDocument(
        "plain-1",
        LyricDocumentKind.PLAIN,
        "local",
        "one\ntwo",
        None,
        ApprovalState.UNREVIEWED,
        NOW,
        representations=(
            LyricRepresentation(
                "original",
                RepresentationKind.ORIGINAL,
                ContentProvenance.LOCAL,
                ApprovalState.UNREVIEWED,
                (LyricLine("line-1", "one"), LyricLine("line-2", "two")),
            ),
        ),
    )

    repository.put(document)
    assert repository.get("plain-1") == document
    assert repository.delete("plain-1") is True
    assert repository.delete("plain-1") is False


def test_lyrics_reject_unknown_inherited_timing_source(tmp_path: Path) -> None:
    repository = open_storage(tmp_path / "invalid-alignment.sqlite3").lyrics
    document = LyricDocument(
        "invalid-source",
        LyricDocumentKind.PLAIN,
        "local",
        "line",
        None,
        ApprovalState.UNREVIEWED,
        NOW,
        representations=(
            LyricRepresentation(
                "translation",
                RepresentationKind.TRANSLATED,
                ContentProvenance.USER,
                ApprovalState.UNREVIEWED,
                (LyricLine("translated-1", "line", source_line_id="missing"),),
            ),
        ),
    )

    with pytest.raises(StorageValidationError, match="unknown source line"):
        repository.put(document)


def test_matches_and_provider_cache_remain_separate(tmp_path: Path) -> None:
    path = tmp_path / "separate.sqlite3"
    storage = open_storage(path)
    document = _multilingual_document()
    source = YouTubeIdentity("xa4WrgqI7q0")
    match = LyricsMatch(
        document.document_id,
        LyricsMatchDecision.APPROVED,
        ContentProvenance.USER,
        NOW,
    )
    cache = ProviderCacheEntry("provider", "query-key", b"raw response", NOW)
    storage.lyrics.put(document)
    storage.lyrics_matches.put(source, match)
    storage.provider_cache.put(cache)

    restarted = open_storage(path)
    assert restarted.lyrics_matches.get(source) == match
    rejected = LyricsMatch(
        document.document_id,
        LyricsMatchDecision.REJECTED,
        ContentProvenance.USER,
        NOW,
    )
    restarted.lyrics_matches.put(source, rejected)
    assert restarted.lyrics_matches.get(source) == rejected
    assert restarted.provider_cache.get("provider", "query-key") == cache
    assert restarted.provider_cache.delete("provider", "query-key") is True
    assert restarted.lyrics_matches.get(source) == rejected
    assert restarted.lyrics_matches.delete(source) is True
