"""Presentation-neutral state transitions for everyday frontend adapters."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

from konokashi.application.frontend_lines import FrontendLineCache
from konokashi.application.representations import original_lines
from konokashi.application.sync_state import (
    SourceGenerationGuard,
    SourceGenerationToken,
    SynchronizationSnapshot,
    SynchronizedLine,
    SynchronizedRepresentationLayer,
)
from konokashi.domain.lyrics import (
    LyricDocument,
    LyricsResolutionResult,
    LyricsResolutionStatus,
    RepresentationKind,
)
from konokashi.domain.representations import (
    EffectiveRepresentationLine,
    LanguageRoutingEvidence,
    RepresentationDisplaySettings,
    RepresentationLayerStatus,
)
from konokashi.domain.synchronization import ClockHealth, PlaybackState
from konokashi.domain.tracks import ResolvedTrack


class DesktopLyricsState(Enum):
    """Stable user-facing states shared by desktop and future frontends."""

    WAITING = "waiting"
    RESOLVING = "resolving"
    TIMED = "timed"
    UNTIMED = "untimed"
    INSTRUMENTAL = "instrumental"
    AMBIGUOUS = "ambiguous"
    NO_RESULT = "no-result"
    OFFLINE = "offline"
    PROVIDER_FAILURE = "provider-failure"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DesktopKaraokeSegment:
    """A trusted timed span within the original line's plain text."""

    segment_id: str
    start_index: int
    end_index: int
    highlight_fraction: float


@dataclass(frozen=True, slots=True)
class DesktopRepresentationMetadata:
    """Identity and provenance for one independently selected text layer."""

    kind: str
    provenance: str | None = None
    approval_state: str | None = None
    source_name: str | None = None
    source_version: str | None = None
    language: str | None = None
    script: str | None = None
    uncertainty: str | None = None


@dataclass(frozen=True, slots=True)
class DesktopLyricGroup:
    """One grouped original/romanized/translated line for display."""

    line_id: str
    original: str | None
    romanized_or_transliterated: str | None = None
    translation: str | None = None
    provenance: tuple[str, ...] = field(default_factory=tuple)
    transition_us: int | None = None
    karaoke_segments: tuple[DesktopKaraokeSegment, ...] = field(default_factory=tuple)
    reading_metadata: DesktopRepresentationMetadata | None = None
    translation_metadata: DesktopRepresentationMetadata | None = None


@dataclass(frozen=True, slots=True)
class DesktopViewState:
    """Complete semantic state rendered by a desktop or future TUI adapter."""

    state: DesktopLyricsState
    status_message: str
    generation: int = 0
    title: str | None = None
    artists: tuple[str, ...] = field(default_factory=tuple)
    album: str | None = None
    player: str | None = None
    playback_state: PlaybackState = PlaybackState.UNKNOWN
    progress_fraction: float | None = None
    position_us: int | None = None
    duration_us: int | None = None
    previous: tuple[DesktopLyricGroup, ...] = field(default_factory=tuple)
    active: tuple[DesktopLyricGroup, ...] = field(default_factory=tuple)
    next: tuple[DesktopLyricGroup, ...] = field(default_factory=tuple)
    static_lines: tuple[DesktopLyricGroup, ...] = field(default_factory=tuple)
    lyrics_source: str | None = None
    match_confidence: str | None = None
    sync_health: ClockHealth | None = None
    display_delay_us: int = 0
    diagnostics: tuple[str, ...] = field(default_factory=tuple)


_STATUS_MESSAGES = {
    LyricsResolutionStatus.AMBIGUOUS: "Several possible lyrics results need review.",
    LyricsResolutionStatus.NO_RESULT: "No lyrics found for this recording.",
    LyricsResolutionStatus.OFFLINE_MISS: "Lyrics are not available offline.",
    LyricsResolutionStatus.PROVIDER_UNAVAILABLE: (
        "The lyrics provider is temporarily unavailable."
    ),
    LyricsResolutionStatus.RATE_LIMITED: (
        "The lyrics provider is busy. KonoKashi will recover on a later track update."
    ),
    LyricsResolutionStatus.INVALID_LOCAL_LYRICS: (
        "Local lyrics could not be read safely."
    ),
    LyricsResolutionStatus.INVALID_PROVIDER_RESPONSE: (
        "The lyrics provider returned an unsupported response."
    ),
}


