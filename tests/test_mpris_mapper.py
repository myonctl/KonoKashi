"""Fixture and defensive-type tests for raw MPRIS property mapping."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from konokashi.infrastructure.mpris.metadata_mapper import (
    full_service_name,
    map_player_snapshot,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mpris"


def load_fixture(name: str) -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8")),
    )


@pytest.mark.parametrize(
    ("fixture_name", "expected_service"),
    [
        ("firefox_native.json", "firefox.instance_1_95"),
        ("plasma_browser_integration.json", "plasma-browser-integration"),
    ],
)
def test_observed_browser_fixtures_preserve_raw_values(
    fixture_name: str, expected_service: str
) -> None:
    fixture = load_fixture(fixture_name)
    bus_name = full_service_name(cast(str, fixture["service"]))
    snapshot = map_player_snapshot(
        bus_name,
        cast(dict[str, object], fixture["root"]),
        cast(dict[str, object], fixture["player"]),
    )

    assert snapshot.service_name == expected_service
    assert snapshot.playback_status == "Playing"
    assert snapshot.metadata.url == (
        "https://www.youtube.com/watch?v=kFqGyp60d8s&list=RD6YdpyClplBk&index=3"
    )

    if fixture_name == "firefox_native.json":
        assert snapshot.identity == "firefox"
        assert snapshot.metadata.artists is None
        assert snapshot.metadata.album is None
        assert snapshot.position_us is None
        assert snapshot.metadata.duration_us is None
        assert snapshot.metadata.title is not None
        assert snapshot.metadata.title.endswith(" - YouTube")
    else:
        assert snapshot.metadata.artists == ("Megacorp",)
        assert snapshot.position_us == 252940632
        assert snapshot.metadata.duration_us == 311581000
        assert snapshot.metadata.title == (
            'S3RL feat. sara - "Will to be" - Megacorp Theme Song (Full MTV)'
        )


def test_unicode_metadata_and_empty_artist_array_are_preserved() -> None:
    snapshot = map_player_snapshot(
        full_service_name("unicode-player"),
        {"Identity": "音楽プレーヤー"},
        {
            "PlaybackStatus": "Paused",
            "Metadata": {
                "xesam:title": "夜に駆ける 🌙",
                "xesam:artist": [],
                "xesam:album": "世界",
            },
        },
    )

    assert snapshot.identity == "音楽プレーヤー"
    assert snapshot.metadata.title == "夜に駆ける 🌙"
    assert snapshot.metadata.artists == ()
    assert snapshot.metadata.album == "世界"
    assert snapshot.metadata.duration_us is None
    assert snapshot.metadata.url is None


def test_wrongly_typed_properties_become_diagnostics_not_crashes() -> None:
    snapshot = map_player_snapshot(
        full_service_name("malformed"),
        {"Identity": 42, "CanQuit": "yes", "SupportedUriSchemes": "file"},
        {
            "PlaybackStatus": 7,
            "Position": True,
            "Rate": 1,
            "Metadata": {
                "xesam:title": ["not", "a", "title"],
                "xesam:artist": ["valid", 9],
                "xesam:url": 123,
                "mpris:length": "311581000",
            },
        },
    )

    assert snapshot.identity is None
    assert snapshot.playback_status is None
    assert snapshot.position_us is None
    assert snapshot.metadata.title is None
    assert snapshot.metadata.artists is None
    assert snapshot.metadata.url is None
    assert snapshot.metadata.duration_us is None
    assert len(snapshot.diagnostics) == 10
    assert any("expected integer microseconds" in item for item in snapshot.diagnostics)


def test_unexpected_string_status_and_negative_timings_are_preserved() -> None:
    snapshot = map_player_snapshot(
        full_service_name("odd-player"),
        {},
        {
            "PlaybackStatus": "Buffering",
            "Position": -5,
            "Metadata": {"mpris:length": -1},
        },
    )

    assert snapshot.playback_status == "Buffering"
    assert snapshot.position_us == -5
    assert snapshot.metadata.duration_us == -1
    assert len(snapshot.diagnostics) == 3
