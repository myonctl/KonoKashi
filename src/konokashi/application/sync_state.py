"""Presentation-neutral synchronization snapshots and meaningful subscriptions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from konokashi.application.frontend_lines import (
    FrontendLineCache,
    build_frontend_line_cache,
)
from konokashi.application.lyrics_sync import SynchronizationFrame
from konokashi.domain.identity import SourceIdentity
from konokashi.domain.lyrics import (
    LyricDocument,
    LyricLine,
    RepresentationKind,
    lyric_line_timing_start_ms,
)
from konokashi.domain.representations import EffectiveRepresentationLine
from konokashi.domain.synchronization import (
    AudioOutputLatency,
    PlaybackClockDiagnostics,
    PlaybackPositionEstimate,
    PlaybackState,
    SynchronizationCalibration,
)
from konokashi.domain.tracks import ResolvedTrack


@dataclass(frozen=True, slots=True)
class SynchronizedTimingSegment:
    """One rich timing element projected into the calibrated lyric timeline."""

    segment_id: str
    text: str
    unit: str
    source_start_us: int
    source_end_us: int | None
    effective_start_us: int
    effective_end_us: int | None
    timing_provenance: str | None
    parent_segment_id: str | None = None
    provider_unit: str | None = None
    highlight_fraction: float | None = None


@dataclass(frozen=True, slots=True)
class SynchronizedRepresentationLayer:
    """One selected secondary text layer with its independent identity."""

    kind: str
    text: str
    provenance: str | None
    approval_state: str | None
    source_name: str | None
    source_version: str | None
    language: str | None
    script: str | None
    uncertainty: str | None


@dataclass(frozen=True, slots=True)
class SynchronizedLine:
    """One original timing identity with every selected textual layer."""

    line_id: str
    original_index: int
    source_timestamp_us: int | None
    effective_transition_us: int | None
    original: str
    reading: SynchronizedRepresentationLayer | None = None
    translated: SynchronizedRepresentationLayer | None = None
    timing_segments: tuple[SynchronizedTimingSegment, ...] = field(
        default_factory=tuple
    )

    @property
    def romanized_or_transliterated(self) -> str | None:
        return None if self.reading is None else self.reading.text

    @property
    def translation(self) -> str | None:
        return None if self.translated is None else self.translated.text

    @property
    def representation_provenance(self) -> tuple[str, ...]:
        return tuple(
            "unknown" if layer.provenance is None else layer.provenance
            for layer in (self.reading, self.translated)
            if layer is not None
        )


@dataclass(frozen=True, slots=True)
class SynchronizationSnapshot:
    """Complete app-level state suitable for desktop, TUI, web, or diagnostics."""

    generation: int
    source_identity: SourceIdentity
    player_service: str
    player_identity: str | None
    track_title: str | None
    artists: tuple[str, ...]
    album: str | None
    playback_status: PlaybackState
    reported_mpris_position_us: int | None
    disciplined_player_position_us: int
    estimated_audible_position_us: int | None
    duration_us: int | None
    rate: float
    clock: PlaybackClockDiagnostics
    audio_output: AudioOutputLatency
    lyrics_status: str
    lyrics_source: str
    lyrics_provenance: str
    lyrics_match_confidence: str | None
    lyric_document_id: str
    lyrics_timing_level: str
    lyrics_display_delay_us: int
    previous: tuple[SynchronizedLine, ...]
    active: tuple[SynchronizedLine, ...]
    next: tuple[SynchronizedLine, ...]
    next_transition_monotonic_ns: int | None
    time_until_next_transition_us: int | None
    observed_monotonic_us: int
    diagnostics: tuple[str, ...] = field(default_factory=tuple)

    @property
    def progress_fraction(self) -> float | None:
        """Return clamped playback progress without requiring frontend policy."""

        if self.duration_us is None or self.duration_us <= 0:
            return None
        return min(
            1.0, max(0.0, self.disciplined_player_position_us / self.duration_us)
        )


@dataclass(frozen=True, slots=True)
class SourceGenerationToken:
    """Identity-bearing token attached to every asynchronous source result."""

    generation: int
    source_identity: SourceIdentity


class SourceGenerationGuard:
    """Reject late lyrics/representation/cache results after source changes."""

    def __init__(self) -> None:
        self._generation = 0
        self._current: SourceGenerationToken | None = None

    @property
    def current(self) -> SourceGenerationToken | None:
        return self._current

    def select(self, source_identity: SourceIdentity) -> SourceGenerationToken:
        """Create a new generation even when display metadata looks identical."""

        self._generation += 1
        self._current = SourceGenerationToken(self._generation, source_identity)
        return self._current

    def invalidate(self) -> None:
        """Reject every outstanding result after player/source disappearance."""

        self._generation += 1
        self._current = None

    def accepts(self, token: SourceGenerationToken) -> bool:
        """Accept only the exact current generation and stable identity."""

        return token == self._current


def _line_bundle(
    line: LyricLine,
    *,
    original_index: int,
    document_delay_us: int,
    line_shift_us: int,
    display_position_us: int,
    representations: Mapping[
        tuple[str, RepresentationKind], EffectiveRepresentationLine
    ],
) -> SynchronizedLine:
    romanized = representations.get((line.line_id, RepresentationKind.ROMANIZED))
    transliterated = representations.get(
        (line.line_id, RepresentationKind.TRANSLITERATED)
    )
    translation = representations.get((line.line_id, RepresentationKind.TRANSLATED))
    alternate = (
        romanized
        if romanized is not None and romanized.text is not None
        else transliterated
        if transliterated is not None
        else romanized
    )

    def synchronized_layer(
        item: EffectiveRepresentationLine | None,
    ) -> SynchronizedRepresentationLayer | None:
        if item is None or item.text is None:
            return None
        return SynchronizedRepresentationLayer(
            kind=item.kind.value,
            text=item.text,
            provenance=None if item.provenance is None else item.provenance.value,
            approval_state=(
                None if item.approval_state is None else item.approval_state.value
            ),
            source_name=item.source_name,
            source_version=item.source_version,
            language=item.language,
            script=item.script,
            uncertainty=(None if item.uncertainty is None else item.uncertainty.value),
        )

    start_ms = lyric_line_timing_start_ms(line)
    source_us = None if start_ms is None else start_ms * 1_000
    effective_us = (
        None if source_us is None else source_us + document_delay_us + line_shift_us
    )
    timing_shift_us = document_delay_us + line_shift_us
    trusted_karaoke_provenance = {
        "provider",
        "imported",
        "user-approved",
        "user-edited",
    }

    def highlight_fraction(
        start_us: int, end_us: int | None, provenance: str | None
    ) -> float | None:
        if provenance not in trusted_karaoke_provenance:
            return None
        if display_position_us <= start_us:
            return 0.0
        if end_us is None or end_us <= start_us:
            return 1.0
        return min(1.0, (display_position_us - start_us) / (end_us - start_us))

    timing_segments = tuple(
        SynchronizedTimingSegment(
            segment.segment_id,
            segment.text,
            segment.unit.value,
            segment.start_ms * 1_000,
            None if segment.end_ms is None else segment.end_ms * 1_000,
            segment.start_ms * 1_000 + timing_shift_us,
            (
                None
                if segment.end_ms is None
                else segment.end_ms * 1_000 + timing_shift_us
            ),
            (
                None
                if segment.timing_provenance is None
                else segment.timing_provenance.value
            ),
            segment.parent_segment_id,
            segment.provider_unit,
            highlight_fraction(
                segment.start_ms * 1_000 + timing_shift_us,
                (
                    None
                    if segment.end_ms is None
                    else segment.end_ms * 1_000 + timing_shift_us
                ),
                (
                    None
                    if segment.timing_provenance is None
                    else segment.timing_provenance.value
                ),
            ),
        )
        for segment in line.timing_segments
    )
    return SynchronizedLine(
        line_id=line.line_id,
        original_index=original_index,
        source_timestamp_us=source_us,
        effective_transition_us=effective_us,
        original=line.text,
        reading=synchronized_layer(alternate),
        translated=synchronized_layer(translation),
        timing_segments=timing_segments,
    )


def build_sync_snapshot(
    *,
    generation: int,
    track: ResolvedTrack,
    document: LyricDocument,
    estimate: PlaybackPositionEstimate,
    frame: SynchronizationFrame,
    calibration: SynchronizationCalibration,
    representations: tuple[EffectiveRepresentationLine, ...] = (),
    line_cache: FrontendLineCache | None = None,
    lyrics_match_confidence: str | None = None,
) -> SynchronizationSnapshot:
    """Build a framework/storage/provider-neutral frontend state value."""

    cache = line_cache or build_frontend_line_cache(document, representations)
    if cache.document is not document:
        raise ValueError("frontend line cache does not match lyric document")
    by_line_kind = cache.representations
    original_indexes = cache.original_indexes
    shifts = dict(
        (*frame.lyrics.active_line_shifts_us, *frame.lyrics.next_line_shifts_us)
    )
    display_position_us = (
        frame.media_position_us
        if frame.audible_position_us is None
        else frame.audible_position_us
    )

    def bundle(lines: tuple[LyricLine, ...]) -> tuple[SynchronizedLine, ...]:
        return tuple(
            _line_bundle(
                line,
                original_index=original_indexes[line.line_id],
                document_delay_us=calibration.lyrics.shift_us,
                line_shift_us=shifts.get(
                    line.line_id,
                    calibration.lyrics.line_shift_us(line.line_id),
                ),
                display_position_us=display_position_us,
                representations=by_line_kind,
            )
            for line in lines
        )

    deadline_ns = None if frame.deadline is None else frame.deadline.display_deadline_ns
    until_us = (
        None if deadline_ns is None else (deadline_ns - estimate.monotonic_ns) // 1_000
    )
    snapshot = track.raw_snapshot
    representation_diagnostics = tuple(
        dict.fromkeys(
            diagnostic
            for item in representations
            if item.text is None
            for diagnostic in item.diagnostics
        )
    )
    original_representation = next(
        (
            representation
            for representation in document.representations
            if representation.kind is RepresentationKind.ORIGINAL
        ),
        None,
    )
    return SynchronizationSnapshot(
        generation=generation,
        source_identity=track.source_identity,
        player_service=snapshot.service_name,
        player_identity=snapshot.identity,
        track_title=track.candidate.title,
        artists=track.candidate.artists,
        album=track.candidate.album,
        playback_status=estimate.state,
        reported_mpris_position_us=(
            estimate.latest_authoritative_position_us
            if estimate.latest_authoritative_position_us is not None
            else snapshot.position_us
        ),
        disciplined_player_position_us=estimate.position_us,
        estimated_audible_position_us=frame.audible_position_us,
        duration_us=track.candidate.duration_us,
        rate=estimate.reported_rate,
        clock=estimate.diagnostics,
        audio_output=calibration.audio_output,
        lyrics_status=document.kind.value,
        lyrics_source=document.source_name,
        lyrics_provenance=(
            "unknown"
            if original_representation is None
            else original_representation.provenance.value
        ),
        lyrics_match_confidence=lyrics_match_confidence,
        lyric_document_id=document.document_id,
        lyrics_timing_level=document.timing_level.value,
        lyrics_display_delay_us=calibration.lyrics.shift_us,
        previous=bundle(frame.lyrics.previous),
        active=bundle(frame.lyrics.active),
        next=bundle(frame.lyrics.next),
        next_transition_monotonic_ns=deadline_ns,
        time_until_next_transition_us=until_us,
        observed_monotonic_us=estimate.monotonic_ns // 1_000,
        diagnostics=(*frame.error_budget.unknown_sources, *representation_diagnostics),
    )


SnapshotHandler = Callable[[SynchronizationSnapshot], None]


class SnapshotSubscription:
    """Idempotent unsubscriber owned by one caller."""

    def __init__(self, close: Callable[[], None]) -> None:
        self._close = close

    def close(self) -> None:
        callback, self._close = self._close, lambda: None
        callback()


class SynchronizationPublisher:
    """Publish meaningful state changes without a global singleton or tick flood."""

    def __init__(self) -> None:
        self._subscribers: dict[int, SnapshotHandler] = {}
        self._next_token = 1
        self._last_snapshot: SynchronizationSnapshot | None = None
        self._last_publication_key: tuple[object, ...] | None = None

    @property
    def subscriber_count(self) -> int:
        """Expose bounded ownership for deterministic leak tests."""

        return len(self._subscribers)

    @property
    def current(self) -> SynchronizationSnapshot | None:
        """Return the latest state for local animation/interpolation queries."""

        return self._last_snapshot

    def subscribe(self, handler: SnapshotHandler) -> SnapshotSubscription:
        """Subscribe and return a clean, idempotent unsubscribe handle."""

        token = self._next_token
        self._next_token += 1
        self._subscribers[token] = handler

        def close() -> None:
            self._subscribers.pop(token, None)

        return SnapshotSubscription(close)

    def publish(self, snapshot: SynchronizationSnapshot) -> bool:
        """Publish semantic transitions while retaining the freshest trajectory."""

        publication_key = self._publication_key(snapshot)
        changed = publication_key != self._last_publication_key
        self._last_snapshot = snapshot
        if not changed:
            return False
        self._last_publication_key = publication_key
        for handler in tuple(self._subscribers.values()):
            handler(snapshot)
        return True

    @staticmethod
    def _publication_key(snapshot: SynchronizationSnapshot) -> tuple[object, ...]:
        """Exclude locally interpolated tick fields from subscriber events."""

        clock = snapshot.clock
        clock_discipline = (
            clock.quality,
            clock.health,
            clock.drift_ppm,
            clock.drift_correction_active,
            clock.accepted_sample_count,
            clock.rejected_sample_count,
            clock.discontinuity_count,
            clock.last_sample_source,
        )

        return (
            snapshot.generation,
            snapshot.source_identity,
            snapshot.player_service,
            snapshot.player_identity,
            snapshot.track_title,
            snapshot.artists,
            snapshot.album,
            snapshot.playback_status,
            snapshot.duration_us,
            snapshot.rate,
            clock_discipline,
            snapshot.audio_output,
            snapshot.lyrics_status,
            snapshot.lyrics_source,
            snapshot.lyrics_provenance,
            snapshot.lyrics_match_confidence,
            snapshot.lyric_document_id,
            snapshot.lyrics_timing_level,
            snapshot.lyrics_display_delay_us,
            snapshot.previous,
            snapshot.active,
            snapshot.next,
            snapshot.next_transition_monotonic_ns,
            snapshot.diagnostics,
        )
