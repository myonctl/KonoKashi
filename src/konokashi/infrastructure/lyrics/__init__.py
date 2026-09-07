"""Read-only local and network lyrics infrastructure adapters."""

from konokashi.infrastructure.lyrics.embedded import EmbeddedLyricsProvider
from konokashi.infrastructure.lyrics.local_sidecar import LocalSidecarLyricsProvider
from konokashi.infrastructure.lyrics.lrclib import LrclibLyricsProvider
from konokashi.infrastructure.lyrics.provider_documents import (
    ProviderLyricDocumentBuilder,
)

__all__ = [
    "EmbeddedLyricsProvider",
    "LocalSidecarLyricsProvider",
    "LrclibLyricsProvider",
    "ProviderLyricDocumentBuilder",
]
