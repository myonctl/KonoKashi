"""Bounded local-only MPRIS album-art loading."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QImageReader

from konokashi.application.artwork import (
    ArtworkAsset,
    ArtworkLoadResult,
    ArtworkLoadStatus,
)

MAX_ARTWORK_URI_CHARS = 8_192
MAX_ARTWORK_FILE_BYTES = 16 * 1024 * 1024
MAX_ARTWORK_SOURCE_PIXELS = 16_777_216
ARTWORK_THUMBNAIL_EDGE = 512
_SUPPORTED_FORMATS = frozenset({"jpeg", "jpg", "png", "webp"})


class LocalArtworkLoader:
    """Decode only local bounded raster art; never follow a network URI."""

    def load(self, uri: str | None) -> ArtworkLoadResult:
        if uri is None or not uri.strip():
            return ArtworkLoadResult(ArtworkLoadStatus.ABSENT)
        if len(uri) > MAX_ARTWORK_URI_CHARS:
            return ArtworkLoadResult(ArtworkLoadStatus.INVALID_URI)
        try:
            parsed = urlsplit(uri)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError:
            return ArtworkLoadResult(ArtworkLoadStatus.INVALID_URI)
        if parsed.scheme.casefold() != "file":
            return ArtworkLoadResult(ArtworkLoadStatus.UNSUPPORTED_URI)
        if (
            parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or port
        ):
            return ArtworkLoadResult(ArtworkLoadStatus.INVALID_URI)
        if hostname not in {None, "", "localhost"}:
            return ArtworkLoadResult(ArtworkLoadStatus.UNSUPPORTED_URI)
        try:
            decoded_path = unquote(parsed.path, errors="strict")
        except (UnicodeDecodeError, ValueError):
            return ArtworkLoadResult(ArtworkLoadStatus.INVALID_URI)
        if not decoded_path or "\x00" in decoded_path:
            return ArtworkLoadResult(ArtworkLoadStatus.INVALID_URI)
        path = Path(decoded_path)
        if not path.is_absolute():
            return ArtworkLoadResult(ArtworkLoadStatus.INVALID_URI)
        try:
            flags = os.O_RDONLY | os.O_NONBLOCK
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            with os.fdopen(os.open(path, flags), "rb") as source:
                metadata = os.fstat(source.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    return ArtworkLoadResult(ArtworkLoadStatus.UNAVAILABLE)
                if metadata.st_size > MAX_ARTWORK_FILE_BYTES:
                    return ArtworkLoadResult(ArtworkLoadStatus.TOO_LARGE)
                payload = source.read(MAX_ARTWORK_FILE_BYTES + 1)
        except OSError:
            return ArtworkLoadResult(ArtworkLoadStatus.UNAVAILABLE)
        if len(payload) > MAX_ARTWORK_FILE_BYTES:
            return ArtworkLoadResult(ArtworkLoadStatus.TOO_LARGE)
        return _decode_artwork(payload)


def _decode_artwork(payload: bytes) -> ArtworkLoadResult:
    if not payload:
        return ArtworkLoadResult(ArtworkLoadStatus.INVALID_IMAGE)
    encoded = QByteArray(payload)
    device = QBuffer(encoded)
    if not device.open(QIODevice.OpenModeFlag.ReadOnly):
        return ArtworkLoadResult(ArtworkLoadStatus.INVALID_IMAGE)
    reader = QImageReader(device)
    reader.setDecideFormatFromContent(True)
    if not reader.canRead():
        return ArtworkLoadResult(ArtworkLoadStatus.INVALID_IMAGE)
    format_name = (
        memoryview(reader.format().data()).tobytes().decode("ascii").casefold()
    )
    if format_name not in _SUPPORTED_FORMATS:
        return ArtworkLoadResult(ArtworkLoadStatus.UNSUPPORTED_FORMAT)
    source_size = reader.size()
    if (
        not source_size.isValid()
        or source_size.width() * source_size.height() > MAX_ARTWORK_SOURCE_PIXELS
    ):
        return ArtworkLoadResult(ArtworkLoadStatus.TOO_LARGE)
    scaled_size = source_size.scaled(
        QSize(ARTWORK_THUMBNAIL_EDGE, ARTWORK_THUMBNAIL_EDGE),
        Qt.AspectRatioMode.KeepAspectRatio,
    )
    reader.setScaledSize(scaled_size)
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        return ArtworkLoadResult(ArtworkLoadStatus.INVALID_IMAGE)
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width = rgba.width()
    height = rgba.height()
    raw = bytes(rgba.constBits())
    expected = width * height * 4
    if len(raw) < expected:
        return ArtworkLoadResult(ArtworkLoadStatus.INVALID_IMAGE)
    asset = ArtworkAsset(
        raw[:expected],
        width,
        height,
        _average_palette_color(rgba),
    )
    return ArtworkLoadResult(ArtworkLoadStatus.LOADED, asset)


def _average_palette_color(image: QImage) -> str:
    sample = image.scaled(
        QSize(24, 24),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    red = green = blue = weight = 0
    for y in range(sample.height()):
        for x in range(sample.width()):
            color = sample.pixelColor(x, y)
            alpha = color.alpha()
            if alpha < 16:
                continue
            red += color.red() * alpha
            green += color.green() * alpha
            blue += color.blue() * alpha
            weight += alpha
    if weight == 0:
        return "#202124"
    average = (round(red / weight), round(green / weight), round(blue / weight))
    return "#" + "".join(f"{channel:02X}" for channel in average)
