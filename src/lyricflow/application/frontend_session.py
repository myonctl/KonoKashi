"""Reusable current-track loading boundary for desktop and future TUI adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from lyricflow.application.ports import (
    SettingsRepositoryPort,
    TimingCalibrationRepositoryPort,
)
from lyricflow.application.representations import RepresentationService
from lyricflow.application.resolve_lyrics import LyricsResolver
from lyricflow.application.select_player import PlayerSelectionService
from lyricflow.application.settings import DesktopInteractionSettings
from lyricflow.domain.lyrics import LyricsResolutionResult, RepresentationKind
from lyricflow.domain.models import PlayerListResult
from lyricflow.domain.representations import (
    EffectiveRepresentationLine,
    RepresentationDisplaySettings,
)
from lyricflow.domain.synchronization import LyricDocumentTiming
from lyricflow.domain.tracks import PlayerSelectionResult, ResolvedTrack


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

    def cancel_inflight(self) -> None:
        """Request cancellation of the replaceable provider boundary."""


class FrontendSessionService:
    """Coordinate Stage 1-6 services without exposing adapters to a frontend."""

    def __init__(
        self,
        selection: PlayerSelectionService,
        lyrics: LyricsResolver,
        representations: RepresentationService,
        settings: SettingsRepositoryPort,
        timing: TimingCalibrationRepositoryPort,
        cancel_inflight: Callable[[], None] | None = None,
    ) -> None:
        self._selection = selection
        self._lyrics = lyrics
        self._representations = representations
        self._settings = settings
        self._timing = timing
        self._cancel_inflight = cancel_inflight or (lambda: None)

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

    def cancel_inflight(self) -> None:
        """Cancel a provider request after a source change or frontend shutdown."""

        self._cancel_inflight()
