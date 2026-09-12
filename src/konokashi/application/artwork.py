"""Frontend-neutral album-art asset and loading boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ArtworkLoadStatus(Enum):
    """Privacy-safe result classes that never include a local path."""

    LOADED = "loaded"
    ABSENT = "absent"
    UNSUPPORTED_URI = "unsupported-uri"
    INVALID_URI = "invalid-uri"
    UNAVAILABLE = "unavailable"
    TOO_LARGE = "too-large"
    UNSUPPORTED_FORMAT = "unsupported-format"
    INVALID_IMAGE = "invalid-image"


@dataclass(frozen=True, slots=True)
class ArtworkAsset:
    """Bounded RGBA thumbnail plus one non-identifying palette color."""

    rgba: bytes
    width: int
    height: int
    palette_color: str

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1 or self.width > 512 or self.height > 512:
            raise ValueError("artwork dimensions must be within 1..512")
        if len(self.rgba) != self.width * self.height * 4:
            raise ValueError("artwork RGBA payload size does not match its dimensions")
        if (
            len(self.palette_color) != 7
            or not self.palette_color.startswith("#")
            or any(
                character not in "0123456789ABCDEF"
                for character in self.palette_color[1:].upper()
            )
        ):
            raise ValueError("artwork palette color must be #RRGGBB")


@dataclass(frozen=True, slots=True)
class ArtworkLoadResult:
    """One controlled load outcome without exposing its untrusted source URI."""

    status: ArtworkLoadStatus
    asset: ArtworkAsset | None = None

    def __post_init__(self) -> None:
        if (self.status is ArtworkLoadStatus.LOADED) != (self.asset is not None):
            raise ValueError("loaded artwork status and asset must agree")


class ArtworkLoaderPort(Protocol):
    """Load one MPRIS artwork URI outside the presentation/UI thread."""

    def load(self, uri: str | None) -> ArtworkLoadResult:
        """Return a bounded local asset or a path-free controlled status."""
