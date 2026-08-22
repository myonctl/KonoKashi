"""Composition root for the presentation-neutral current-track session service."""

from __future__ import annotations

from lyricflow.application.frontend_session import FrontendSessionService
from lyricflow.application.ports import LyricsProviderPort
from lyricflow.application.representations import RepresentationService
from lyricflow.application.resolve_lyrics import LyricsResolver
from lyricflow.application.resolve_track import TrackResolver
from lyricflow.application.select_player import PlayerSelectionService
from lyricflow.application.source_identity import SourceIdentityResolver
from lyricflow.infrastructure.lyrics.embedded import EmbeddedLyricsProvider
from lyricflow.infrastructure.lyrics.local_sidecar import LocalSidecarLyricsProvider
from lyricflow.infrastructure.lyrics.provider_documents import (
    ProviderLyricDocumentBuilder,
)
from lyricflow.infrastructure.metadata.local_paths import (
    FilesystemLocalPathCanonicalizer,
)
from lyricflow.infrastructure.romanization.offline import OfflineRomanizationProvider
from lyricflow.infrastructure.storage.bootstrap import StorageRepositories


def create_frontend_session(
    storage: StorageRepositories,
    provider: LyricsProviderPort,
) -> FrontendSessionService:
    """Assemble shared application services for desktop and future frontends."""

    cancellation = getattr(provider, "cancel_inflight", None)
    return FrontendSessionService(
        PlayerSelectionService(
            TrackResolver(
                SourceIdentityResolver(FilesystemLocalPathCanonicalizer()),
                storage.track_overrides,
            )
        ),
        LyricsResolver(
            local_sources=(
                LocalSidecarLyricsProvider(),
                EmbeddedLyricsProvider(),
            ),
            provider=provider,
            provider_documents=ProviderLyricDocumentBuilder(),
            lyrics=storage.lyrics,
            matches=storage.lyrics_matches,
            provider_cache=storage.provider_cache,
        ),
        RepresentationService(OfflineRomanizationProvider(), storage.representations),
        storage.settings,
        storage.timing_calibrations,
        cancellation if callable(cancellation) else None,
    )
