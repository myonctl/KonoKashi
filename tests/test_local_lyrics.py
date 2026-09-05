"""Exact-path sidecar and read-only embedded lyrics regressions."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mutagen import MutagenError
from mutagen.flac import VCFLACDict

from lyriflux.domain.identity import LocalFileIdentity, YouTubeIdentity
from lyriflux.domain.lyrics import LocalLyricsStatus, LyricDocumentKind
from lyriflux.domain.models import PlayerCapabilities, PlayerSnapshot, RawTrackMetadata
from lyriflux.domain.tracks import Confidence, ResolvedTrack, TrackCandidate
from lyriflux.infrastructure.lyrics.embedded import EmbeddedLyricsProvider
from lyriflux.infrastructure.lyrics.local_sidecar import LocalSidecarLyricsProvider

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


def _track(path: Path, *, duration_us: int = 183_771_000) -> ResolvedTrack:
    snapshot = PlayerSnapshot(
        "strawberry",
        "org.mpris.MediaPlayer2.strawberry",
        "Strawberry",
        "strawberry",
        "Playing",
        RawTrackMetadata(
            title="Elevate (Radio Edit)",
            artists=("Little Sis Nora & S3RL",),
            album="Elevate",
            url=path.as_uri(),
            duration_us=duration_us,
        ),
        1_000_000,
        PlayerCapabilities(),
    )
    return ResolvedTrack(
        snapshot,
        LocalFileIdentity(str(path)),
        TrackCandidate(
            "Elevate (Radio Edit)",
            ("Little Sis Nora & S3RL",),
            "Elevate",
            duration_us,
        ),
        Confidence.HIGH,
    )


def test_unicode_space_sidecar_is_derived_from_exact_media_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "音楽 library" / "Elevate (Radio Edit).flac"
    media.parent.mkdir()
    media.write_bytes(b"audio-placeholder")
    sidecar = media.with_suffix(".lrc")
    sidecar.write_text("\ufeff[00:01.12]君の声\r\n[00:02.345]Second", encoding="utf-8")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    result = LocalSidecarLyricsProvider(lambda: NOW).load(_track(media))

    assert result.status is LocalLyricsStatus.FOUND
    assert result.document is not None
    assert result.document.kind is LyricDocumentKind.SYNCED
    assert result.document.source_name == "local-sidecar"
    assert [line.start_ms for line in result.document.representations[0].lines] == [
        1_120,
        2_345,
    ]
    assert all(
        line.timing_provenance is None
        for line in result.document.representations[0].lines
    )
    assert str(media) not in result.document.document_id
    assert sidecar.read_text(encoding="utf-8").startswith("\ufeff")
    assert media.read_bytes() == b"audio-placeholder"


def test_missing_and_inaccessible_sidecars_are_controlled_misses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "song.flac"
    provider = LocalSidecarLyricsProvider(lambda: NOW)

    assert provider.load(_track(media)).status is LocalLyricsStatus.MISS
    media.with_suffix(".lrc").write_text("plain", encoding="utf-8")

    def inaccessible(_path: Path, _flags: int) -> int:
        raise PermissionError("denied")

    monkeypatch.setattr(os, "open", inaccessible)
    result = provider.load(_track(media))
    assert result.status is LocalLyricsStatus.MISS
    assert any("inaccessible" in item for item in result.diagnostics)


def test_non_regular_sidecar_is_rejected_without_blocking(tmp_path: Path) -> None:
    media = tmp_path / "song.flac"
    sidecar = media.with_suffix(".lrc")
    os.mkfifo(sidecar)

    result = LocalSidecarLyricsProvider(lambda: NOW).load(_track(media))

    assert result.status is LocalLyricsStatus.INVALID
    assert any("regular file" in item for item in result.diagnostics)


def test_invalid_encoding_and_malformed_sidecar_are_controlled(
    tmp_path: Path,
) -> None:
    media = tmp_path / "song.flac"
    sidecar = media.with_suffix(".lrc")
    sidecar.write_bytes(b"\xff\xfe")
    provider = LocalSidecarLyricsProvider(lambda: NOW)

    invalid_encoding = provider.load(_track(media))
    assert invalid_encoding.status is LocalLyricsStatus.INVALID
    assert any("valid UTF-8" in item for item in invalid_encoding.diagnostics)

    sidecar.write_text("[00:bad]Not safe", encoding="utf-8")
    malformed = provider.load(_track(media))
    assert malformed.status is LocalLyricsStatus.INVALID
    assert any("malformed timestamp" in item for item in malformed.diagnostics)


class _FakeAudio:
    def __init__(self, tags: object) -> None:
        self.tags = tags
        self.saved = False

    def save(self) -> None:
        self.saved = True


@pytest.mark.parametrize(
    ("field", "text", "kind"),
    [
        ("LYRICS", "one\ntwo", LyricDocumentKind.PLAIN),
        ("SYNCEDLYRICS", "[00:01.00]one", LyricDocumentKind.SYNCED),
        ("UNSYNCEDLYRICS", "君の声", LyricDocumentKind.PLAIN),
    ],
)
def test_supported_vorbis_fields_are_read_only_and_original(
    tmp_path: Path, field: str, text: str, kind: LyricDocumentKind
) -> None:
    media = tmp_path / "song.flac"
    audio = _FakeAudio({field: [text]})
    seen: list[Path] = []

    def loader(path: Path) -> object:
        seen.append(path)
        return audio

    result = EmbeddedLyricsProvider(loader, lambda: NOW).load(_track(media))

    assert result.status is LocalLyricsStatus.FOUND
    assert result.document is not None
    assert result.document.kind is kind
    assert result.document.original_text == text
    assert result.document.representations[0].lines[0].text in text
    assert seen == [media]
    assert audio.saved is False


def test_actual_mutagen_flac_vorbis_tag_container_is_supported(tmp_path: Path) -> None:
    media = tmp_path / "actual-tags.flac"
    tags = VCFLACDict()
    tags["LYRICS"] = ["君の声\nSecond"]
    audio = _FakeAudio(tags)

    result = EmbeddedLyricsProvider(lambda _path: audio, lambda: NOW).load(
        _track(media)
    )

    assert result.status is LocalLyricsStatus.FOUND
    assert result.document is not None
    assert result.document.original_text == "君の声\nSecond"
    assert audio.saved is False


def test_missing_malformed_and_loader_failure_embedded_values_are_controlled(
    tmp_path: Path,
) -> None:
    track = _track(tmp_path / "song.flac")

    assert (
        EmbeddedLyricsProvider(lambda _path: _FakeAudio({})).load(track).status
        is LocalLyricsStatus.MISS
    )
    malformed = EmbeddedLyricsProvider(lambda _path: _FakeAudio({"lyrics": 123})).load(
        track
    )
    assert malformed.status is LocalLyricsStatus.INVALID

    def broken(_path: Path) -> object:
        raise OSError("unreadable")

    failed = EmbeddedLyricsProvider(broken).load(track)
    assert failed.status is LocalLyricsStatus.MISS
    assert any("could not be read" in item for item in failed.diagnostics)

    def corrupt_audio(_path: Path) -> object:
        raise MutagenError("bad audio")

    mutagen_failure = EmbeddedLyricsProvider(corrupt_audio).load(track)
    assert mutagen_failure.status is LocalLyricsStatus.MISS
    assert any("bad audio" in item for item in mutagen_failure.diagnostics)


def test_non_local_track_is_normal_miss(tmp_path: Path) -> None:
    track = _track(tmp_path / "song.flac")
    generic = ResolvedTrack(
        track.raw_snapshot,
        YouTubeIdentity("xa4WrgqI7q0"),
        track.candidate,
        track.confidence,
    )
    assert EmbeddedLyricsProvider(lambda _path: None).load(generic).status is (
        LocalLyricsStatus.MISS
    )
