"""Composition root for the presentation-neutral current-track session service."""

from __future__ import annotations

from lyriflux.application.frontend_session import FrontendSessionService
from lyriflux.application.ports import LyricsProviderPort
from lyriflux.application.representations import RepresentationService
from lyriflux.application.resolve_lyrics import LyricsResolver
from lyriflux.application.resolve_track import TrackResolver
from lyriflux.application.review_corrections import ReviewCorrectionService
from lyriflux.application.select_player import PlayerSelectionService
from lyriflux.application.source_identity import SourceIdentityResolver
from lyriflux.infrastructure.lyrics.embedded import EmbeddedLyricsProvider
from lyriflux.infrastructure.lyrics.local_sidecar import LocalSidecarLyricsProvider
from lyriflux.infrastructure.lyrics.provider_documents import (
    ProviderLyricDocumentBuilder,
)
from lyriflux.infrastructure.metadata.local_paths import (
    FilesystemLocalPathCanonicalizer,
)
from lyriflux.infrastructure.romanization.offline import (
    IcuHanLanguageEvidenceAdapter,
    OfflineRomanizationProvider,
)
from lyriflux.infrastructure.storage.bootstrap import StorageRepositories


def create_frontend_session(
    storage: StorageRepositories,
    provider: LyricsProviderPort,
) -> FrontendSessionService:
    """Assemble shared application services for desktop and future frontends."""

    cancellation = getattr(provider, "cancel_inflight", None)
    documents = ProviderLyricDocumentBuilder()
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
            provider_documents=documents,
            lyrics=storage.lyrics,
            matches=storage.lyrics_matches,
            provider_cache=storage.provider_cache,
        ),
        RepresentationService(
            OfflineRomanizationProvider(),
            storage.representations,
            language_evidence=IcuHanLanguageEvidenceAdapter(),
        ),
        storage.settings,
        storage.timing_calibrations,
        ReviewCorrectionService(
            track_overrides=storage.track_overrides,
            lyrics=storage.lyrics,
            matches=storage.lyrics_matches,
            provider_documents=documents,
            timing=storage.timing_calibrations,
        ),
        cancellation if callable(cancellation) else None,
    )
