"""Bounded local album-art loading and neutral asset contracts."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QImage

from konokashi.application.artwork import (
    ArtworkAsset,
    ArtworkLoadResult,
    ArtworkLoadStatus,
)
from konokashi.infrastructure.artwork import (
    ARTWORK_THUMBNAIL_EDGE,
    MAX_ARTWORK_FILE_BYTES,
    LocalArtworkLoader,
)


def test_artwork_asset_and_result_invariants() -> None:
    asset = ArtworkAsset(bytes((1, 2, 3, 255)), 1, 1, "#010203")
    assert ArtworkLoadResult(ArtworkLoadStatus.LOADED, asset).asset is asset
    with pytest.raises(ValueError, match="payload size"):
        ArtworkAsset(b"short", 2, 2, "#010203")
    with pytest.raises(ValueError, match="must agree"):
        ArtworkLoadResult(ArtworkLoadStatus.ABSENT, asset)


@pytest.mark.parametrize(
    ("uri", "status"),
    (
        (None, ArtworkLoadStatus.ABSENT),
        ("", ArtworkLoadStatus.ABSENT),
        ("https://example.invalid/cover.png", ArtworkLoadStatus.UNSUPPORTED_URI),
        ("data:image/png;base64,AAAA", ArtworkLoadStatus.UNSUPPORTED_URI),
        ("file://remote.invalid/cover.png", ArtworkLoadStatus.UNSUPPORTED_URI),
        ("file:relative.png", ArtworkLoadStatus.INVALID_URI),
        ("file:///cover.png?token=secret", ArtworkLoadStatus.INVALID_URI),
        ("file://localhost:invalid/cover.png", ArtworkLoadStatus.INVALID_URI),
    ),
)
def test_loader_rejects_network_and_malformed_uris_without_io(
    uri: str | None, status: ArtworkLoadStatus
) -> None:
    assert LocalArtworkLoader().load(uri).status is status


def test_loader_decodes_scales_and_extracts_a_path_free_palette(tmp_path: Path) -> None:
    path = tmp_path / "album art Ω.png"
    image = QImage(1_024, 512, QImage.Format.Format_RGB32)
    image.fill(QColor("#C04020"))
    assert image.save(str(path))

    result = LocalArtworkLoader().load(path.as_uri())

    assert result.status is ArtworkLoadStatus.LOADED
    assert result.asset is not None
    assert result.asset.width == ARTWORK_THUMBNAIL_EDGE
    assert result.asset.height == ARTWORK_THUMBNAIL_EDGE // 2
    assert result.asset.palette_color == "#C04020"
    assert len(result.asset.rgba) == result.asset.width * result.asset.height * 4
    assert str(path) not in repr(result)


def test_loader_rejects_oversized_unsupported_and_invalid_files(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.png"
    with oversized.open("wb") as stream:
        stream.truncate(MAX_ARTWORK_FILE_BYTES + 1)
    assert (
        LocalArtworkLoader().load(oversized.as_uri()).status
        is ArtworkLoadStatus.TOO_LARGE
    )

    bitmap = tmp_path / "cover.bmp"
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(QColor("#123456"))
    assert image.save(str(bitmap))
    assert (
        LocalArtworkLoader().load(bitmap.as_uri()).status
        is ArtworkLoadStatus.UNSUPPORTED_FORMAT
    )

    invalid = tmp_path / "not-an-image.png"
    invalid.write_text("synthetic non-image", encoding="utf-8")
    assert (
        LocalArtworkLoader().load(invalid.as_uri()).status
        is ArtworkLoadStatus.INVALID_IMAGE
    )

    fifo = tmp_path / "artwork-fifo"
    os.mkfifo(fifo)
    assert (
        LocalArtworkLoader().load(fifo.as_uri()).status is ArtworkLoadStatus.UNAVAILABLE
    )
