"""Typed helpers for Stage 2 tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from lyricflow.application.ports import LocalPathResolution
from lyricflow.application.resolve_track import TrackResolver
from lyricflow.application.select_player import PlayerSelectionService
from lyricflow.application.source_identity import SourceIdentityResolver
from lyricflow.domain.models import PlayerInspection, PlayerListResult, PlayerSnapshot
from lyricflow.infrastructure.mpris.metadata_mapper import (
    full_service_name,
    map_player_snapshot,
)
from lyricflow.infrastructure.storage.track_overrides import (
    InMemoryTrackOverrideRepository,
)

FIXTURES = Path(__file__).parent / "fixtures"


class PredictableLocalPaths:
    """Filesystem-free canonicalizer that records decoded paths."""

    def __init__(self, *, exists: bool | None = True) -> None:
        self.exists = exists
        self.seen: list[str] = []

    def canonicalize(self, path: str) -> LocalPathResolution:
        self.seen.append(path)
        return LocalPathResolution(path, self.exists, False)


def resolver(
    paths: PredictableLocalPaths | None = None,
) -> tuple[TrackResolver, InMemoryTrackOverrideRepository]:
    repository = InMemoryTrackOverrideRepository()
    return (
        TrackResolver(
            SourceIdentityResolver(paths or PredictableLocalPaths()), repository
        ),
        repository,
    )


def selection_service() -> PlayerSelectionService:
    track_resolver, _repository = resolver()
    return PlayerSelectionService(track_resolver)


def snapshot(
    service: str,
    *,
    identity: str | None = None,
    desktop_entry: str | None = None,
    status: str = "Playing",
    title: str | None = "Track",
    artists: tuple[str, ...] | None = ("Artist",),
    url: str | None = None,
    duration_us: int | None = 180_000_000,
    position_us: int | None = 10_000_000,
) -> PlayerSnapshot:
    metadata: dict[str, object] = {}
    if title is not None:
        metadata["xesam:title"] = title
    if artists is not None:
        metadata["xesam:artist"] = list(artists)
    if url is not None:
        metadata["xesam:url"] = url
    if duration_us is not None:
        metadata["mpris:length"] = duration_us
    player: dict[str, object] = {"PlaybackStatus": status, "Metadata": metadata}
    if position_us is not None:
        player["Position"] = position_us
    return map_player_snapshot(
        full_service_name(service),
        {
            "Identity": service if identity is None else identity,
            "DesktopEntry": (
                service.split(".", 1)[0] if desktop_entry is None else desktop_entry
            ),
        },
        player,
    )


def inspection(value: PlayerSnapshot) -> PlayerInspection:
    return PlayerInspection(value.service_name, value.bus_name, snapshot=value)


def player_list(*snapshots: PlayerSnapshot) -> PlayerListResult:
    return PlayerListResult(tuple(inspection(value) for value in snapshots))


def fixture_snapshot(relative_path: str) -> PlayerSnapshot:
    fixture = json.loads((FIXTURES / relative_path).read_text(encoding="utf-8"))
    root = fixture["root"]
    player = fixture["player"]
    assert isinstance(root, Mapping)
    assert isinstance(player, Mapping)
    return map_player_snapshot(full_service_name(str(fixture["service"])), root, player)
