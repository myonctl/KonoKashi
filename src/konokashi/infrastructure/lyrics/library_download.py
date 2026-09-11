"""Reuse the accepted lyrics resolver for policy-gated library downloads."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from konokashi.application.ports import LyricsProviderPort
from konokashi.application.resolve_lyrics import LyricsResolver
from konokashi.domain.identity import LocalFileIdentity
from konokashi.domain.library import LibraryFile, LibraryMetadata
from konokashi.domain.lyrics import LyricsResolutionStatus
from konokashi.domain.models import PlayerCapabilities, PlayerSnapshot, RawTrackMetadata
from konokashi.domain.tracks import ResolvedTrack
from konokashi.infrastructure.lyrics.embedded import EmbeddedLyricsProvider
from konokashi.infrastructure.lyrics.local_sidecar import LocalSidecarLyricsProvider
from konokashi.infrastructure.lyrics.provider_documents import (
    ProviderLyricDocumentBuilder,
)
from konokashi.infrastructure.storage.bootstrap import StorageRepositories


class LibraryLyricsDownloader:
    """Adapt high-confidence scanned metadata to the existing resolver policy."""

    def __init__(
        self,
        storage: StorageRepositories,
        provider: LyricsProviderPort | Sequence[LyricsProviderPort],
    ) -> None:
        self._resolver = LyricsResolver(
            local_sources=(LocalSidecarLyricsProvider(), EmbeddedLyricsProvider()),
            provider=provider,
            provider_documents=ProviderLyricDocumentBuilder(),
            lyrics=storage.lyrics,
            matches=storage.lyrics_matches,
            provider_cache=storage.provider_cache,
        )

    def __call__(
        self, file: LibraryFile, metadata: LibraryMetadata, offline: bool
    ) -> str:
        candidate = metadata.candidate
        raw = RawTrackMetadata(
            title=candidate.title,
            artists=candidate.artists,
            album=candidate.album,
            url=Path(file.path).as_uri(),
            duration_us=candidate.duration_us,
        )
        snapshot = PlayerSnapshot(
            "library-scan",
            "library-scan",
            "KonoKashi library scanner",
            None,
            None,
            raw,
            None,
            PlayerCapabilities(),
        )
        track = ResolvedTrack(
            snapshot,
            LocalFileIdentity(file.path),
            candidate,
            metadata.confidence,
            evidence=metadata.candidate.evidence,
        )
        result = self._resolver.resolve(track, offline=offline)
        if result.status in {
            LyricsResolutionStatus.FOUND_TIMED,
            LyricsResolutionStatus.FOUND_UNTIMED,
            LyricsResolutionStatus.INSTRUMENTAL,
        }:
            if result.source_label and "local" in result.source_label.lower():
                return "local"
            return "cached" if result.cache_hit else "downloaded"
        return result.status.value.lower()
