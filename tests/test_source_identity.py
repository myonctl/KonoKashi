"""Stable source identity tests without media downloads or file hashing."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from konokashi.application.source_identity import (
    SourceIdentityResolver,
    extract_youtube_video_id,
    same_playback_recording,
)
from konokashi.domain.identity import (
    GenericMprisIdentity,
    LocalFileIdentity,
    PersistenceScope,
    YouTubeIdentity,
)
from konokashi.domain.tracks import ResolvedTrack
from konokashi.infrastructure.metadata.local_paths import (
    FilesystemLocalPathCanonicalizer,
)
from tests.stage2_helpers import PredictableLocalPaths, snapshot
from tests.stage2_helpers import resolver as track_resolver


@pytest.mark.parametrize(
    ("url", "expected"),
    (
        ("https://www.youtube.com/watch?v=xa4WrgqI7q0", "xa4WrgqI7q0"),
        (
            "https://www.youtube.com/watch?v=xa4WrgqI7q0&list=PL123&index=8",
            "xa4WrgqI7q0",
        ),
        ("https://youtu.be/xa4WrgqI7q0?t=42", "xa4WrgqI7q0"),
        ("https://music.youtube.com/watch?v=xa4WrgqI7q0&utm_source=x", "xa4WrgqI7q0"),
        ("https://www.youtube.com/shorts/xa4WrgqI7q0?feature=share", "xa4WrgqI7q0"),
        ("https://www.youtube-nocookie.com/embed/xa4WrgqI7q0", "xa4WrgqI7q0"),
        ("https://www.youtube.com/watch?v=short", None),
        ("https://example.com/watch?v=xa4WrgqI7q0", None),
    ),
)
def test_extract_youtube_video_id_ignores_playlist_and_tracking(
    url: str, expected: str | None
) -> None:
    assert extract_youtube_video_id(url) == expected


def test_local_file_identity_decodes_spaces_unicode_and_percent_encoding() -> None:
    paths = PredictableLocalPaths(exists=False)
    resolver = SourceIdentityResolver(paths)
    raw = snapshot(
        "strawberry",
        url="file:///music/S3RL%20%26%20%E3%83%A6%E3%83%8B%E3%82%B3%E3%83%BC%E3%83%89.flac",
    )

    result = resolver.resolve(raw)

    assert result.identity == LocalFileIdentity("/music/S3RL & ユニコード.flac")
    assert paths.seen == ["/music/S3RL & ユニコード.flac"]
    assert any("missing or inaccessible" in warning for warning in result.warnings)
    assert any("rename or move" in warning for warning in result.warnings)


def test_existing_symlink_is_canonicalized_without_hashing(
    tmp_path: Path,
) -> None:
    target = tmp_path / "real song.flac"
    target.touch()
    link = tmp_path / "linked song.flac"
    link.symlink_to(target)

    result = FilesystemLocalPathCanonicalizer().canonicalize(str(link))

    assert result.canonical_path == str(target)
    assert result.exists is True
    assert result.symlink_resolved is True


def test_file_url_with_null_byte_falls_back_without_touching_filesystem() -> None:
    paths = PredictableLocalPaths()
    result = SourceIdentityResolver(paths).resolve(
        snapshot("hostile", url="file://localhost/%00music/song.flac")
    )

    assert isinstance(result.identity, GenericMprisIdentity)
    assert result.identity.persistence_scope is PersistenceScope.SESSION_ONLY
    assert result.identity.media_url is None
    assert paths.seen == []
    assert "null byte" in result.warnings[0]


def test_invalid_youtube_url_uses_limited_generic_identity() -> None:
    result = SourceIdentityResolver(PredictableLocalPaths()).resolve(
        snapshot("browser", url="https://youtu.be/not-valid")
    )

    assert isinstance(result.identity, GenericMprisIdentity)
    assert result.identity.persistence_scope is PersistenceScope.SESSION_ONLY
    assert "valid 11-character" in result.warnings[0]


def test_generic_stream_fallback_is_explicitly_session_only() -> None:
    result = SourceIdentityResolver(PredictableLocalPaths()).resolve(
        snapshot("radio", url="https://radio.example/live#now")
    )

    assert result.identity == GenericMprisIdentity(
        "radio", None, "https://radio.example/live"
    )
    assert result.identity.persistence_scope is PersistenceScope.SESSION_ONLY


def test_valid_youtube_source_is_permanent_video_identity() -> None:
    result = SourceIdentityResolver(PredictableLocalPaths()).resolve(
        snapshot(
            "browser",
            url="https://www.youtube.com/watch?v=xa4WrgqI7q0&list=ignored",
        )
    )

    assert result.identity == YouTubeIdentity("xa4WrgqI7q0")
    assert result.identity.persistence_scope is PersistenceScope.PERMANENT


def _generic_track(
    service: str = "radio",
    *,
    title: str = "Moonlit Circuit",
    artists: tuple[str, ...] = ("Aoi Test Artist",),
    duration_us: int = 180_000_000,
) -> ResolvedTrack:
    raw = snapshot(
        service,
        title=title,
        artists=artists,
        url="https://radio.example/current",
        duration_us=duration_us,
    )
    raw = replace(
        raw,
        metadata=replace(raw.metadata, track_id="/reused/track/1"),
    )
    resolver, _repository = track_resolver()
    return resolver.resolve(raw)


def test_same_generic_track_id_and_semantic_metadata_is_same_recording() -> None:
    first = _generic_track()
    second = _generic_track(title="  Moonlit   Circuit ")

    assert first.source_identity == second.source_identity
    assert same_playback_recording(first, second)


def test_reused_generic_track_id_with_changed_title_or_artist_is_new_recording() -> (
    None
):
    first = _generic_track()
    changed_title = _generic_track(title="Neon Shore")
    changed_artist = _generic_track(artists=("Another Artist",))

    assert first.source_identity == changed_title.source_identity
    assert not same_playback_recording(first, changed_title)
    assert not same_playback_recording(first, changed_artist)


def test_non_identity_metadata_update_does_not_change_recording() -> None:
    first = _generic_track()
    updated = _generic_track(duration_us=181_000_000)
    updated = replace(
        updated,
        raw_snapshot=replace(
            updated.raw_snapshot,
            playback_status="Paused",
            position_us=42_000_000,
            metadata=replace(
                updated.raw_snapshot.metadata,
                album="Newly reported album",
                art_url="https://images.example/cover.jpg",
            ),
        ),
    )

    assert same_playback_recording(first, updated)


def test_player_change_never_reuses_playback_recording_ownership() -> None:
    assert not same_playback_recording(
        _generic_track("radio"),
        _generic_track("other-player"),
    )