class DesktopStateController:
    """Own source generations and convert application results into view state."""

    def __init__(self) -> None:
        self._guard = SourceGenerationGuard()
        self._settings = RepresentationDisplaySettings()
        self._state = DesktopViewState(
            DesktopLyricsState.WAITING,
            "Waiting for media…",
        )

    @property
    def state(self) -> DesktopViewState:
        return self._state

    @property
    def current_token(self) -> SourceGenerationToken | None:
        return self._guard.current

    def no_player(self, diagnostics: tuple[str, ...] = ()) -> DesktopViewState:
        """Invalidate every old source and present a recoverable idle state."""

        self._guard.invalidate()
        self._state = DesktopViewState(
            DesktopLyricsState.WAITING,
            "Waiting for media…",
            generation=self._state.generation + 1,
            diagnostics=diagnostics,
        )
        return self._state

    def application_error(
        self, message: str, diagnostics: tuple[str, ...] = ()
    ) -> DesktopViewState:
        """Present a controlled startup/storage failure without a selected source."""

        self._guard.invalidate()
        self._state = DesktopViewState(
            DesktopLyricsState.ERROR,
            message,
            generation=self._state.generation + 1,
            diagnostics=diagnostics,
        )
        return self._state

    def source_changed(self) -> DesktopViewState:
        """Immediately remove old lyrics while a changed source is reselected."""

        self._guard.invalidate()
        self._state = DesktopViewState(
            DesktopLyricsState.RESOLVING,
            "Resolving lyrics…",
            generation=self._state.generation + 1,
        )
        return self._state

    def begin_resolution(self, track: ResolvedTrack) -> SourceGenerationToken:
        """Invalidate old lyrics before showing metadata for a new source."""

        token = self._guard.select(track.source_identity)
        self._state = DesktopViewState(
            DesktopLyricsState.RESOLVING,
            "Resolving lyrics…",
            generation=token.generation,
            title=track.candidate.title,
            artists=track.candidate.artists,
            album=track.candidate.album,
            player=track.raw_snapshot.identity or track.raw_snapshot.service_name,
            playback_state=PlaybackState.from_mpris(track.raw_snapshot.playback_status),
            position_us=(
                track.raw_snapshot.position_us
                if track.raw_snapshot.position_us is not None
                and track.raw_snapshot.position_us >= 0
                else None
            ),
            duration_us=track.candidate.duration_us,
        )
        return token

    def fail_resolution(
        self,
        token: SourceGenerationToken,
        message: str,
        diagnostics: tuple[str, ...] = (),
    ) -> bool:
        """Present a controlled internal/boundary failure for the current source."""

        if not self._guard.accepts(token):
            return False
        self._state = self._replace_content(
            state=DesktopLyricsState.ERROR,
            message=message,
            diagnostics=diagnostics,
        )
        return True

    def accept_resolution(
        self,
        token: SourceGenerationToken,
        result: LyricsResolutionResult,
        representations: tuple[EffectiveRepresentationLine, ...] = (),
        settings: RepresentationDisplaySettings | None = None,
        layer_statuses: tuple[RepresentationLayerStatus, ...] = (),
        routing: LanguageRoutingEvidence | None = None,
        line_cache: FrontendLineCache | None = None,
    ) -> bool:
        """Accept only the current source result and map every normal outcome."""

        if (
            not self._guard.accepts(token)
            or result.source_identity != token.source_identity
        ):
            return False
        if settings is not None:
            self._settings = settings
        document = result.document
        representation_diagnostics = _representation_diagnostics(
            layer_statuses, routing
        )
        if result.status is LyricsResolutionStatus.FOUND_TIMED and document is not None:
            self._state = self._replace_content(
                state=DesktopLyricsState.TIMED,
                message="Synchronizing lyrics…",
                lyrics_source=result.source_label or document.source_name,
                match_confidence=(
                    None if result.confidence is None else result.confidence.value
                ),
                diagnostics=(*result.diagnostics, *representation_diagnostics),
            )
            return True
        if (
            result.status is LyricsResolutionStatus.FOUND_UNTIMED
            and document is not None
        ):
            self._state = self._replace_content(
                state=DesktopLyricsState.UNTIMED,
                message="Untimed lyrics",
                static_lines=self._static_groups(
                    document, representations, line_cache=line_cache
                ),
                lyrics_source=result.source_label or document.source_name,
                match_confidence=(
                    None if result.confidence is None else result.confidence.value
                ),
                diagnostics=(*result.diagnostics, *representation_diagnostics),
            )
            return True
        if result.status is LyricsResolutionStatus.INSTRUMENTAL:
            self._state = self._replace_content(
                state=DesktopLyricsState.INSTRUMENTAL,
                message="Instrumental recording",
                lyrics_source=(
                    result.source_label
                    if result.source_label is not None
                    else (None if document is None else document.source_name)
                ),
                match_confidence=(
                    None if result.confidence is None else result.confidence.value
                ),
                diagnostics=(*result.diagnostics, *representation_diagnostics),
            )
            return True
        state = _state_for_resolution_status(result.status)
        self._state = self._replace_content(
            state=state,
            message=_STATUS_MESSAGES.get(result.status, result.status.value),
            diagnostics=(*result.diagnostics, *representation_diagnostics),
        )
        return True

    def accept_snapshot(self, snapshot: SynchronizationSnapshot) -> bool:
        """Accept a timed snapshot only for the exact active generation/source."""

        token = self._guard.current
        if (
            token is None
            or snapshot.generation != token.generation
            or snapshot.source_identity != token.source_identity
        ):
            return False
        self._state = DesktopViewState(
            DesktopLyricsState.TIMED,
            "Synchronized lyrics",
            generation=token.generation,
            title=snapshot.track_title,
            artists=snapshot.artists,
            album=snapshot.album,
            player=snapshot.player_identity or snapshot.player_service,
            playback_state=snapshot.playback_status,
            progress_fraction=snapshot.progress_fraction,
            position_us=snapshot.disciplined_player_position_us,
            duration_us=snapshot.duration_us,
            previous=self._snapshot_groups(snapshot.previous),
            active=self._snapshot_groups(snapshot.active),
            next=self._snapshot_groups(snapshot.next),
            lyrics_source=snapshot.lyrics_source,
            match_confidence=snapshot.lyrics_match_confidence,
            sync_health=snapshot.clock.health,
            display_delay_us=snapshot.lyrics_display_delay_us,
            diagnostics=snapshot.diagnostics,
        )
        return True

    def set_representation_settings(
        self, settings: RepresentationDisplaySettings
    ) -> None:
        """Apply shared durable toggles to subsequent state conversions."""

        self._settings = settings

    def update_playback(
        self,
        generation: int,
        playback_state: PlaybackState,
        position_us: int,
        duration_us: int | None,
    ) -> bool:
        """Refresh lightweight progress without changing lyric content/state."""

        token = self._guard.current
        if token is None or token.generation != generation:
            return False
        progress = (
            None
            if duration_us is None or duration_us <= 0
            else min(1.0, max(0.0, position_us / duration_us))
        )
        self._state = replace(
            self._state,
            playback_state=playback_state,
            position_us=position_us,
            duration_us=duration_us,
            progress_fraction=progress,
        )
        return True

    def add_diagnostic(self, message: str) -> DesktopViewState:
        """Retain usable content while reporting a non-fatal boundary failure."""

        self._state = replace(
            self._state,
            diagnostics=(*self._state.diagnostics, message),
        )
        return self._state

    def _replace_content(
        self,
        *,
        state: DesktopLyricsState,
        message: str,
        static_lines: tuple[DesktopLyricGroup, ...] = (),
        lyrics_source: str | None = None,
        match_confidence: str | None = None,
        diagnostics: tuple[str, ...] = (),
    ) -> DesktopViewState:
        current = self._state
        return DesktopViewState(
            state,
            message,
            generation=current.generation,
            title=current.title,
            artists=current.artists,
            album=current.album,
            player=current.player,
            playback_state=current.playback_state,
            position_us=current.position_us,
            duration_us=current.duration_us,
            static_lines=static_lines,
            lyrics_source=lyrics_source,
            match_confidence=match_confidence,
            diagnostics=diagnostics,
        )

    def _snapshot_groups(
        self, lines: tuple[SynchronizedLine, ...]
    ) -> tuple[DesktopLyricGroup, ...]:
        return tuple(
            DesktopLyricGroup(
                line.line_id,
                line.original if self._settings.show_original else None,
                (
                    line.romanized_or_transliterated
                    if self._settings.show_romanized
                    else None
                ),
                line.translation if self._settings.show_translated else None,
                line.representation_provenance,
                line.effective_transition_us,
                _trusted_karaoke_segments(line),
                _desktop_representation_metadata(line.reading),
                _desktop_representation_metadata(line.translated),
            )
            for line in lines
        )

    def _static_groups(
        self,
        document: LyricDocument,
        representations: tuple[EffectiveRepresentationLine, ...],
        *,
        line_cache: FrontendLineCache | None = None,
    ) -> tuple[DesktopLyricGroup, ...]:
        by_line_kind = (
            line_cache.representations
            if line_cache is not None
            else {
                (item.original_line.line_id, item.kind): item
                for item in representations
            }
        )
        groups = []
        originals = (
            line_cache.originals if line_cache is not None else original_lines(document)
        )
        for original in originals:
            romanized = by_line_kind.get(
                (original.line_id, RepresentationKind.ROMANIZED)
            )
            transliterated = by_line_kind.get(
                (original.line_id, RepresentationKind.TRANSLITERATED)
            )
            alternate = romanized if romanized is not None else transliterated
            translation = by_line_kind.get(
                (original.line_id, RepresentationKind.TRANSLATED)
            )
            visible = tuple(
                item
                for item in (alternate, translation)
                if item is not None and item.text is not None
            )
            groups.append(
                DesktopLyricGroup(
                    original.line_id,
                    original.text if self._settings.show_original else None,
                    (
                        alternate.text
                        if self._settings.show_romanized and alternate is not None
                        else None
                    ),
                    (
                        translation.text
                        if self._settings.show_translated and translation is not None
                        else None
                    ),
                    tuple(
                        "unknown" if item.provenance is None else item.provenance.value
                        for item in visible
                    ),
                    reading_metadata=_effective_representation_metadata(alternate),
                    translation_metadata=_effective_representation_metadata(
                        translation
                    ),
                )
            )
        return tuple(groups)


