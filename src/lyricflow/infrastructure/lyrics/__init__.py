"""Read-only local and network lyrics infrastructure adapters."""

from lyricflow.infrastructure.lyrics.embedded import EmbeddedLyricsProvider
from lyricflow.infrastructure.lyrics.local_sidecar import LocalSidecarLyricsProvider
from lyricflow.infrastructure.lyrics.lrclib import LrclibLyricsProvider
from lyricflow.infrastructure.lyrics.provider_documents import (
    ProviderLyricDocumentBuilder,
)

__all__ = [
    "EmbeddedLyricsProvider",
    "LocalSidecarLyricsProvider",
    "LrclibLyricsProvider",
    "ProviderLyricDocumentBuilder",
]
