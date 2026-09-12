"""Stable privacy-deliberate events projected from shared synchronization state."""

from __future__ import annotations

import json
from typing import TypeAlias

from konokashi.application.sync_state import (
    SynchronizationSnapshot,
    SynchronizedLine,
    SynchronizedRepresentationLayer,
    SynchronizedTimingSegment,
)

CURRENT_LYRICS_EVENT_SCHEMA = "io.github.myonctl.konokashi.current-lyrics"
CURRENT_LYRICS_EVENT_VERSION = 1

JsonValue: TypeAlias = (
    bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"] | None
)


class CurrentLyricsEventEncoder:
    """Emit versioned JSONL only when externally meaningful state changes."""

    def __init__(self) -> None:
        self._sequence = 0
        self._last_key: tuple[object, ...] | None = None

    @property
    def sequence(self) -> int:
        return self._sequence

    def encode(self, snapshot: SynchronizationSnapshot) -> str | None:
        """Return one record for a semantic transition, otherwise no record."""

        key = _event_key(snapshot)
        if key == self._last_key:
            return None
        self._last_key = key
        self._sequence += 1
        return render_current_lyrics_jsonl(snapshot, sequence=self._sequence)


def active_timing_segment(
    snapshot: SynchronizationSnapshot,
) -> tuple[str, SynchronizedTimingSegment] | None:
    """Return the latest trusted segment reached at the audible display position."""

    position_us = (
        snapshot.disciplined_player_position_us
        if snapshot.estimated_audible_position_us is None
        else snapshot.estimated_audible_position_us
    )
    reached = tuple(
        (line.line_id, segment)
        for line in snapshot.active
        for segment in line.timing_segments
        if segment.highlight_fraction is not None
        and segment.effective_start_us <= position_us
    )
    if not reached:
        return None
    return max(
        reached,
        key=lambda item: (item[1].effective_start_us, item[1].segment_id),
    )


def current_lyrics_event(
    snapshot: SynchronizationSnapshot, *, sequence: int
) -> dict[str, JsonValue]:
    """Build versioned generic state without leaking local source identities."""

    if sequence < 1:
        raise ValueError("current-lyrics event sequence must be positive")
    active_word = active_timing_segment(snapshot)
    return {
        "schema": CURRENT_LYRICS_EVENT_SCHEMA,
        "version": CURRENT_LYRICS_EVENT_VERSION,
        "event": "state",
        "sequence": sequence,
        "generation": snapshot.generation,
        "observed_monotonic_us": snapshot.observed_monotonic_us,
        "track": {
            "title": snapshot.track_title,
            "artists": list(snapshot.artists),
            "album": snapshot.album,
            "duration_us": snapshot.duration_us,
        },
        "playback": {
            "status": snapshot.playback_status.value,
            "position_us": snapshot.disciplined_player_position_us,
            "audible_position_us": snapshot.estimated_audible_position_us,
            "rate": snapshot.rate,
        },
        "lyrics": {
            "status": snapshot.lyrics_status,
            "document_id": snapshot.lyric_document_id,
            "source": snapshot.lyrics_source,
            "provenance": snapshot.lyrics_provenance,
            "match_confidence": snapshot.lyrics_match_confidence,
            "timing_level": snapshot.lyrics_timing_level,
            "display_delay_us": snapshot.lyrics_display_delay_us,
        },
        "active_lines": [_line_value(line) for line in snapshot.active],
        "active_word": (
            None
            if active_word is None
            else {
                "line_id": active_word[0],
                **_segment_value(active_word[1]),
            }
        ),
        "next_line": None if not snapshot.next else _line_value(snapshot.next[0]),
        "next_transition_monotonic_us": (
            None
            if snapshot.next_transition_monotonic_ns is None
            else snapshot.next_transition_monotonic_ns // 1_000
        ),
    }


def render_current_lyrics_jsonl(
    snapshot: SynchronizationSnapshot, *, sequence: int
) -> str:
    """Render exactly one compact, deterministic UTF-8 JSON Lines record."""

    return json.dumps(
        current_lyrics_event(snapshot, sequence=sequence),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _line_value(line: SynchronizedLine) -> dict[str, JsonValue]:
    return {
        "line_id": line.line_id,
        "index": line.original_index,
        "text": line.original,
        "source_timestamp_us": line.source_timestamp_us,
        "effective_transition_us": line.effective_transition_us,
        "reading": _layer_value(line.reading),
        "translation": _layer_value(line.translated),
    }


def _layer_value(
    layer: SynchronizedRepresentationLayer | None,
) -> dict[str, JsonValue] | None:
    if layer is None:
        return None
    return {
        "kind": layer.kind,
        "text": layer.text,
        "provenance": layer.provenance,
        "approval_state": layer.approval_state,
        "source": layer.source_name,
        "source_version": layer.source_version,
        "language": layer.language,
        "script": layer.script,
        "uncertainty": layer.uncertainty,
    }


def _segment_value(segment: SynchronizedTimingSegment) -> dict[str, JsonValue]:
    return {
        "segment_id": segment.segment_id,
        "text": segment.text,
        "unit": segment.unit,
        "source_start_us": segment.source_start_us,
        "source_end_us": segment.source_end_us,
        "effective_start_us": segment.effective_start_us,
        "effective_end_us": segment.effective_end_us,
        "provenance": segment.timing_provenance,
    }


def _event_key(snapshot: SynchronizationSnapshot) -> tuple[object, ...]:
    active_word = active_timing_segment(snapshot)

    def line_key(line: SynchronizedLine) -> tuple[object, ...]:
        return (
            line.line_id,
            line.original_index,
            line.source_timestamp_us,
            line.effective_transition_us,
            line.original,
            line.reading,
            line.translated,
        )

    return (
        snapshot.generation,
        snapshot.track_title,
        snapshot.artists,
        snapshot.album,
        snapshot.duration_us,
        snapshot.playback_status,
        snapshot.rate,
        snapshot.lyrics_status,
        snapshot.lyrics_source,
        snapshot.lyrics_provenance,
        snapshot.lyrics_match_confidence,
        snapshot.lyric_document_id,
        snapshot.lyrics_timing_level,
        snapshot.lyrics_display_delay_us,
        tuple(line_key(line) for line in snapshot.active),
        None if active_word is None else (active_word[0], active_word[1].segment_id),
        None if not snapshot.next else line_key(snapshot.next[0]),
    )
