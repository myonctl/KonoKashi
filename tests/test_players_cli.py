"""Diagnostic CLI tests using typed fixture-backed ports, never a live player."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from lyricflow import cli
from lyricflow.domain.models import (
    InspectionFailure,
    PlayerEvent,
    PlayerEventKind,
    PlayerInspection,
    PlayerListResult,
    PlayerWatchStart,
)
from lyricflow.infrastructure.mpris.metadata_mapper import (
    full_service_name,
    map_player_snapshot,
)


class FakeClient:
    def __init__(
        self,
        list_result: PlayerListResult,
        inspection: PlayerInspection,
    ) -> None:
        self.list_result = list_result
        self.inspection = inspection
        self.inspected_service: str | None = None

    def list_players(self) -> PlayerListResult:
        return self.list_result

    def inspect_player(self, service_name: str) -> PlayerInspection:
        self.inspected_service = service_name
        return self.inspection


class FakeMonitor:
    def __init__(
        self,
        start_result: PlayerWatchStart | None = None,
    ) -> None:
        self.start_result = start_result or PlayerWatchStart()
        self.handler: Callable[[PlayerEvent], None] | None = None
        self.closed = False

    def start(self, handler: Callable[[PlayerEvent], None]) -> PlayerWatchStart:
        self.handler = handler
        return self.start_result

    def close(self) -> None:
        self.closed = True


class FakeRuntime:
    def __init__(
        self,
        client: FakeClient,
        monitor: FakeMonitor | None = None,
        *,
        interrupt: bool = False,
    ) -> None:
        self.client = client
        self.monitor = monitor or FakeMonitor()
        self.interrupt = interrupt
        self.quit_called = False

    def exec(self) -> int:
        if self.interrupt:
            raise KeyboardInterrupt
        if self.monitor.handler is not None:
            self.monitor.handler(
                PlayerEvent(
                    PlayerEventKind.PLAYBACK_STATUS_CHANGED,
                    "strawberry",
                    playback_status="Paused",
                )
            )
        return 0

    def quit(self) -> None:
        self.quit_called = True


def browser_inspections() -> tuple[PlayerInspection, PlayerInspection]:
    firefox_bus = full_service_name("firefox.instance_1_95")
    firefox = map_player_snapshot(
        firefox_bus,
        {"Identity": "firefox"},
        {
            "PlaybackStatus": "Playing",
            "Metadata": {
                "xesam:title": (
                    'S3RL feat. sara - "Will to be" - Megacorp Theme Song '
                    "(Full MTV) - YouTube"
                ),
                "xesam:url": (
                    "https://www.youtube.com/watch?v=kFqGyp60d8s&"
                    "list=RD6YdpyClplBk&index=3"
                ),
            },
        },
        ("Position: unavailable (property is not available)",),
    )
    plasma_bus = full_service_name("plasma-browser-integration")
    plasma = map_player_snapshot(
        plasma_bus,
        {"Identity": "plasma-browser-integration"},
        {
            "PlaybackStatus": "Playing",
            "Position": 252940632,
            "Metadata": {
                "xesam:title": (
                    'S3RL feat. sara - "Will to be" - Megacorp Theme Song (Full MTV)'
                ),
                "xesam:artist": ["Megacorp"],
                "xesam:url": (
                    "https://www.youtube.com/watch?v=kFqGyp60d8s&"
                    "list=RD6YdpyClplBk&index=3"
                ),
                "mpris:length": 311581000,
            },
        },
    )
    return (
        PlayerInspection("firefox.instance_1_95", firefox_bus, snapshot=firefox),
        PlayerInspection("plasma-browser-integration", plasma_bus, snapshot=plasma),
    )


def runtime_with_browsers() -> FakeRuntime:
    firefox, plasma = browser_inspections()
    return FakeRuntime(FakeClient(PlayerListResult((firefox, plasma)), plasma))


def test_players_list_shows_both_browser_services_without_suppression(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = runtime_with_browsers()

    exit_code = cli.main(
        ["players", "list"],
        runtime_factory=lambda: runtime,
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "MPRIS players: 2" in output
    assert "firefox.instance_1_95" in output
    assert "plasma-browser-integration" in output
    assert "(Full MTV) - YouTube" in output
    assert "artists: <unavailable>" in output
    assert "artists: Megacorp" in output
    assert "duration: 311581000 us" in output
    assert "position: 252940632 us" in output


def test_players_inspect_accepts_short_service_and_renders_typed_raw_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = runtime_with_browsers()

    exit_code = cli.main(
        ["players", "inspect", "plasma-browser-integration"],
        runtime_factory=lambda: runtime,
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert runtime.client.inspected_service == "plasma-browser-integration"
    assert "MPRIS player inspection" in output
    assert "position: 252940632 us" in output
    assert "duration: 311581000 us" in output
    assert "artists: Megacorp" in output
    assert "QDBusVariant" not in output


def test_players_list_zero_players_is_success(
    capsys: pytest.CaptureFixture[str],
) -> None:
    unavailable = PlayerInspection(
        "unused",
        full_service_name("unused"),
        failure=InspectionFailure.UNAVAILABLE,
        message="unused",
    )
    runtime = FakeRuntime(FakeClient(PlayerListResult(), unavailable))

    assert cli.main(["players", "list"], runtime_factory=lambda: runtime) == 0
    assert capsys.readouterr().out == "No MPRIS players found.\n"


def test_players_inspect_expected_failure_has_nonzero_exit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inspection = PlayerInspection(
        "gone",
        full_service_name("gone"),
        failure=InspectionFailure.UNAVAILABLE,
        message="player disappeared during inspection",
    )
    runtime = FakeRuntime(FakeClient(PlayerListResult(), inspection))

    exit_code = cli.main(
        ["players", "inspect", "gone"], runtime_factory=lambda: runtime
    )

    assert exit_code == 1
    assert "status: unavailable" in capsys.readouterr().out


def test_players_watch_reports_initial_and_property_events_and_closes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inspection = browser_inspections()[0]
    monitor = FakeMonitor(PlayerWatchStart(("strawberry",)))
    runtime = FakeRuntime(
        FakeClient(PlayerListResult((inspection,)), inspection),
        monitor,
    )

    exit_code = cli.main(["players", "watch"], runtime_factory=lambda: runtime)
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Current players: strawberry" in output
    assert "[playback status changed] strawberry: Paused" in output
    assert monitor.closed


def test_players_watch_keyboard_interrupt_quits_and_returns_130(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inspection = browser_inspections()[0]
    monitor = FakeMonitor()
    runtime = FakeRuntime(
        FakeClient(PlayerListResult((inspection,)), inspection),
        monitor,
        interrupt=True,
    )

    exit_code = cli.main(["players", "watch"], runtime_factory=lambda: runtime)

    assert exit_code == 130
    assert runtime.quit_called
    assert monitor.closed
    assert "Watch stopped." in capsys.readouterr().out
