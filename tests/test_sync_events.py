"""Stable current-lyrics event schema and semantic emission tests."""

from __future__ import annotations

import json
from dataclasses import replace

from konokashi.application.sync_events import (
    CURRENT_LYRICS_EVENT_SCHEMA,
    CurrentLyricsEventEncoder,
    active_timing_segment,
    render_current_lyrics_jsonl,
)
from konokashi.application.sync_state import SynchronizedTimingSegment
from tests.test_desktop_state import _snapshot, _track


def test_jsonl_schema_contains_generic_track_line_source_and_timestamps() -> None:
    snapshot = _snapshot(_track("xa4WrgqI7q0", "Synthetic track"), 4)

    rendered = render_current_lyrics_jsonl(snapshot, sequence=1)
    payload = json.loads(rendered)

    assert "\n" not in rendered
    assert payload["schema"] == CURRENT_LYRICS_EVENT_SCHEMA
    assert payload["version"] == 1
    assert payload["event"] == "state"
    assert payload["sequence"] == 1
    assert payload["generation"] == 4
    assert payload["track"] == {
        "album": "Album",
        "artists": ["Artist"],
        "duration_us": 5_000_000,
        "title": "Synthetic track",
    }
    assert payload["playback"]["status"] == "Playing"
    assert payload["lyrics"]["source"] == "fixture"
    assert payload["lyrics"]["provenance"] == "provider"
    assert payload["active_lines"][0]["text"] == "second A"
    assert payload["active_lines"][0]["effective_transition_us"] == 2_000_000
    assert payload["next_line"]["text"] == "third"
    assert "source_identity" not in rendered
    assert "/home/" not in rendered


def test_encoder_suppresses_position_ticks_but_emits_line_word_and_state_changes() -> (
    None
):
    snapshot = _snapshot(_track("xa4WrgqI7q0", "Track"), 1)
    segment = SynchronizedTimingSegment(
        "word-1",
        "two",
        "word",
        2_000_000,
        2_500_000,
        2_000_000,
        2_500_000,
        "provider",
        highlight_fraction=0.1,
    )
    second_segment = replace(
        segment,
        segment_id="word-2",
        text=" words",
        source_start_us=2_300_000,
        source_end_us=2_500_000,
        effective_start_us=2_300_000,
        highlight_fraction=0.0,
    )
    rich = replace(
        snapshot,
        active=(
            replace(snapshot.active[0], timing_segments=(segment, second_segment)),
        ),
    )
    encoder = CurrentLyricsEventEncoder()

    first = encoder.encode(rich)
    tick = encoder.encode(
        replace(
            rich,
            disciplined_player_position_us=rich.disciplined_player_position_us + 10,
            observed_monotonic_us=rich.observed_monotonic_us + 10,
            next_transition_monotonic_ns=(
                None
                if rich.next_transition_monotonic_ns is None
                else rich.next_transition_monotonic_ns + 10_000
            ),
            active=(
                replace(
                    rich.active[0],
                    timing_segments=(
                        replace(segment, highlight_fraction=0.2),
                        second_segment,
                    ),
                ),
            ),
        )
    )
    word_advanced = replace(
        rich,
        disciplined_player_position_us=2_350_000,
        estimated_audible_position_us=2_350_000,
        active=(
            replace(
                rich.active[0],
                timing_segments=(
                    replace(segment, highlight_fraction=1.0),
                    replace(second_segment, highlight_fraction=0.25),
                ),
            ),
        ),
    )
    next_word = encoder.encode(word_advanced)
    paused = encoder.encode(
        replace(word_advanced, playback_status=rich.playback_status.PAUSED)
    )

    assert first is not None
    first_payload = json.loads(first)
    assert first_payload["sequence"] == 1
    assert first_payload["active_word"] == {
        "effective_end_us": 2_500_000,
        "effective_start_us": 2_000_000,
        "line_id": rich.active[0].line_id,
        "provenance": "provider",
        "segment_id": "word-1",
        "source_end_us": 2_500_000,
        "source_start_us": 2_000_000,
        "text": "two",
        "unit": "word",
    }
    assert tick is None
    assert next_word is not None
    assert json.loads(next_word)["active_word"]["segment_id"] == "word-2"
    assert paused is not None and json.loads(paused)["sequence"] == 3
    assert active_timing_segment(rich) == (rich.active[0].line_id, segment)


def test_jsonl_rejects_nonpositive_sequence() -> None:
    snapshot = _snapshot(_track("xa4WrgqI7q0", "Track"), 1)

    try:
        render_current_lyrics_jsonl(snapshot, sequence=0)
    except ValueError as error:
        assert "positive" in str(error)
    else:
        raise AssertionError("nonpositive sequence was accepted")