def _trusted_karaoke_segments(
    line: SynchronizedLine,
) -> tuple[DesktopKaraokeSegment, ...]:
    """Project only exact, fully trusted leaf timing onto plain-text offsets."""

    parent_ids = {
        segment.parent_segment_id
        for segment in line.timing_segments
        if segment.parent_segment_id is not None
    }
    leaves = tuple(
        segment
        for segment in line.timing_segments
        if segment.segment_id not in parent_ids
    )
    if not leaves or any(segment.highlight_fraction is None for segment in leaves):
        return ()

    output: list[DesktopKaraokeSegment] = []
    cursor = 0
    for segment in leaves:
        if not segment.text or not line.original.startswith(segment.text, cursor):
            return ()
        end = cursor + len(segment.text)
        fraction = segment.highlight_fraction
        assert fraction is not None
        output.append(
            DesktopKaraokeSegment(
                segment.segment_id,
                cursor,
                end,
                fraction,
            )
        )
        cursor = end
    if cursor != len(line.original):
        return ()
    return tuple(output)


def _desktop_representation_metadata(
    layer: SynchronizedRepresentationLayer | None,
) -> DesktopRepresentationMetadata | None:
    if layer is None:
        return None
    return DesktopRepresentationMetadata(
        layer.kind,
        layer.provenance,
        layer.approval_state,
        layer.source_name,
        layer.source_version,
        layer.language,
        layer.script,
        layer.uncertainty,
    )


