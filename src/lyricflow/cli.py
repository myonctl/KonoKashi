"""Command-line entry point for local and MPRIS diagnostics."""

from __future__ import annotations

import argparse
import os
import signal
import sys
from collections.abc import Callable, Sequence
from types import FrameType
from typing import TypeAlias

from lyricflow import __version__
from lyricflow.application.diagnostics import build_doctor_report
from lyricflow.application.player_diagnostics import (
    render_player_event,
    render_player_inspection,
    render_player_list,
)
from lyricflow.application.ports import MprisRuntimePort
from lyricflow.infrastructure.diagnostics import collect_local_diagnostics

RuntimeFactory: TypeAlias = Callable[[], MprisRuntimePort]


def _silence_broken_stdout() -> None:
    """Prevent CPython's shutdown flush from replacing an intentional exit code."""

    try:
        stdout_fd = sys.stdout.fileno()
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull_fd, stdout_fd)
        finally:
            os.close(devnull_fd)
    except (AttributeError, OSError, ValueError):
        pass


def _create_runtime() -> MprisRuntimePort:
    """Import QtDBus lazily so ``doctor`` can diagnose a missing Qt install."""

    from lyricflow.infrastructure.mpris.qt_dbus_client import (
        create_qt_mpris_runtime,
    )

    return create_qt_mpris_runtime()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lyricflow",
        description="Local-first synchronized lyrics for Linux MPRIS players.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "doctor",
        help="check local platform and desktop prerequisites",
    )
    players = subparsers.add_parser(
        "players",
        help="inspect MPRIS players on the session D-Bus",
    )
    player_commands = players.add_subparsers(
        dest="players_command",
        required=True,
    )
    player_commands.add_parser("list", help="list every current MPRIS player")
    inspect_parser = player_commands.add_parser(
        "inspect",
        help="inspect one MPRIS service",
    )
    inspect_parser.add_argument(
        "service",
        help="service suffix or full org.mpris.MediaPlayer2.* bus name",
    )
    player_commands.add_parser(
        "watch",
        help="watch MPRIS lifecycle, property, and seek events",
    )
    return parser


def _run_watch(runtime: MprisRuntimePort) -> int:
    def show_event(event: object) -> None:
        from lyricflow.domain.models import PlayerEvent

        if not isinstance(event, PlayerEvent):
            raise TypeError("player monitor emitted a non-PlayerEvent value")
        print(render_player_event(event), flush=True)

    start = runtime.monitor.start(show_event)
    if start.error is not None:
        runtime.monitor.close()
        print(f"Unable to watch MPRIS players: {start.error}", file=sys.stderr)
        return 1

    current = ", ".join(start.current_services) if start.current_services else "none"
    print("Watching MPRIS events. Press Ctrl+C to stop.", flush=True)
    print(f"Current players: {current}", flush=True)
    interrupted = False
    previous_handler = signal.getsignal(signal.SIGINT)

    def stop(_signum: int, _frame: FrameType | None) -> None:
        nonlocal interrupted
        interrupted = True
        runtime.quit()

    signal.signal(signal.SIGINT, stop)
    cleanup_error: RuntimeError | None = None
    try:
        exit_code = runtime.exec()
    except KeyboardInterrupt:
        interrupted = True
        runtime.quit()
        exit_code = 0
    finally:
        try:
            runtime.monitor.close()
        except RuntimeError as error:
            cleanup_error = error
        signal.signal(signal.SIGINT, previous_handler)
    if cleanup_error is not None:
        print(f"Unable to stop MPRIS watcher cleanly: {cleanup_error}", file=sys.stderr)
        return 1
    if interrupted:
        try:
            print("Watch stopped.", flush=True)
        except BrokenPipeError:
            _silence_broken_stdout()
        return 130
    return exit_code


def _run_players(arguments: argparse.Namespace, runtime_factory: RuntimeFactory) -> int:
    try:
        runtime = runtime_factory()
    except (ImportError, RuntimeError) as error:
        print(f"Unable to initialize MPRIS: {error}", file=sys.stderr)
        return 1

    if arguments.players_command == "list":
        result = runtime.client.list_players()
        print(render_player_list(result))
        return int(result.error is not None)
    if arguments.players_command == "inspect":
        inspection = runtime.client.inspect_player(arguments.service)
        print(render_player_inspection(inspection))
        return int(not inspection.succeeded)
    if arguments.players_command == "watch":
        return _run_watch(runtime)
    return 2


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime_factory: RuntimeFactory = _create_runtime,
) -> int:
    """Run the CLI and return a process exit code."""

    arguments = _parser().parse_args(argv)
    if arguments.command == "doctor":
        report = build_doctor_report(collect_local_diagnostics())
        print(report.render())
        return report.exit_code
    if arguments.command == "players":
        return _run_players(arguments, runtime_factory)
    return 2
