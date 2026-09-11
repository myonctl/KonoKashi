"""Reusable current-track loading boundary for desktop and future TUI adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from konokashi.application.frontend_lines import (
    FrontendLineCache,
    build_frontend_line_cache,
)
from konokashi.application.lyric_corrections import LyricCorrectionService
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
    TranslationReviewLine,
)
from konokashi.application.select_player import PlayerSelectionService
from konokashi.application.settings import DesktopInteractionSettings
from konokashi.domain.identity import PersistenceScope
from konokashi.domain.lyric_corrections import (
    LyricCorrectionProjection,
    LyricLineEdit,
)
from konokashi.domain.lyrics import (
    LyricDocument,
    LyricsAlternative,
    LyricsResolutionResult,
    RepresentationKind,
)
from konokashi.domain.models import PlayerListResult
from konokashi.domain.representations import (
    EffectiveRepresentationLine,
    LanguageRoutingEvidence,
    RepresentationDisplaySettings,
    RepresentationGenerationReport,
    RepresentationLayerStatus,
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
    generation_report: RepresentationGenerationReport | None = None
    routing: LanguageRoutingEvidence | None = None
    layer_statuses: tuple[RepresentationLayerStatus, ...] = ()
    line_cache: FrontendLineCache | None = None
    source_document: LyricDocument | None = None
    correction_projection: LyricCorrectionProjection | None = None


class FrontendSessionPort(Protocol):
    """Presentation-facing use cases shared by desktop and future TUI adapters."""

    def select_track(
        self, players: PlayerListResult, *, player_override: str | None = None
    ) -> PlayerSelectionResult:
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

    def representation_statuses(
        self,
        bundle: FrontendLyricsBundle,
        settings: RepresentationDisplaySettings,
    ) -> tuple[RepresentationLayerStatus, ...]:
        """Recompute shown/hidden layer state without regenerating content."""

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
        self,
        track: ResolvedTrack,
        *,
        title: str,
        artists: tuple[str, ...],
        album: str | None,
    ) -> None:
        """Persist one source-identity title, artist, and album correction."""

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

    def set_document_language(
        self, bundle: FrontendLyricsBundle, language: str
    ) -> None:
        """Persist and regenerate one exact-document Han language route."""

    def reset_document_language(self, bundle: FrontendLyricsBundle) -> bool:
        """Reset exact-document language routing to automatic."""

    def put_translation(
        self,
        bundle: FrontendLyricsBundle,
        *,
        source_line_id: str,
        text: str,
    ) -> None:
        """Save and approve one explicitly mapped local translation line."""

    def reset_translation(
        self, bundle: FrontendLyricsBundle, *, source_line_id: str
    ) -> bool:
        """Reset the user decision for one exact translated line."""

    def set_display_delay(self, bundle: FrontendLyricsBundle, delay_us: int) -> None:
        """Persist one exact-document lyric delay."""

    def reset_display_delay(self, bundle: FrontendLyricsBundle) -> bool:
        """Reset one exact-document lyric delay."""

    def put_lyric_edits(
        self, bundle: FrontendLyricsBundle, edits: tuple[LyricLineEdit, ...]
    ) -> int:
        """Atomically replace local line text/timestamp overlays."""

    def reset_lyric_edits(self, bundle: FrontendLyricsBundle) -> int:
        """Revert every line overlay to the immutable source document."""

    def import_lyric_text(self, bundle: FrontendLyricsBundle, text: str) -> int:
        """Import aligned plain/LRC text as the same local correction layer."""

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
        lyric_corrections: LyricCorrectionService | None = None,
    ) -> None:
        self._selection = selection
        self._lyrics = lyrics
        self._representations = representations
        self._settings = settings
        self._timing = timing
        self._corrections = corrections
        self._lyric_corrections = lyric_corrections
        self._cancel_inflight = cancel_inflight or (lambda: None)
        self._youtube_metadata = youtube_metadata

    def select_track(
        self, players: PlayerListResult, *, player_override: str | None = None
    ) -> PlayerSelectionResult:
        """Select from a captured player list using durable shared policy."""

        return self._selection.select(
            players,
            self._settings.get_player_selection(),
            player_override=player_override,
        )

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
        source_document = document
        correction_projection = (
            None
            if self._lyric_corrections is None
            else self._lyric_corrections.project(source_document)
        )
        if correction_projection is not None:
            document = correction_projection.document
            if (
                correction_projection.applied_corrections
                or correction_projection.stale_corrections
            ):
                source_label = result.source_label or source_document.source_name
                if correction_projection.applied_corrections:
                    source_label += " + local corrections"
                result = replace(
                    result,
                    document=document,
                    source_label=source_label,
                    diagnostics=(
                        *result.diagnostics,
                        *correction_projection.diagnostics,
                    ),
                )
        # Frontends request ready-to-display multilingual values from this
        # shared application boundary.  Generation is local, runs on the
        # desktop worker, and preserves imported/user-approved precedence.
        # Keeping it here also gives the future TUI the same behavior without
        # teaching either presentation adapter about romanization engines.
        report = self._representations.generate(document)
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
        statuses = tuple(
            self._representations.layer_status(document, kind, visible=visible)
            for kind, visible in (
                (RepresentationKind.ROMANIZED, settings.show_romanized),
                (RepresentationKind.TRANSLITERATED, settings.show_romanized),
                (RepresentationKind.TRANSLATED, settings.show_translated),
            )
        )
        return FrontendLyricsBundle(
            track,
            result,
            effective,
            settings,
            timing,
            report,
            self._representations.routing_language(document),
            statuses,
            build_frontend_line_cache(document, effective),
            source_document,
            correction_projection,
        )

    def put_display_settings(self, settings: RepresentationDisplaySettings) -> None:
        """Persist the same display policy used by every frontend."""

        self._settings.put_representation_display(settings)

    def representation_statuses(
        self,
        bundle: FrontendLyricsBundle,
        settings: RepresentationDisplaySettings,
    ) -> tuple[RepresentationLayerStatus, ...]:
        """Recompute availability after a visibility-only settings change."""

        document = bundle.resolution.document
        if document is None:
            return ()
        return tuple(
            self._representations.layer_status(document, kind, visible=visible)
            for kind, visible in (
                (RepresentationKind.ROMANIZED, settings.show_romanized),
                (RepresentationKind.TRANSLITERATED, settings.show_romanized),
                (RepresentationKind.TRANSLATED, settings.show_translated),
            )
        )

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
        snapshot = self._corrections.snapshot(
            bundle.track, bundle.resolution, alternatives
        )
        document = bundle.resolution.document
        if document is None:
            return snapshot
        routing = self._representations.routing_language(document)
        override = self._representations.language_override(document.document_id)
        translated = self._representations.effective_lines(
            document, RepresentationKind.TRANSLATED
        )
        return replace(
            snapshot,
            routing_status=routing.status,
            routing_language=routing.language,
            routing_diagnostic=routing.diagnostic,
            language_override=(None if override is None else override.language),
            layer_statuses=self.representation_statuses(
                bundle, bundle.display_settings
            ),
            translation_lines=tuple(
                TranslationReviewLine(
                    item.original_line.line_id,
                    item.original_line.text,
                    item.text,
                    item.provenance,
                    item.approval_state,
                    item.diagnostics,
                )
                for item in translated
            ),
            lyric_editor=(
                None
                if bundle.correction_projection is None
                else bundle.correction_projection.editor
            ),
        )

    def put_track_override(
        self,
        track: ResolvedTrack,
        *,
        title: str,
        artists: tuple[str, ...],
        album: str | None,
    ) -> None:
        self._corrections.put_track_override(
            track, title=title, artists=artists, album=album
        )

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

    def set_document_language(
        self, bundle: FrontendLyricsBundle, language: str
    ) -> None:
        document = _require_document(bundle)
        self._representations.set_language_override(document, language)
        self._representations.generate(document)

    def reset_document_language(self, bundle: FrontendLyricsBundle) -> bool:
        document = _require_document(bundle)
        removed = self._representations.reset_language_override(document)
        self._representations.generate(document)
        return removed

    def put_translation(
        self,
        bundle: FrontendLyricsBundle,
        *,
        source_line_id: str,
        text: str,
    ) -> None:
        document = _require_document(bundle)
        self._representations.set_draft(
            document,
            source_line_id,
            RepresentationKind.TRANSLATED,
            text,
        )
        self._representations.approve(
            document,
            source_line_id,
            RepresentationKind.TRANSLATED,
        )

    def reset_translation(
        self, bundle: FrontendLyricsBundle, *, source_line_id: str
    ) -> bool:
        document = _require_document(bundle)
        return self._representations.reset(
            document,
            source_line_id,
            RepresentationKind.TRANSLATED,
        )

    def set_display_delay(self, bundle: FrontendLyricsBundle, delay_us: int) -> None:
        self._corrections.set_display_delay(bundle.track, bundle.resolution, delay_us)

    def reset_display_delay(self, bundle: FrontendLyricsBundle) -> bool:
        return self._corrections.reset_display_delay(bundle.track, bundle.resolution)

    def put_lyric_edits(
        self, bundle: FrontendLyricsBundle, edits: tuple[LyricLineEdit, ...]
    ) -> int:
        if self._lyric_corrections is None:
            raise ValueError("local lyric corrections are unavailable")
        _require_durable_bundle(bundle)
        source = _require_source_document(bundle)
        return self._lyric_corrections.replace(source, edits)

    def reset_lyric_edits(self, bundle: FrontendLyricsBundle) -> int:
        if self._lyric_corrections is None:
            raise ValueError("local lyric corrections are unavailable")
        _require_durable_bundle(bundle)
        return self._lyric_corrections.reset(_require_source_document(bundle))

    def import_lyric_text(self, bundle: FrontendLyricsBundle, text: str) -> int:
        if self._lyric_corrections is None:
            raise ValueError("local lyric corrections are unavailable")
        _require_durable_bundle(bundle)
        return self._lyric_corrections.import_text(
            _require_source_document(bundle), text
        )

    def cancel_inflight(self) -> None:
        """Cancel a provider request after a source change or frontend shutdown."""

        self._cancel_inflight()
        if self._youtube_metadata is not None:
            cancellation = getattr(self._youtube_metadata, "cancel_inflight", None)
            if callable(cancellation):
                cancellation()


def _require_document(bundle: FrontendLyricsBundle) -> LyricDocument:
    document = bundle.resolution.document
    if document is None:
        raise ValueError("there is no current lyric document")
    return document


def _require_source_document(bundle: FrontendLyricsBundle) -> LyricDocument:
    source = bundle.source_document or bundle.resolution.document
    if source is None:
        raise ValueError("there is no current lyric source document")
    return source


def _require_durable_bundle(bundle: FrontendLyricsBundle) -> None:
    if bundle.track.source_identity.persistence_scope is not PersistenceScope.PERMANENT:
        raise ValueError(
            "session-only sources cannot receive durable lyric corrections"
        )
