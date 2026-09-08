"""Composition root for the presentation-neutral current-track session service."""

from __future__ import annotations

from konokashi.application.frontend_session import FrontendSessionService
from konokashi.application.ports import LyricsProviderPort, SettingsRepositoryPort
from konokashi.application.representations import RepresentationService
from konokashi.application.resolve_lyrics import LyricsResolver
from konokashi.application.resolve_track import TrackResolver
from konokashi.application.review_corrections import ReviewCorrectionService
from konokashi.application.select_player import PlayerSelectionService
from konokashi.application.source_identity import SourceIdentityResolver
from konokashi.infrastructure.lyrics.embedded import EmbeddedLyricsProvider
from konokashi.infrastructure.lyrics.local_sidecar import LocalSidecarLyricsProvider
from konokashi.infrastructure.lyrics.provider_documents import (
    ProviderLyricDocumentBuilder,
)
from konokashi.infrastructure.metadata.local_paths import (
    FilesystemLocalPathCanonicalizer,
)
from konokashi.infrastructure.romanization.offline import (
    IcuHanLanguageEvidenceAdapter,
    OfflineRomanizationProvider,
)
from konokashi.infrastructure.romanization.title_aliases import (
    OfflineTitleAliasProvider,
)
from konokashi.infrastructure.storage.bootstrap import StorageRepositories


def create_frontend_session(
    storage: StorageRepositories,
    provider: LyricsProviderPort,
    settings: SettingsRepositoryPort | None = None,
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
            title_aliases=OfflineTitleAliasProvider().aliases,
        ),
        RepresentationService(
            OfflineRomanizationProvider(),
            storage.representations,
            language_evidence=IcuHanLanguageEvidenceAdapter(),
        ),
        settings or storage.settings,
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
