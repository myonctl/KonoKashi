"""Read-only local and network lyrics infrastructure adapters."""

from lyriflux.infrastructure.lyrics.embedded import EmbeddedLyricsProvider
from lyriflux.infrastructure.lyrics.local_sidecar import LocalSidecarLyricsProvider
from lyriflux.infrastructure.lyrics.lrclib import LrclibLyricsProvider
from lyriflux.infrastructure.lyrics.provider_documents import (
    ProviderLyricDocumentBuilder,
)

__all__ = [
    "EmbeddedLyricsProvider",
    "LocalSidecarLyricsProvider",
    "LrclibLyricsProvider",
    "ProviderLyricDocumentBuilder",
]
