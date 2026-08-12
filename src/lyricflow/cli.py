"""Command-line entry point for local and MPRIS diagnostics."""

from __future__ import annotations

import argparse
import os
import signal
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
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
from lyricflow.application.selection_diagnostics import render_player_selection
from lyricflow.domain.tracks import PlayerSelectionConfig
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
    select_parser = player_commands.add_parser(
        "select",
        help="select and explain the current player and stable track identity",
    )
    select_parser.add_argument(
        "--prefer",
        action="append",
        default=None,
        metavar="PLAYER",
        help="prefer a player selector; bare names match service families (repeatable)",
    )
    select_parser.add_argument(
        "--ignore",
        action="append",
        default=None,
        metavar="PLAYER",
        help="ignore a player selector; bare names match service families (repeatable)",
    )
    select_parser.add_argument(
        "--approve-title",
        help="durably approve this title for the selected stable source",
    )
    select_parser.add_argument(
        "--approve-artist",
        action="append",
        default=None,
        help="durably approve an artist for the selected source (repeatable)",
    )
    select_parser.add_argument(
        "--approve-album",
        help="optionally approve an album with the title/artist correction",
    )
    select_parser.add_argument(
        "--reset-override",
        action="store_true",
        help="explicitly remove the selected source's approved correction",
    )

    storage = subparsers.add_parser(
        "storage", help="inspect and initialize durable local storage"
    )
    storage_commands = storage.add_subparsers(dest="storage_command", required=True)
    storage_commands.add_parser("status", help="inspect storage without modifying it")
    storage_commands.add_parser(
        "migrate", help="initialize or migrate storage without destructive reset"
    )
    settings = storage_commands.add_parser(
        "settings", help="inspect or update durable player settings"
    )
    settings_commands = settings.add_subparsers(dest="settings_command", required=True)
    settings_commands.add_parser("show", help="show durable preferred/ignored players")
    settings_set = settings_commands.add_parser(
        "set", help="atomically replace durable preferred/ignored players"
    )
    settings_set.add_argument(
        "--prefer",
        action="append",
        default=[],
        help="prefer a player selector; bare names match service families (repeatable)",
    )
    settings_set.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="ignore a player selector; bare names match service families (repeatable)",
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


def _run_players(
    arguments: argparse.Namespace,
    runtime_factory: RuntimeFactory,
    database_path: Path | None,
) -> int:
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
    if arguments.players_command == "select":
        from lyricflow.application.resolve_track import TrackResolver
        from lyricflow.application.select_player import PlayerSelectionService
        from lyricflow.application.source_identity import SourceIdentityResolver
        from lyricflow.domain.tracks import ApprovedTrackIdentity
        from lyricflow.infrastructure.metadata.local_paths import (
            FilesystemLocalPathCanonicalizer,
        )
        from lyricflow.infrastructure.storage.bootstrap import open_storage
        from lyricflow.infrastructure.storage.errors import StorageError

        try:
            storage = open_storage(database_path)
            persisted = storage.settings.get_player_selection()
            config = PlayerSelectionConfig(
                preferred_players=(
                    persisted.preferred_players
                    if arguments.prefer is None
                    else tuple(arguments.prefer)
                ),
                ignored_players=(
                    persisted.ignored_players
                    if arguments.ignore is None
                    else tuple(arguments.ignore)
                ),
            )
            service = PlayerSelectionService(
                TrackResolver(
                    SourceIdentityResolver(FilesystemLocalPathCanonicalizer()),
                    storage.track_overrides,
                )
            )
            players = runtime.client.list_players()
            selection = service.select(players, config)
            approving = any(
                value is not None
                for value in (
                    arguments.approve_title,
                    arguments.approve_artist,
                    arguments.approve_album,
                )
            )
            if arguments.reset_override and approving:
                print(
                    "Cannot approve and reset a correction in the same command.",
                    file=sys.stderr,
                )
                return 2
            if approving and (
                arguments.approve_title is None or not arguments.approve_artist
            ):
                print(
                    "Approval requires --approve-title and at least one "
                    "--approve-artist.",
                    file=sys.stderr,
                )
                return 2
            if approving or arguments.reset_override:
                if selection.selected is None:
                    print("No selected source is available to update.", file=sys.stderr)
                    return 1
                source_identity = selection.selected.track.source_identity
                if approving:
                    storage.track_overrides.put(
                        source_identity,
                        ApprovedTrackIdentity(
                            arguments.approve_title,
                            tuple(arguments.approve_artist),
                            arguments.approve_album,
                        ),
                    )
                    print("Saved user-approved correction.")
                else:
                    removed = storage.track_overrides.delete(source_identity)
                    print(
                        "Removed user-approved correction."
                        if removed
                        else "No user-approved correction existed."
                    )
                selection = service.select(players, config)
        except StorageError as error:
            print(f"Unable to use LyricFlow storage: {error}", file=sys.stderr)
            return 1
        print(render_player_selection(selection))
        return int(players.error is not None)
    return 2


def _run_storage(arguments: argparse.Namespace, database_path: Path | None) -> int:
    from lyricflow.application.storage_diagnostics import render_storage_status
    from lyricflow.infrastructure.storage.bootstrap import open_storage
    from lyricflow.infrastructure.storage.diagnostics import inspect_storage
    from lyricflow.infrastructure.storage.errors import StorageError
    from lyricflow.infrastructure.storage.paths import default_database_path

    path = database_path or default_database_path()
    if arguments.storage_command == "status":
        status = inspect_storage(path)
        print(render_storage_status(status))
        return status.exit_code
    try:
        storage = open_storage(path)
        if arguments.storage_command == "migrate":
            print(
                f"LyricFlow storage is current at schema version "
                f"{storage.database.migration_history()[-1][0]}."
            )
            print(f"database path: {path}")
            return 0
        if arguments.storage_command == "settings":
            if arguments.settings_command == "set":
                storage.settings.put_player_selection(
                    PlayerSelectionConfig(
                        tuple(arguments.prefer), tuple(arguments.ignore)
                    )
                )
                print("Saved durable player settings.")
            config = storage.settings.get_player_selection()
            preferred = ", ".join(config.preferred_players) or "none"
            ignored = ", ".join(config.ignored_players) or "none"
            print(f"preferred players: {preferred}")
            print(f"ignored players: {ignored}")
            return 0
    except StorageError as error:
        print(f"Unable to use LyricFlow storage: {error}", file=sys.stderr)
        return 1
    return 2


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime_factory: RuntimeFactory = _create_runtime,
    database_path: Path | None = None,
) -> int:
    """Run the CLI and return a process exit code."""

    arguments = _parser().parse_args(argv)
    if arguments.command == "doctor":
        report = build_doctor_report(collect_local_diagnostics())
        print(report.render())
        return report.exit_code
    if arguments.command == "players":
        return _run_players(arguments, runtime_factory, database_path)
    if arguments.command == "storage":
        return _run_storage(arguments, database_path)
    return 2
