"""Stable source identity tests without media downloads or file hashing."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyriflux.application.source_identity import (
    SourceIdentityResolver,
    extract_youtube_video_id,
)
from lyriflux.domain.identity import (
    GenericMprisIdentity,
    LocalFileIdentity,
    PersistenceScope,
    YouTubeIdentity,
)
from lyriflux.infrastructure.metadata.local_paths import (
    FilesystemLocalPathCanonicalizer,
)
from tests.stage2_helpers import PredictableLocalPaths, snapshot


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