def _effective_representation_metadata(
    layer: EffectiveRepresentationLine | None,
) -> DesktopRepresentationMetadata | None:
    if layer is None or layer.text is None:
        return None
    return DesktopRepresentationMetadata(
        kind=layer.kind.value,
        provenance=None if layer.provenance is None else layer.provenance.value,
        approval_state=(
            None if layer.approval_state is None else layer.approval_state.value
        ),
        source_name=layer.source_name,
        source_version=layer.source_version,
        language=layer.language,
        script=layer.script,
        uncertainty=None if layer.uncertainty is None else layer.uncertainty.value,
    )


def _state_for_resolution_status(status: LyricsResolutionStatus) -> DesktopLyricsState:
    if status is LyricsResolutionStatus.AMBIGUOUS:
        return DesktopLyricsState.AMBIGUOUS
    if status is LyricsResolutionStatus.NO_RESULT:
        return DesktopLyricsState.NO_RESULT
    if status is LyricsResolutionStatus.OFFLINE_MISS:
        return DesktopLyricsState.OFFLINE
    if status in {
        LyricsResolutionStatus.PROVIDER_UNAVAILABLE,
        LyricsResolutionStatus.RATE_LIMITED,
        LyricsResolutionStatus.INVALID_PROVIDER_RESPONSE,
    }:
        return DesktopLyricsState.PROVIDER_FAILURE
    return DesktopLyricsState.ERROR


def _representation_diagnostics(
    statuses: tuple[RepresentationLayerStatus, ...],
    routing: LanguageRoutingEvidence | None = None,
) -> tuple[str, ...]:
    output: list[str] = []
    if routing is not None:
        output.append(
            f"document language routing: {routing.status.value}; "
            f"language {routing.language or 'undetermined'}; "
            f"evidence {routing.diagnostic}"
        )
    for status in statuses:
        output.append(
            f"{status.kind.value} layer: {status.availability.value}; "
            f"selected {status.candidate_selected}/{status.original_lines}; "
            + (
                "origin unavailable"
                if not status.origins
                else "origin " + ", ".join(status.origins)
            )
        )
        output.extend(
            f"{status.kind.value} layer detail: {item}" for item in status.diagnostics
        )
    return tuple(output)
