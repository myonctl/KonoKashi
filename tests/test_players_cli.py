"""Diagnostic CLI tests using typed fixture-backed ports, never a live player."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from lyricflow import cli
from lyricflow.application.player_diagnostics import render_player_event
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
        *,
        close_error: RuntimeError | None = None,
    ) -> None:
        self.start_result = start_result or PlayerWatchStart()
        self.handler: Callable[[PlayerEvent], None] | None = None
        self.closed = False
        self.close_error = close_error

    def start(self, handler: Callable[[PlayerEvent], None]) -> PlayerWatchStart:
        self.handler = handler
        return self.start_result

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class FakeRuntime:
    def __init__(
        self,
        client: FakeClient,
        monitor: FakeMonitor | None = None,
        *,
        interrupt: bool = False,
        signal_interrupt: bool = False,
    ) -> None:
        self.client = client
        self.monitor = monitor or FakeMonitor()
        self.interrupt = interrupt
        self.signal_interrupt = signal_interrupt
        self.quit_called = False

    def exec(self) -> int:
        if self.interrupt:
            raise KeyboardInterrupt
        if self.signal_interrupt:
            import signal

            signal.raise_signal(signal.SIGINT)
            return 0
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
        {"Identity": "Firefox", "DesktopEntry": "firefox"},
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


def test_players_list_mixed_quality_is_success_and_keeps_diagnostics(
    capsys: pytest.CaptureFixture[str],
) -> None:
    partial, _ = browser_inspections()
    broken = PlayerInspection(
        "broken",
        full_service_name("broken"),
        failure=InspectionFailure.BUS_ERROR,
        message="no readable MPRIS properties",
    )
    runtime = FakeRuntime(FakeClient(PlayerListResult((broken, partial)), partial))

    exit_code = cli.main(["players", "list"], runtime_factory=lambda: runtime)
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "MPRIS players: 2" in output
    assert "broken" in output
    assert "unavailable: no readable MPRIS properties" in output
    assert "firefox.instance_1_95" in output
    assert "title:" in output


def test_players_inspect_partial_snapshot_is_success(
    capsys: pytest.CaptureFixture[str],
) -> None:
    partial, _ = browser_inspections()
    runtime = FakeRuntime(FakeClient(PlayerListResult((partial,)), partial))

    exit_code = cli.main(
        ["players", "inspect", partial.service_name],
        runtime_factory=lambda: runtime,
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "artists: <unavailable>" in output
    assert "diagnostics:" in output


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


def test_player_property_event_renders_changed_and_invalidated_values() -> None:
    snapshot = map_player_snapshot(
        full_service_name("strawberry"),
        {},
        {"Volume": 0.75},
    )
    event = PlayerEvent(
        PlayerEventKind.PROPERTIES_CHANGED,
        "strawberry",
        snapshot=snapshot,
        changed_properties=("Volume",),
        invalidated_properties=("CanSeek",),
    )

    assert render_player_event(event) == (
        "[properties changed] strawberry: Volume=0.75; CanSeek=<invalidated>"
    )


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


def test_players_watch_sigint_handler_quits_and_returns_130(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inspection = browser_inspections()[0]
    monitor = FakeMonitor()
    runtime = FakeRuntime(
        FakeClient(PlayerListResult((inspection,)), inspection),
        monitor,
        signal_interrupt=True,
    )

    exit_code = cli.main(["players", "watch"], runtime_factory=lambda: runtime)

    assert exit_code == 130
    assert runtime.quit_called
    assert monitor.closed
    assert "Watch stopped." in capsys.readouterr().out


def test_players_watch_broken_pipeline_does_not_become_python_exit_120(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenFinalOutput:
        def write(self, value: str) -> int:
            if "Watch stopped." in value:
                raise BrokenPipeError
            return len(value)

        def flush(self) -> None:
            pass

        def fileno(self) -> int:
            return 1

    inspection = browser_inspections()[0]
    runtime = FakeRuntime(
        FakeClient(PlayerListResult((inspection,)), inspection),
        FakeMonitor(),
        signal_interrupt=True,
    )
    duplicated: list[tuple[int, int]] = []
    monkeypatch.setattr(cli.sys, "stdout", BrokenFinalOutput())
    monkeypatch.setattr(cli.os, "open", lambda *_args: 99)
    monkeypatch.setattr(
        cli.os, "dup2", lambda source, target: duplicated.append((source, target))
    )
    monkeypatch.setattr(cli.os, "close", lambda _fd: None)

    exit_code = cli.main(["players", "watch"], runtime_factory=lambda: runtime)

    assert exit_code == 130
    assert duplicated == [(99, 1)]


def test_players_watch_cleanup_failure_is_controlled(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inspection = browser_inspections()[0]
    monitor = FakeMonitor(close_error=RuntimeError("disconnect rejected"))
    runtime = FakeRuntime(
        FakeClient(PlayerListResult((inspection,)), inspection),
        monitor,
    )

    exit_code = cli.main(["players", "watch"], runtime_factory=lambda: runtime)

    assert exit_code == 1
    assert monitor.closed
    assert "Unable to stop MPRIS watcher cleanly" in capsys.readouterr().err


def test_players_watch_interrupt_does_not_hide_cleanup_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inspection = browser_inspections()[0]
    monitor = FakeMonitor(close_error=RuntimeError("disconnect rejected"))
    runtime = FakeRuntime(
        FakeClient(PlayerListResult((inspection,)), inspection),
        monitor,
        signal_interrupt=True,
    )

    exit_code = cli.main(["players", "watch"], runtime_factory=lambda: runtime)

    assert exit_code == 1
    assert runtime.quit_called
    assert monitor.closed
    captured = capsys.readouterr()
    assert "Watch stopped." not in captured.out
    assert "Unable to stop MPRIS watcher cleanly" in captured.err


def test_players_watch_start_failure_closes_monitor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inspection = browser_inspections()[0]
    monitor = FakeMonitor(PlayerWatchStart(error="session bus unavailable"))
    runtime = FakeRuntime(
        FakeClient(PlayerListResult((inspection,)), inspection),
        monitor,
    )

    exit_code = cli.main(["players", "watch"], runtime_factory=lambda: runtime)

    assert exit_code == 1
    assert monitor.closed
    assert "Unable to watch MPRIS players" in capsys.readouterr().err


def test_players_command_reports_runtime_import_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unavailable_runtime() -> FakeRuntime:
        raise ImportError("PySide6 is unavailable")

    assert cli.main(["players", "list"], runtime_factory=unavailable_runtime) == 1
    assert "Unable to initialize MPRIS" in capsys.readouterr().err


def test_players_select_explains_resolution_and_suppressed_duplicate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = runtime_with_browsers()

    exit_code = cli.main(
        ["players", "select"],
        runtime_factory=lambda: runtime,
        database_path=tmp_path / "selection.sqlite3",
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "selected player: plasma-browser-integration" in output
    assert "raw title: S3RL feat. sara" in output
    assert "raw artist/uploader: Megacorp" in output
    assert "source kind: youtube" in output
    assert "source identity: kFqGyp60d8s" in output
    assert "resolved artist: S3RL feat. sara" in output
    assert "resolved title: Will to be" in output
    assert "duration: 311581000 us" in output
    assert "confidence: High" in output
    assert "selection reasons:" in output
    assert "transformations:" in output
    assert "suppressed duplicates:" in output
    assert "firefox.instance_1_95" in output
    assert "lower selection rank/metadata quality" in output


def test_players_select_supports_preferred_and_ignored_configuration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = runtime_with_browsers()

    exit_code = cli.main(
        [
            "players",
            "select",
            "--ignore",
            "plasma-browser-integration",
            "--prefer",
            "firefox.instance_1_95",
        ],
        runtime_factory=lambda: runtime,
        database_path=tmp_path / "selection.sqlite3",
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "selected player: firefox.instance_1_95" in output
    assert "ignored by player configuration" in output
    assert "+ configured preferred player" in output


def test_players_select_approved_correction_survives_fresh_cli_objects(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "durable selection.sqlite3"

    assert (
        cli.main(
            [
                "players",
                "select",
                "--approve-title",
                "Will to Be (User Radio Edit)",
                "--approve-artist",
                "S3RL feat. sara 日本語",
            ],
            runtime_factory=runtime_with_browsers,
            database_path=path,
        )
        == 0
    )
    saved = capsys.readouterr().out
    assert "Saved user-approved correction." in saved
    assert "confidence: Approved" in saved

    assert (
        cli.main(
            ["players", "select"],
            runtime_factory=runtime_with_browsers,
            database_path=path,
        )
        == 0
    )
    restarted = capsys.readouterr().out
    assert "raw title: S3RL feat. sara" in restarted
    assert "resolved artist: S3RL feat. sara 日本語" in restarted
    assert "resolved title: Will to Be (User Radio Edit)" in restarted
    assert "confidence: Approved" in restarted
    assert "automatic artist: S3RL feat. sara" in restarted
    assert "automatic title: Will to be" in restarted
    assert "automatic confidence: High" in restarted
    assert "user-approved correction" in restarted

    assert (
        cli.main(
            ["players", "select", "--reset-override"],
            runtime_factory=runtime_with_browsers,
            database_path=path,
        )
        == 0
    )
    reset = capsys.readouterr().out
    assert "Removed user-approved correction." in reset
    assert "confidence: High" in reset


def test_players_select_uses_durable_player_settings_by_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "player settings.sqlite3"
    assert (
        cli.main(
            [
                "storage",
                "settings",
                "set",
                "--ignore",
                "firefox",
                "--ignore",
                "LF_STAGE3_TEMP_IGNORED_PLAYER",
                "--prefer",
                "plasma-browser-integration",
            ],
            database_path=path,
        )
        == 0
    )
    capsys.readouterr()

    assert (
        cli.main(
            ["players", "select"],
            runtime_factory=runtime_with_browsers,
            database_path=path,
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "selected player: plasma-browser-integration" in output
    assert "firefox.instance_1_95: ignored by player configuration" in output
    assert "ignored by player configuration" in output
    assert "+ configured preferred player" in output
