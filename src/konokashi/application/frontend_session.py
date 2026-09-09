"""Reusable current-track loading boundary for desktop and future TUI adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from konokashi.application.ports import (
    SettingsRepositoryPort,
    TimingCalibrationRepositoryPort,
    YouTubeMetadataEnrichmentPort,
)
from konokashi.application.representations import RepresentationService
from konokashi.application.resolve_lyrics import LyricsResolver
from konokashi.application.review_corrections import (
    ReviewCorrectionService,
    ReviewCorrectionSnapshot,
)
from konokashi.application.select_player import PlayerSelectionService
from konokashi.application.settings import DesktopInteractionSettings
from konokashi.domain.lyrics import (
    LyricsAlternative,
    LyricsResolutionResult,
    RepresentationKind,
)
from konokashi.domain.models import PlayerListResult
from konokashi.domain.representations import (
    EffectiveRepresentationLine,
    RepresentationDisplaySettings,
)
from konokashi.domain.synchronization import LyricDocumentTiming
from konokashi.domain.tracks import PlayerSelectionResult, ResolvedTrack


@dataclass(frozen=True, slots=True)
class FrontendLyricsBundle:
    """One source-bound resolution result with every shared display value."""

    track: ResolvedTrack
    resolution: LyricsResolutionResult
    representations: tuple[EffectiveRepresentationLine, ...]
    display_settings: RepresentationDisplaySettings
    document_timing: LyricDocumentTiming | None


class FrontendSessionPort(Protocol):
    """Presentation-facing use cases shared by desktop and future TUI adapters."""

    def select_track(self, players: PlayerListResult) -> PlayerSelectionResult:
        """Select one current track from provider-neutral player observations."""

    def load_track(
        self,
        track: ResolvedTrack,
        *,
        offline: bool = False,
        refresh: bool = False,
    ) -> FrontendLyricsBundle:
        """Resolve one selected track into frontend-ready application values."""

    def put_display_settings(self, settings: RepresentationDisplaySettings) -> None:
        """Persist shared multilingual display settings."""

    def put_interaction_settings(self, settings: DesktopInteractionSettings) -> None:
        """Persist opt-in desktop interaction mechanics."""

    def review_track(
        self,
        bundle: FrontendLyricsBundle,
        *,
        offline: bool = False,
        refresh: bool = False,
        title: str | None = None,
        artists: tuple[str, ...] = (),
        enrich_youtube: bool = False,
    ) -> ReviewCorrectionSnapshot:
        """Load alternatives and audit evidence for one exact current source."""

    def put_track_override(
        self, track: ResolvedTrack, *, title: str, artists: tuple[str, ...]
    ) -> None:
        """Persist one source-identity artist/title correction."""

    def reset_track_override(self, track: ResolvedTrack) -> bool:
        """Reset only the source-identity correction."""

    def approve_current(self, bundle: FrontendLyricsBundle) -> None:
        """Approve the current lyric document."""

    def reject_current(self, bundle: FrontendLyricsBundle) -> None:
        """Reject the current lyric document."""

    def choose_alternative(
        self, track: ResolvedTrack, alternative: LyricsAlternative
    ) -> None:
        """Approve one explicitly selected provider alternative."""

    def reject_alternative(
        self, track: ResolvedTrack, alternative: LyricsAlternative
    ) -> None:
        """Reject one explicitly selected provider alternative."""

    def reset_match(self, track: ResolvedTrack) -> bool:
        """Reset current and rejected lyric match preferences for one source."""

    def set_display_delay(self, bundle: FrontendLyricsBundle, delay_us: int) -> None:
        """Persist one exact-document lyric delay."""

    def reset_display_delay(self, bundle: FrontendLyricsBundle) -> bool:
        """Reset one exact-document lyric delay."""

    def cancel_inflight(self) -> None:
        """Request cancellation of the replaceable provider boundary."""


class FrontendSessionService:
    """Coordinate shared Stage 1-8 services without exposing adapter values."""

    def __init__(
        self,
        selection: PlayerSelectionService,
        lyrics: LyricsResolver,
        representations: RepresentationService,
        settings: SettingsRepositoryPort,
        timing: TimingCalibrationRepositoryPort,
        corrections: ReviewCorrectionService,
        cancel_inflight: Callable[[], None] | None = None,
        youtube_metadata: YouTubeMetadataEnrichmentPort | None = None,
    ) -> None:
        self._selection = selection
        self._lyrics = lyrics
        self._representations = representations
        self._settings = settings
        self._timing = timing
        self._corrections = corrections
        self._cancel_inflight = cancel_inflight or (lambda: None)
        self._youtube_metadata = youtube_metadata

    def select_track(self, players: PlayerListResult) -> PlayerSelectionResult:
        """Select from a captured player list using durable shared policy."""

        return self._selection.select(players, self._settings.get_player_selection())

    def load_track(
        self,
        track: ResolvedTrack,
        *,
        offline: bool = False,
        refresh: bool = False,
    ) -> FrontendLyricsBundle:
        """Resolve one selected source and collect its aligned display layers."""

        result = self._lyrics.resolve(track, offline=offline, refresh=refresh)
        settings = self._settings.get_representation_display()
        document = result.document
        if document is None:
            return FrontendLyricsBundle(track, result, (), settings, None)
        # Frontends request ready-to-display multilingual values from this
        # shared application boundary.  Generation is local, runs on the
        # desktop worker, and preserves imported/user-approved precedence.
        # Keeping it here also gives the future TUI the same behavior without
        # teaching either presentation adapter about romanization engines.
        self._representations.generate(document)
        effective = tuple(
            line
            for kind in (
                RepresentationKind.ROMANIZED,
                RepresentationKind.TRANSLITERATED,
                RepresentationKind.TRANSLATED,
            )
            for line in self._representations.effective_lines(document, kind)
        )
        timing = self._timing.get_document_timing(document.document_id)
        return FrontendLyricsBundle(track, result, effective, settings, timing)

    def put_display_settings(self, settings: RepresentationDisplaySettings) -> None:
        """Persist the same display policy used by every frontend."""

        self._settings.put_representation_display(settings)

    def put_interaction_settings(self, settings: DesktopInteractionSettings) -> None:
        """Persist desktop mechanics through the shared settings repository."""

        self._settings.put_desktop_interaction(settings)

    def review_track(
        self,
        bundle: FrontendLyricsBundle,
        *,
        offline: bool = False,
        refresh: bool = False,
        title: str | None = None,
        artists: tuple[str, ...] = (),
        enrich_youtube: bool = False,
    ) -> ReviewCorrectionSnapshot:
        """Return source-bound alternatives and raw/effective audit evidence."""

        search_track = bundle.track
        enrichment_diagnostics: tuple[str, ...] = ()
        enrichment_cache_hit = False
        enrichment_network_used = False
        if enrich_youtube and self._youtube_metadata is not None:
            enrichment = self._youtube_metadata.enrich(
                bundle.track,
                offline=offline,
                refresh=refresh,
            )
            enrichment_diagnostics = enrichment.diagnostics
            enrichment_cache_hit = enrichment.cache_hit
            enrichment_network_used = enrichment.network_used
            if enrichment.candidates:
                interpretations = bundle.track.interpretation_candidates or (
                    bundle.track.candidate,
                )
                search_track = replace(
                    bundle.track,
                    interpretation_candidates=(
                        *interpretations,
                        *enrichment.candidates,
                    ),
                )
        alternatives = self._lyrics.alternatives(
            search_track,
            offline=offline,
            refresh=refresh,
            title=title,
            artists=artists,
        )
        if enrichment_diagnostics or enrichment_cache_hit or enrichment_network_used:
            alternatives = replace(
                alternatives,
                diagnostics=(
                    *enrichment_diagnostics,
                    *alternatives.diagnostics,
                ),
                cache_hit=alternatives.cache_hit or enrichment_cache_hit,
                network_used=(alternatives.network_used or enrichment_network_used),
            )
        return self._corrections.snapshot(bundle.track, bundle.resolution, alternatives)

    def put_track_override(
        self, track: ResolvedTrack, *, title: str, artists: tuple[str, ...]
    ) -> None:
        self._corrections.put_track_override(track, title=title, artists=artists)

    def reset_track_override(self, track: ResolvedTrack) -> bool:
        return self._corrections.reset_track_override(track)

    def approve_current(self, bundle: FrontendLyricsBundle) -> None:
        self._corrections.approve_current(bundle.track, bundle.resolution)

    def reject_current(self, bundle: FrontendLyricsBundle) -> None:
        self._corrections.reject_current(bundle.track, bundle.resolution)

    def choose_alternative(
        self, track: ResolvedTrack, alternative: LyricsAlternative
    ) -> None:
        self._corrections.choose_alternative(track, alternative)

    def reject_alternative(
        self, track: ResolvedTrack, alternative: LyricsAlternative
    ) -> None:
        self._corrections.reject_alternative(track, alternative)

    def reset_match(self, track: ResolvedTrack) -> bool:
        return self._corrections.reset_match(track)

    def set_display_delay(self, bundle: FrontendLyricsBundle, delay_us: int) -> None:
        self._corrections.set_display_delay(bundle.track, bundle.resolution, delay_us)

    def reset_display_delay(self, bundle: FrontendLyricsBundle) -> bool:
        return self._corrections.reset_display_delay(bundle.track, bundle.resolution)

    def cancel_inflight(self) -> None:
        """Cancel a provider request after a source change or frontend shutdown."""

        self._cancel_inflight()
        if self._youtube_metadata is not None:
            cancellation = getattr(self._youtube_metadata, "cancel_inflight", None)
            if callable(cancellation):
                cancellation()
