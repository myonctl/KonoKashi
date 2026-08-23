"""Presentation-neutral synchronization snapshots and meaningful subscriptions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from lyricflow.application.lyrics_sync import SynchronizationFrame
from lyricflow.application.representations import original_lines
from lyricflow.domain.identity import SourceIdentity
from lyricflow.domain.lyrics import LyricDocument, LyricLine, RepresentationKind
from lyricflow.domain.representations import EffectiveRepresentationLine
from lyricflow.domain.synchronization import (
    AudioOutputLatency,
    PlaybackClockDiagnostics,
    PlaybackPositionEstimate,
    PlaybackState,
    SynchronizationCalibration,
)
from lyricflow.domain.tracks import ResolvedTrack


@dataclass(frozen=True, slots=True)
class SynchronizedLine:
    """One original timing identity with every selected textual layer."""

    line_id: str
    original_index: int
    source_timestamp_us: int | None
    effective_transition_us: int | None
    original: str
    romanized_or_transliterated: str | None = None
    translation: str | None = None
    representation_provenance: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SynchronizationSnapshot:
    """Complete app-level state suitable for desktop, TUI, web, or diagnostics."""

    generation: int
    source_identity: SourceIdentity
    player_service: str
    player_identity: str | None
    track_title: str | None
    artists: tuple[str, ...]
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
    lyrics_match_confidence: str | None
    lyric_document_id: str
    lyrics_display_delay_us: int
    previous: tuple[SynchronizedLine, ...]
    active: tuple[SynchronizedLine, ...]
    next: tuple[SynchronizedLine, ...]
    next_transition_monotonic_ns: int | None
    time_until_next_transition_us: int | None
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
    representations: Mapping[
        tuple[str, RepresentationKind], EffectiveRepresentationLine
    ],
) -> SynchronizedLine:
    romanized = representations.get((line.line_id, RepresentationKind.ROMANIZED))
    transliterated = representations.get(
        (line.line_id, RepresentationKind.TRANSLITERATED)
    )
    translation = representations.get((line.line_id, RepresentationKind.TRANSLATED))
    alternate = romanized if romanized is not None else transliterated
    selected = tuple(
        item
        for item in (alternate, translation)
        if item is not None and item.text is not None
    )
    source_us = None if line.start_ms is None else line.start_ms * 1_000
    effective_us = (
        None if source_us is None else source_us + document_delay_us + line_shift_us
    )
    return SynchronizedLine(
        line.line_id,
        original_index,
        source_us,
        effective_us,
        line.text,
        None if alternate is None else alternate.text,
        None if translation is None else translation.text,
        tuple(
            "unknown" if item.provenance is None else item.provenance.value
            for item in selected
        ),
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
    lyrics_match_confidence: str | None = None,
) -> SynchronizationSnapshot:
    """Build a framework/storage/provider-neutral frontend state value."""

    by_line_kind = {
        (item.original_line.line_id, item.kind): item for item in representations
    }
    original_indexes = {
        line.line_id: index for index, line in enumerate(original_lines(document))
    }
    shifts = dict(
        (*frame.lyrics.active_line_shifts_us, *frame.lyrics.next_line_shifts_us)
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
    return SynchronizationSnapshot(
        generation=generation,
        source_identity=track.source_identity,
        player_service=snapshot.service_name,
        player_identity=snapshot.identity,
        track_title=track.candidate.title,
        artists=track.candidate.artists,
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
        lyrics_match_confidence=lyrics_match_confidence,
        lyric_document_id=document.document_id,
        lyrics_display_delay_us=calibration.lyrics.shift_us,
        previous=bundle(frame.lyrics.previous),
        active=bundle(frame.lyrics.active),
        next=bundle(frame.lyrics.next),
        next_transition_monotonic_ns=deadline_ns,
        time_until_next_transition_us=until_us,
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
            snapshot.playback_status,
            snapshot.duration_us,
            snapshot.rate,
            clock_discipline,
            snapshot.audio_output,
            snapshot.lyrics_status,
            snapshot.lyrics_source,
            snapshot.lyrics_match_confidence,
            snapshot.lyric_document_id,
            snapshot.lyrics_display_delay_us,
            snapshot.previous,
            snapshot.active,
            snapshot.next,
            snapshot.next_transition_monotonic_ns,
            snapshot.diagnostics,
        )
