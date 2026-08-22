"""Command-line entry point for local and MPRIS diagnostics."""

from __future__ import annotations

import argparse
import os
import signal
import sys
from collections.abc import Callable, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, TypeAlias

from lyricflow import __version__
from lyricflow.application.diagnostics import build_doctor_report
from lyricflow.application.player_diagnostics import (
    render_player_event,
    render_player_inspection,
    render_player_list,
)
from lyricflow.application.ports import (
    AudioLatencyProbePort,
    LyricsProviderPort,
    MprisRuntimePort,
)
from lyricflow.application.selection_diagnostics import render_player_selection
from lyricflow.domain.tracks import PlayerSelectionConfig
from lyricflow.infrastructure.diagnostics import collect_local_diagnostics

if TYPE_CHECKING:
    from lyricflow.domain.lyrics import LyricDocument
    from lyricflow.domain.representations import EffectiveRepresentationLine
    from lyricflow.domain.tracks import ResolvedTrack
    from lyricflow.infrastructure.storage.bootstrap import StorageRepositories

RuntimeFactory: TypeAlias = Callable[[], MprisRuntimePort]
LyricsProviderFactory: TypeAlias = Callable[[], LyricsProviderPort]
AudioLatencyProbeFactory: TypeAlias = Callable[[], AudioLatencyProbePort]


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


def _create_lyrics_provider() -> LyricsProviderPort:
    """Construct the network adapter lazily so offline diagnostics remain testable."""

    from lyricflow.infrastructure.lyrics.lrclib import LrclibLyricsProvider

    return LrclibLyricsProvider()


def _create_audio_latency_probe() -> AudioLatencyProbePort:
    """Construct the optional read-only PipeWire diagnostic adapter."""

    from lyricflow.infrastructure.audio.pipewire import PipeWireLatencyProbe

    return PipeWireLatencyProbe()


def _milliseconds(value: str, *, signed: bool) -> int:
    """Parse user calibration to integer microseconds without binary float."""

    normalized = value.strip()
    if normalized.lower().endswith("ms"):
        normalized = normalized[:-2].strip()
    try:
        parsed = Decimal(normalized)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("expected decimal milliseconds") from error
    if not parsed.is_finite():
        raise argparse.ArgumentTypeError("milliseconds must be finite")
    if not signed and parsed < 0:
        raise argparse.ArgumentTypeError("milliseconds cannot be negative")
    if abs(parsed) > Decimal(60_000):
        raise argparse.ArgumentTypeError("calibration cannot exceed 60000 ms")
    return int((parsed * 1_000).to_integral_value(rounding=ROUND_HALF_UP))


def _nonnegative_milliseconds(value: str) -> int:
    return _milliseconds(value, signed=False)


def _signed_milliseconds(value: str) -> int:
    return _milliseconds(value, signed=True)


def _sample_count(value: str) -> int:
    try:
        count = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("samples must be an integer") from error
    if count < 0:
        raise argparse.ArgumentTypeError("samples cannot be negative")
    return count


def _sample_interval(value: str) -> int:
    try:
        interval = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("interval must be an integer") from error
    if not 50 <= interval <= 5_000:
        raise argparse.ArgumentTypeError("interval must be between 50 and 5000 ms")
    return interval


def _probe_duration(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("duration must be whole seconds") from error
    if not 1 <= seconds <= 600:
        raise argparse.ArgumentTypeError("duration must be between 1 and 600 seconds")
    return seconds


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
    display = storage_commands.add_parser(
        "display", help="inspect or update multilingual lyric layer toggles"
    )
    display_commands = display.add_subparsers(dest="display_command", required=True)
    display_commands.add_parser("show", help="show durable lyric layer toggles")
    display_set = display_commands.add_parser(
        "set", help="update one or more durable lyric layer toggles"
    )
    for layer in ("original", "romanized", "translated"):
        display_set.add_argument(
            f"--{layer}",
            choices=("on", "off"),
            help=f"show or hide the {layer} layer",
        )
    lyrics = subparsers.add_parser(
        "lyrics", help="resolve lyrics for the currently selected MPRIS recording"
    )
    lyrics_commands = lyrics.add_subparsers(dest="lyrics_command", required=True)
    lyrics_current = lyrics_commands.add_parser(
        "current", help="resolve and explain current lyrics"
    )
    lyrics_current.add_argument(
        "--offline",
        action="store_true",
        help="use only exact local and persistent cache sources",
    )
    lyrics_current.add_argument(
        "--refresh",
        action="store_true",
        help="re-query the provider without deleting previous usable lyrics",
    )
    lyrics_current.add_argument(
        "--full",
        action="store_true",
        help="explicitly print all lyric lines instead of a three-line preview",
    )
    romanize = lyrics_commands.add_parser(
        "romanize", help="generate and persist offline representations"
    )
    romanize.add_argument("target", choices=("current",))
    romanize.add_argument("--offline", action="store_true")
    romanize.add_argument(
        "--language", help="explicit BCP-47-style language hint such as ja, ko, or zh"
    )
    romanize.add_argument("--line-id", action="append", default=None)
    romanize.add_argument(
        "--regenerate",
        action="store_true",
        help="replace generated cache and clear a draft/rejection, never an approval",
    )
    romanize.add_argument("--full", action="store_true")
    representations = lyrics_commands.add_parser(
        "representations", help="inspect or correct aligned lyric representations"
    )
    representation_commands = representations.add_subparsers(
        dest="representations_command", required=True
    )
    representations_current = representation_commands.add_parser(
        "current", help="show bounded multilingual representation diagnostics"
    )
    representations_current.add_argument("--offline", action="store_true")
    representations_current.add_argument("--full", action="store_true")
    for action in ("set", "approve", "reject", "reset"):
        action_parser = representation_commands.add_parser(
            action, help=f"{action} one representation for the current lyric document"
        )
        action_parser.add_argument("target", choices=("current",))
        action_parser.add_argument("--offline", action="store_true")
        action_parser.add_argument("--line-id", required=True)
        action_parser.add_argument(
            "--kind",
            choices=("romanized", "transliterated", "translated"),
            required=True,
        )
        if action == "set":
            action_parser.add_argument("--text", required=True)
    sync = subparsers.add_parser(
        "sync", help="diagnose playback clock and synchronized lyric transitions"
    )
    sync_commands = sync.add_subparsers(dest="sync_command", required=True)
    sync_current = sync_commands.add_parser(
        "current", help="follow current timed lyrics in a diagnostic terminal view"
    )
    sync_current.add_argument("--offline", action="store_true")
    sync_current.add_argument("--refresh", action="store_true")
    sync_current.add_argument(
        "--samples",
        type=_sample_count,
        default=0,
        help="stop after N samples; 0 follows until interrupted",
    )
    sync_current.add_argument(
        "--interval-ms",
        type=_sample_interval,
        default=500,
        help="maximum diagnostic redraw wait (50-5000 ms; default 500)",
    )
    sync_current.add_argument(
        "--audio-latency-ms",
        type=_nonnegative_milliseconds,
        help="manual output-path latency compensation in decimal milliseconds",
    )
    sync_current.add_argument(
        "--audio-uncertainty-ms",
        type=_nonnegative_milliseconds,
        help="uncertainty of the manual audio latency calibration",
    )
    sync_current.add_argument(
        "--lyrics-shift-ms",
        type=_signed_milliseconds,
        default=None,
        help="command-only override; use sync delay set for durable timing",
    )
    sync_current.add_argument(
        "--provider-timing-uncertainty-ms",
        type=_nonnegative_milliseconds,
        help="known provider timestamp uncertainty, if measured",
    )
    sync_current.add_argument(
        "--presentation-latency-ms",
        type=_nonnegative_milliseconds,
        default=0,
        help="frontend render latency used only to lead transition deadlines",
    )
    sync_current.add_argument(
        "--presentation-uncertainty-ms",
        type=_nonnegative_milliseconds,
        help="uncertainty of the presentation latency calibration",
    )
    sync_current.add_argument(
        "--no-pipewire",
        action="store_true",
        help="skip the optional read-only PipeWire latency diagnostic",
    )
    sync_probe = sync_commands.add_parser(
        "probe", help="measure the current MPRIS media clock without loading lyrics"
    )
    sync_probe.add_argument(
        "--duration-seconds",
        type=_probe_duration,
        default=30,
        help="bounded probe duration in whole seconds (default 30)",
    )
    sync_probe.add_argument(
        "--samples",
        type=_sample_count,
        default=0,
        help="optional deterministic sample limit; 0 uses duration",
    )
    sync_probe.add_argument("--no-pipewire", action="store_true")
    delay = sync_commands.add_parser(
        "delay", help="inspect or change the current lyric document's delay"
    )
    delay_commands = delay.add_subparsers(dest="delay_command", required=True)
    for action in ("show", "reset"):
        command = delay_commands.add_parser(action)
        command.add_argument("--offline", action="store_true")
    delay_set = delay_commands.add_parser("set")
    delay_set.add_argument("value", type=_signed_milliseconds, metavar="[+|-]Nms")
    delay_set.add_argument("--offline", action="store_true")
    audio = sync_commands.add_parser(
        "audio", help="inspect output latency or change exact-device residual"
    )
    audio_commands = audio.add_subparsers(dest="audio_command", required=True)
    audio_commands.add_parser("status")
    audio_calibrate = audio_commands.add_parser("calibrate")
    audio_calibrate.add_argument("value", type=_signed_milliseconds, metavar="[+|-]Nms")
    audio_commands.add_parser("reset")
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
        if arguments.storage_command == "display":
            from lyricflow.domain.representations import RepresentationDisplaySettings

            current = storage.settings.get_representation_display()
            if arguments.display_command == "set":
                supplied = (
                    arguments.original,
                    arguments.romanized,
                    arguments.translated,
                )
                if all(value is None for value in supplied):
                    print(
                        "Display update requires at least one layer option.",
                        file=sys.stderr,
                    )
                    return 2

                def selected(value: str | None, fallback: bool) -> bool:
                    return fallback if value is None else value == "on"

                current = RepresentationDisplaySettings(
                    selected(arguments.original, current.show_original),
                    selected(arguments.romanized, current.show_romanized),
                    selected(arguments.translated, current.show_translated),
                )
                storage.settings.put_representation_display(current)
                print("Saved multilingual lyric display settings.")
            print(f"show original: {'on' if current.show_original else 'off'}")
            romanized_state = "on" if current.show_romanized else "off"
            print(f"show romanized/transliterated: {romanized_state}")
            print(f"show translated: {'on' if current.show_translated else 'off'}")
            return 0
    except StorageError as error:
        print(f"Unable to use LyricFlow storage: {error}", file=sys.stderr)
        return 1
    return 2


def _run_lyrics(
    arguments: argparse.Namespace,
    runtime_factory: RuntimeFactory,
    provider_factory: LyricsProviderFactory,
    database_path: Path | None,
) -> int:
    offline = bool(getattr(arguments, "offline", False))
    refresh = bool(getattr(arguments, "refresh", False))
    if offline and refresh:
        print("--offline and --refresh cannot be used together.", file=sys.stderr)
        return 2
    try:
        runtime = runtime_factory()
    except (ImportError, RuntimeError) as error:
        print(f"Unable to initialize MPRIS: {error}", file=sys.stderr)
        return 1

    from lyricflow.application.lyrics_diagnostics import render_lyrics_resolution
    from lyricflow.application.resolve_lyrics import LyricsResolver
    from lyricflow.application.resolve_track import TrackResolver
    from lyricflow.application.select_player import PlayerSelectionService
    from lyricflow.application.source_identity import SourceIdentityResolver
    from lyricflow.domain.lyrics import LyricsResolutionStatus
    from lyricflow.infrastructure.lyrics.embedded import EmbeddedLyricsProvider
    from lyricflow.infrastructure.lyrics.local_sidecar import (
        LocalSidecarLyricsProvider,
    )
    from lyricflow.infrastructure.lyrics.provider_documents import (
        ProviderLyricDocumentBuilder,
    )
    from lyricflow.infrastructure.metadata.local_paths import (
        FilesystemLocalPathCanonicalizer,
    )
    from lyricflow.infrastructure.storage.bootstrap import open_storage
    from lyricflow.infrastructure.storage.errors import StorageError

    try:
        storage = open_storage(database_path)
        selection = PlayerSelectionService(
            TrackResolver(
                SourceIdentityResolver(FilesystemLocalPathCanonicalizer()),
                storage.track_overrides,
            )
        ).select(runtime.client.list_players(), storage.settings.get_player_selection())
        if selection.selected is None:
            print("No selectable MPRIS track is available for lyrics resolution.")
            for diagnostic in selection.unavailable_diagnostics:
                print(f"  - {diagnostic}")
            return 1
        provider = provider_factory()
        result = LyricsResolver(
            local_sources=(LocalSidecarLyricsProvider(), EmbeddedLyricsProvider()),
            provider=provider,
            provider_documents=ProviderLyricDocumentBuilder(),
            lyrics=storage.lyrics,
            matches=storage.lyrics_matches,
            provider_cache=storage.provider_cache,
        ).resolve(
            selection.selected.track,
            offline=offline,
            refresh=refresh,
        )
    except (ImportError, RuntimeError) as error:
        print(f"Unable to initialize lyrics provider: {error}", file=sys.stderr)
        return 1
    except StorageError as error:
        print(f"Unable to use LyricFlow storage: {error}", file=sys.stderr)
        return 1

    if arguments.lyrics_command == "current":
        print(
            render_lyrics_resolution(
                selection.selected.track, result, full=arguments.full
            )
        )
    else:
        if result.document is None:
            print(
                render_lyrics_resolution(selection.selected.track, result, full=False)
            )
            print("No lyric document is available for multilingual representations.")
            return 1
        from lyricflow.application.representation_diagnostics import (
            render_representations,
        )
        from lyricflow.application.representations import RepresentationService
        from lyricflow.domain.lyrics import RepresentationKind
        from lyricflow.infrastructure.romanization.offline import (
            OfflineRomanizationProvider,
        )

        service = RepresentationService(
            OfflineRomanizationProvider(), storage.representations
        )
        document = result.document
        settings = storage.settings.get_representation_display()
        track = selection.selected.track.candidate
        heading = (
            f"track: {track.title or '<unknown>'}\n"
            f"artist: {', '.join(track.artists) or '<unknown>'}"
        )
        if arguments.lyrics_command == "romanize":
            try:
                report = service.generate(
                    document,
                    language_hint=arguments.language,
                    line_ids=arguments.line_id,
                    regenerate=arguments.regenerate,
                )
            except ValueError as error:
                print(f"Unable to generate representation: {error}", file=sys.stderr)
                return 2
            print(heading)
            print(
                render_representations(
                    document,
                    service,
                    settings,
                    full=arguments.full,
                    report=report,
                )
            )
            return int(report.failed > 0)
        action = arguments.representations_command
        if action == "current":
            print(heading)
            print(
                render_representations(document, service, settings, full=arguments.full)
            )
            return 0
        kind = RepresentationKind(arguments.kind)
        try:
            if action == "set":
                decision = service.set_draft(
                    document, arguments.line_id, kind, arguments.text
                )
                result_message = "Saved user draft"
            elif action == "approve":
                decision = service.approve(document, arguments.line_id, kind)
                result_message = "Saved user approval"
            elif action == "reject":
                decision = service.reject_generated(document, arguments.line_id, kind)
                result_message = "Saved generated-value rejection"
            else:
                removed = service.reset(document, arguments.line_id, kind)
                print(
                    "Removed representation decision."
                    if removed
                    else "No representation decision existed."
                )
                print(f"lyric document: {document.document_id}")
                print(f"line ID: {arguments.line_id}")
                print(f"kind: {kind.value}")
                return 0
        except ValueError as error:
            print(f"Unable to update representation: {error}", file=sys.stderr)
            return 2
        print(f"{result_message}.")
        print(f"lyric document: {decision.document_id}")
        print(f"line ID: {decision.source_line_id}")
        print(f"kind: {decision.kind.value}")
        print(f"state: {decision.approval_state.value}")
        return 0
    failure_states = {
        LyricsResolutionStatus.PROVIDER_UNAVAILABLE,
        LyricsResolutionStatus.RATE_LIMITED,
        LyricsResolutionStatus.INVALID_LOCAL_LYRICS,
        LyricsResolutionStatus.INVALID_PROVIDER_RESPONSE,
    }
    return int(result.status in failure_states)


def _sync_session_id(track: ResolvedTrack) -> str:
    payload = repr(track.source_identity).encode("utf-8", errors="replace")
    return "sync-" + sha256(payload).hexdigest()


def _select_sync_track(
    runtime: MprisRuntimePort,
    storage: StorageRepositories,
) -> ResolvedTrack | None:
    from lyricflow.application.resolve_track import TrackResolver
    from lyricflow.application.select_player import PlayerSelectionService
    from lyricflow.application.source_identity import SourceIdentityResolver
    from lyricflow.infrastructure.metadata.local_paths import (
        FilesystemLocalPathCanonicalizer,
    )

    selection = PlayerSelectionService(
        TrackResolver(
            SourceIdentityResolver(FilesystemLocalPathCanonicalizer()),
            storage.track_overrides,
        )
    ).select(runtime.client.list_players(), storage.settings.get_player_selection())
    if selection.selected is None:
        print("No selectable MPRIS track is available for synchronization.")
        for diagnostic in selection.unavailable_diagnostics:
            print(f"  - {diagnostic}")
        return None
    return selection.selected.track


def _select_sync_track_without_storage(
    runtime: MprisRuntimePort,
) -> ResolvedTrack | None:
    """Select for a clock-only probe without creating or migrating storage."""

    from lyricflow.application.resolve_track import TrackResolver
    from lyricflow.application.select_player import PlayerSelectionService
    from lyricflow.application.source_identity import SourceIdentityResolver
    from lyricflow.domain.tracks import PlayerSelectionConfig
    from lyricflow.infrastructure.metadata.local_paths import (
        FilesystemLocalPathCanonicalizer,
    )
    from lyricflow.infrastructure.storage.track_overrides import (
        InMemoryTrackOverrideRepository,
    )

    selection = PlayerSelectionService(
        TrackResolver(
            SourceIdentityResolver(FilesystemLocalPathCanonicalizer()),
            InMemoryTrackOverrideRepository(),
        )
    ).select(runtime.client.list_players(), PlayerSelectionConfig())
    if selection.selected is None:
        print("No selectable MPRIS track is available for synchronization.")
        for diagnostic in selection.unavailable_diagnostics:
            print(f"  - {diagnostic}")
        return None
    return selection.selected.track


def _resolve_sync_document(
    track: ResolvedTrack,
    storage: StorageRepositories,
    provider: LyricsProviderPort,
    *,
    offline: bool,
    refresh: bool = False,
) -> LyricDocument | None:
    from lyricflow.application.resolve_lyrics import LyricsResolver
    from lyricflow.domain.lyrics import LyricDocumentKind
    from lyricflow.infrastructure.lyrics.embedded import EmbeddedLyricsProvider
    from lyricflow.infrastructure.lyrics.local_sidecar import LocalSidecarLyricsProvider
    from lyricflow.infrastructure.lyrics.provider_documents import (
        ProviderLyricDocumentBuilder,
    )

    resolver = LyricsResolver(
        local_sources=(LocalSidecarLyricsProvider(), EmbeddedLyricsProvider()),
        provider=provider,
        provider_documents=ProviderLyricDocumentBuilder(),
        lyrics=storage.lyrics,
        matches=storage.lyrics_matches,
        provider_cache=storage.provider_cache,
    )
    result = resolver.resolve(track, offline=offline, refresh=refresh)
    if result.document is None:
        print(f"No lyric document is available ({result.status.value}).")
        return None
    if result.document.kind is not LyricDocumentKind.SYNCED:
        print(
            "Synchronization requires timed lyrics; current document is "
            f"{result.document.kind.value}."
        )
        return None
    return result.document


def _run_sync_audio(
    arguments: argparse.Namespace,
    latency_probe_factory: AudioLatencyProbeFactory,
    database_path: Path | None,
) -> int:
    from lyricflow.application.sync_diagnostics import render_audio_latency_probe
    from lyricflow.domain.synchronization import OutputDeviceCalibration
    from lyricflow.infrastructure.storage.bootstrap import open_storage
    from lyricflow.infrastructure.storage.errors import StorageError

    try:
        storage = open_storage(database_path)
        result = latency_probe_factory().probe()
    except (ImportError, RuntimeError, StorageError) as error:
        print(f"Unable to inspect audio timing: {error}", file=sys.stderr)
        return 1
    print(render_audio_latency_probe(result))
    if result.device_key is None:
        print("Device residual calibration: unavailable (no stable device identity)")
        return int(arguments.audio_command != "status")
    existing = storage.timing_calibrations.get_output_calibration(result.device_key)
    if arguments.audio_command == "calibrate":
        label = result.route or "unnamed PipeWire output"
        existing = OutputDeviceCalibration(result.device_key, label, arguments.value)
        storage.timing_calibrations.put_output_calibration(existing)
        print("Saved residual calibration for this exact output device.")
        print("positive residual = audio is heard later; compensation increases")
    elif arguments.audio_command == "reset":
        removed = storage.timing_calibrations.delete_output_calibration(
            result.device_key
        )
        existing = None
        print(
            "Removed this output device's residual calibration."
            if removed
            else "No residual calibration existed for this output device."
        )
    residual_us = 0 if existing is None else existing.residual_delay_us
    total_us = (
        None
        if result.chosen_estimate_us is None
        else max(0, result.chosen_estimate_us + residual_us)
    )
    print(f"device residual calibration: {residual_us / 1_000:+.3f} ms")
    if total_us is not None:
        print(f"automatic estimate + residual: {total_us / 1_000:.3f} ms")
    if result.safe_for_automatic_compensation and total_us is not None:
        print(f"total applied compensation: {total_us / 1_000:.3f} ms")
    else:
        print("total applied compensation: unavailable (route not proven)")
    return 0


def _run_sync_delay(
    arguments: argparse.Namespace,
    runtime_factory: RuntimeFactory,
    provider_factory: LyricsProviderFactory,
    database_path: Path | None,
) -> int:
    from lyricflow.domain.synchronization import LyricDocumentTiming
    from lyricflow.infrastructure.storage.bootstrap import open_storage
    from lyricflow.infrastructure.storage.errors import StorageError

    try:
        runtime = runtime_factory()
        storage = open_storage(database_path)
        track = _select_sync_track(runtime, storage)
        if track is None:
            return 1
        document = _resolve_sync_document(
            track,
            storage,
            provider_factory(),
            offline=bool(arguments.offline),
        )
        if document is None:
            return 1
        repository = storage.timing_calibrations
        if arguments.delay_command == "set":
            repository.put_document_timing(
                LyricDocumentTiming(document.document_id, arguments.value)
            )
            action = "Saved"
        elif arguments.delay_command == "reset":
            action = (
                "Removed"
                if repository.delete_document_timing(document.document_id)
                else "No stored value to remove for"
            )
        else:
            action = "Current"
        timing = repository.get_document_timing(document.document_id)
    except (ImportError, RuntimeError, StorageError) as error:
        print(f"Unable to use lyric timing delay: {error}", file=sys.stderr)
        return 1
    print(f"{action} lyric document display delay.")
    print(f"lyric document: {document.document_id}")
    print(f"lyrics_display_delay_us: {timing.lyrics_display_delay_us}")
    print(f"display delay: {timing.lyrics_display_delay_us / 1_000:+.3f} ms")
    print("positive = show lyric transitions later; negative = show them earlier")
    print("provider timestamps remain unchanged")
    return 0


def _run_sync_probe(
    arguments: argparse.Namespace,
    runtime_factory: RuntimeFactory,
    latency_probe_factory: AudioLatencyProbeFactory,
    database_path: Path | None,
) -> int:
    from lyricflow.application.clock_lifecycle import (
        AdaptiveResampler,
        SuspendResumeDetector,
    )
    from lyricflow.application.playback_clock import PlaybackClock
    from lyricflow.application.sync_diagnostics import render_probe_report
    from lyricflow.application.sync_session import PlaybackSyncSession
    from lyricflow.domain.synchronization import (
        AudioLatencyProbeResult,
        AudioLatencyProbeStatus,
        PlaybackState,
    )
    from lyricflow.infrastructure.mpris.backend import MprisBackendError
    from lyricflow.infrastructure.storage.bootstrap import open_storage_readonly
    from lyricflow.infrastructure.storage.errors import StorageError

    try:
        runtime = runtime_factory()
    except (ImportError, RuntimeError) as error:
        print(f"Unable to initialize synchronization probe: {error}", file=sys.stderr)
        return 1
    try:
        storage = open_storage_readonly(database_path)
    except StorageError as error:
        storage = None
        print(f"Storage ignored by clock-only probe: {error}", file=sys.stderr)
    try:
        track = (
            _select_sync_track(runtime, storage)
            if storage is not None
            else _select_sync_track_without_storage(runtime)
        )
    except (ImportError, RuntimeError, StorageError) as error:
        print(f"Unable to select synchronization source: {error}", file=sys.stderr)
        return 1
    if track is None:
        return 1
    try:
        audio = (
            AudioLatencyProbeResult(
                AudioLatencyProbeStatus.UNAVAILABLE,
                diagnostics=("PipeWire probe disabled",),
            )
            if arguments.no_pipewire
            else latency_probe_factory().probe()
        )
    except (ImportError, RuntimeError) as error:
        audio = AudioLatencyProbeResult(
            AudioLatencyProbeStatus.UNAVAILABLE,
            diagnostics=(str(error),),
        )
    residual = (
        None
        if audio.device_key is None
        else (
            None
            if storage is None
            else storage.timing_calibrations.get_output_calibration(audio.device_key)
        )
    )
    clock = PlaybackClock(runtime.clock.monotonic_ns)
    session = PlaybackSyncSession(clock, track.raw_snapshot, _sync_session_id(track))
    scheduler = AdaptiveResampler()
    detector = SuspendResumeDetector(runtime.clock)
    detector.observe()

    def handle_probe_event(event: object) -> None:
        from lyricflow.domain.models import PlayerEvent

        if not isinstance(event, PlayerEvent):
            return
        if session.handle_event(event):
            runtime.wake()

    start = runtime.monitor.start(handle_probe_event)
    if start.error is not None:
        runtime.monitor.close()
        print(f"Unable to monitor MPRIS synchronization events: {start.error}")
        return 1
    started_ns = runtime.clock.monotonic_ns()
    deadline_ns = started_ns + arguments.duration_seconds * 1_000_000_000
    attempts = 0
    failures = 0
    interrupted = False
    estimate = None
    cleanup_failed = False
    try:
        while True:
            now_ns = runtime.clock.monotonic_ns()
            if arguments.samples and attempts >= arguments.samples:
                break
            if not arguments.samples and attempts > 0 and now_ns >= deadline_ns:
                break
            suspended = detector.observe()
            if suspended.resumed:
                session.system_resumed()
                scheduler.trigger(session.requested_reason, now_ns)
            if session.requires_reload:
                refreshed = (
                    _select_sync_track(runtime, storage)
                    if storage is not None
                    else _select_sync_track_without_storage(runtime)
                )
                if refreshed is None:
                    break
                track = refreshed
                session.replace_source(track.raw_snapshot, _sync_session_id(track))
            scheduler.trigger(session.requested_reason, now_ns)
            if not scheduler.due(now_ns):
                wait_ms = scheduler.delay_ms(now_ns)
                if not arguments.samples:
                    remaining_ms = max(0, (deadline_ns - now_ns) // 1_000_000)
                    wait_ms = min(wait_ms, remaining_ms)
                    if wait_ms == 0:
                        break
                runtime.wait(wait_ms)
                continue
            try:
                update = session.sample(runtime.timing)
            except (MprisBackendError, RuntimeError) as error:
                failures += 1
                update = clock.mark_sampling_failure(
                    f"Position sampling failed: {error}"
                )
            attempts += 1
            estimate = session.estimate(duration_us=track.candidate.duration_us)
            state = PlaybackState.UNKNOWN if estimate is None else estimate.state
            scheduler.record(
                update,
                state,
                runtime.clock.monotonic_ns(),
                None if estimate is None else estimate.diagnostics.health,
            )
            if arguments.samples and attempts >= arguments.samples:
                break
            now_ns = runtime.clock.monotonic_ns()
            wait_ms = scheduler.delay_ms(now_ns)
            if not arguments.samples:
                remaining_ms = max(0, (deadline_ns - now_ns) // 1_000_000)
                wait_ms = min(wait_ms, remaining_ms)
                if wait_ms == 0:
                    break
            runtime.wait(wait_ms)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        try:
            runtime.monitor.close()
        except RuntimeError as error:
            print(f"Unable to stop synchronization monitor: {error}", file=sys.stderr)
            cleanup_failed = True
    if cleanup_failed:
        return 1
    if estimate is None:
        print("Playback clock has no accepted observation.", file=sys.stderr)
        return 1
    source = track.source_identity.kind.value
    print(
        render_probe_report(
            player=track.raw_snapshot.service_name,
            source=source,
            playback_status=estimate.state.value,
            attempts=attempts,
            sampling_failures=failures,
            estimate=estimate,
            audio=audio,
            device_residual_us=(
                None if residual is None else residual.residual_delay_us
            ),
        )
    )
    return 130 if interrupted else 0


def _run_sync(
    arguments: argparse.Namespace,
    runtime_factory: RuntimeFactory,
    provider_factory: LyricsProviderFactory,
    latency_probe_factory: AudioLatencyProbeFactory,
    database_path: Path | None,
) -> int:
    """Follow one selected player through the frontend-neutral Stage 6 engine."""

    if arguments.sync_command == "audio":
        return _run_sync_audio(arguments, latency_probe_factory, database_path)
    if arguments.sync_command == "delay":
        return _run_sync_delay(
            arguments, runtime_factory, provider_factory, database_path
        )
    if arguments.sync_command == "probe":
        return _run_sync_probe(
            arguments, runtime_factory, latency_probe_factory, database_path
        )

    if arguments.offline and arguments.refresh:
        print("--offline and --refresh cannot be used together.", file=sys.stderr)
        return 2
    if (
        arguments.audio_latency_ms is None
        and arguments.audio_uncertainty_ms is not None
    ):
        print(
            "--audio-uncertainty-ms requires --audio-latency-ms.",
            file=sys.stderr,
        )
        return 2

    from lyricflow.application.audio_timing import AudioOutputTimingService
    from lyricflow.application.clock_lifecycle import (
        AdaptiveResampler,
        SuspendResumeDetector,
    )
    from lyricflow.application.lyrics_sync import synchronize
    from lyricflow.application.playback_clock import PlaybackClock
    from lyricflow.application.representations import RepresentationService
    from lyricflow.application.sync_diagnostics import (
        render_audio_latency_probe,
        render_sync_frame,
    )
    from lyricflow.application.sync_session import PlaybackSyncSession
    from lyricflow.domain.lyrics import RepresentationKind
    from lyricflow.domain.synchronization import (
        AudioLatencyProbeResult,
        AudioLatencyProbeStatus,
        AudioOutputLatency,
        LyricTimingCalibration,
        PresentationLatency,
        SynchronizationCalibration,
    )
    from lyricflow.infrastructure.mpris.backend import MprisBackendError
    from lyricflow.infrastructure.romanization.offline import (
        OfflineRomanizationProvider,
    )
    from lyricflow.infrastructure.storage.bootstrap import open_storage
    from lyricflow.infrastructure.storage.errors import StorageError

    try:
        runtime = runtime_factory()
        storage = open_storage(database_path)
        provider = provider_factory()
    except (ImportError, RuntimeError) as error:
        print(f"Unable to initialize synchronization: {error}", file=sys.stderr)
        return 1
    except StorageError as error:
        print(f"Unable to use LyricFlow storage: {error}", file=sys.stderr)
        return 1

    def load_current() -> tuple[ResolvedTrack, LyricDocument] | None:
        track = _select_sync_track(runtime, storage)
        if track is None:
            return None
        document = _resolve_sync_document(
            track,
            storage,
            provider,
            offline=bool(arguments.offline),
            refresh=bool(arguments.refresh),
        )
        if document is None:
            return None
        return track, document

    try:
        loaded = load_current()
    except (ImportError, RuntimeError, StorageError) as error:
        print(f"Unable to resolve synchronized lyrics: {error}", file=sys.stderr)
        return 1
    if loaded is None:
        return 1
    track, document = loaded
    representation_service = RepresentationService(
        OfflineRomanizationProvider(), storage.representations
    )

    def selected_representations() -> tuple[EffectiveRepresentationLine, ...]:
        return (
            *representation_service.effective_lines(
                document, RepresentationKind.ROMANIZED
            ),
            *representation_service.effective_lines(
                document, RepresentationKind.TRANSLITERATED
            ),
            *representation_service.effective_lines(
                document, RepresentationKind.TRANSLATED
            ),
        )

    effective_representations = selected_representations()

    pipewire_report = None
    if not arguments.no_pipewire:
        try:
            pipewire_report = latency_probe_factory().probe()
        except (ImportError, RuntimeError) as error:
            print(f"PipeWire latency diagnostic unavailable: {error}", file=sys.stderr)
    audio_timing_service = AudioOutputTimingService(
        storage.timing_calibrations,
        runtime.clock.monotonic_ns,
    )
    if pipewire_report is not None:
        audio_timing_service.update(pipewire_report)
    if arguments.audio_latency_ms is not None:
        audio_output = AudioOutputLatency(
            arguments.audio_latency_ms,
            arguments.audio_uncertainty_ms,
            "manual command calibration",
        )
    else:
        audio_output = audio_timing_service.current()
    stored_timing = storage.timing_calibrations.get_document_timing(
        document.document_id
    )
    lyric_shift_us = (
        stored_timing.lyrics_display_delay_us
        if arguments.lyrics_shift_ms is None
        else arguments.lyrics_shift_ms
    )
    calibration = SynchronizationCalibration(
        audio_output,
        LyricTimingCalibration(
            lyric_shift_us,
            arguments.provider_timing_uncertainty_ms,
            (
                "manual command calibration"
                if arguments.lyrics_shift_ms is not None
                else "durable per-document display delay"
            ),
            (
                ()
                if arguments.provider_timing_uncertainty_ms is not None
                else ("provider lyric timestamp error is unmeasured",)
            ),
        ),
        PresentationLatency(
            arguments.presentation_latency_ms,
            arguments.presentation_uncertainty_ms,
            "diagnostic-terminal",
        ),
    )
    clock = PlaybackClock(runtime.clock.monotonic_ns)
    session = PlaybackSyncSession(clock, track.raw_snapshot, _sync_session_id(track))
    scheduler = AdaptiveResampler()
    detector = SuspendResumeDetector(runtime.clock)
    detector.observe()
    last_audio_probe_ns = runtime.clock.monotonic_ns()

    def handle_sync_event(event: object) -> None:
        from lyricflow.domain.models import PlayerEvent

        if not isinstance(event, PlayerEvent):
            return
        if session.handle_event(event):
            runtime.wake()

    start = runtime.monitor.start(handle_sync_event)
    if start.error is not None:
        runtime.monitor.close()
        print(f"Unable to monitor MPRIS synchronization events: {start.error}")
        return 1

    completed = 0
    last_update = None
    try:
        while arguments.samples == 0 or completed < arguments.samples:
            now_ns = runtime.clock.monotonic_ns()
            suspended = detector.observe()
            if suspended.resumed:
                session.system_resumed()
                scheduler.trigger(session.requested_reason, now_ns)
            if not arguments.no_pipewire and (
                suspended.resumed or now_ns - last_audio_probe_ns >= 10_000_000_000
            ):
                try:
                    pipewire_report = latency_probe_factory().probe()
                except (ImportError, RuntimeError) as error:
                    pipewire_report = AudioLatencyProbeResult(
                        AudioLatencyProbeStatus.UNAVAILABLE,
                        diagnostics=(str(error),),
                    )
                audio_timing_service.update(pipewire_report)
                last_audio_probe_ns = now_ns
                if arguments.audio_latency_ms is None:
                    calibration = SynchronizationCalibration(
                        audio_timing_service.current(),
                        calibration.lyrics,
                        calibration.presentation,
                    )
            if session.requires_reload:
                loaded = load_current()
                if loaded is None:
                    return 1
                new_track, new_document = loaded
                track, document = new_track, new_document
                effective_representations = selected_representations()
                session.replace_source(track.raw_snapshot, _sync_session_id(track))
                stored_timing = storage.timing_calibrations.get_document_timing(
                    document.document_id
                )
                if arguments.lyrics_shift_ms is None:
                    calibration = SynchronizationCalibration(
                        calibration.audio_output,
                        LyricTimingCalibration(
                            stored_timing.lyrics_display_delay_us,
                            arguments.provider_timing_uncertainty_ms,
                            "durable per-document display delay",
                            calibration.lyrics.limitations,
                        ),
                        calibration.presentation,
                    )
            scheduler.trigger(session.requested_reason, now_ns)
            sampled = scheduler.due(now_ns)
            if sampled:
                try:
                    last_update = session.sample(runtime.timing)
                except (MprisBackendError, RuntimeError) as error:
                    last_update = clock.mark_sampling_failure(
                        f"Position sampling failed: {error}"
                    )
                    print(
                        f"MPRIS Position sample failed; continuing degraded: {error}",
                        file=sys.stderr,
                    )
                    if clock.estimate() is None:
                        if arguments.samples:
                            return 1
                        runtime.wait(750)
                        continue
                else:
                    completed += 1
            estimate = session.estimate(duration_us=track.candidate.duration_us)
            if estimate is None:
                print("Playback clock has no accepted observation.", file=sys.stderr)
                return 1
            if sampled and last_update is not None:
                scheduler.record(
                    last_update,
                    estimate.state,
                    estimate.monotonic_ns,
                    estimate.diagnostics.health,
                )
            frame = synchronize(document, estimate, calibration)
            update_text = (
                "interpolated locally"
                if not sampled or last_update is None
                else f"{last_update.kind.value} ({last_update.reason})"
            )
            heading = (
                f"selected player: {session.snapshot.service_name}\n"
                f"track: {track.candidate.title or '<unknown>'}\n"
                f"clock update: {update_text}"
            )
            rendered = (
                heading
                + "\n"
                + render_sync_frame(
                    estimate,
                    frame,
                    effective_representations,
                )
            )
            if pipewire_report is not None:
                rendered += "\n" + render_audio_latency_probe(pipewire_report)
            if sys.stdout.isatty():
                print("\033[2J\033[H" + rendered, end="", flush=True)
            else:
                print(f"--- sync frame {max(1, completed)} ---\n{rendered}", flush=True)
            if arguments.samples and completed >= arguments.samples:
                break
            wait_ms = min(
                arguments.interval_ms,
                scheduler.delay_ms(estimate.monotonic_ns),
            )
            if frame.deadline is not None:
                audible_transition_ns = (
                    frame.deadline.display_deadline_ns
                    + calibration.presentation.estimate_us * 1_000
                )
                remaining_ns = audible_transition_ns - estimate.monotonic_ns
                if remaining_ns > 0:
                    wait_ms = min(
                        wait_ms, max(1, (remaining_ns + 999_999) // 1_000_000)
                    )
            wait_ms = max(1, wait_ms)
            runtime.wait(wait_ms)
    except KeyboardInterrupt:
        return 130
    finally:
        try:
            runtime.monitor.close()
        except RuntimeError as error:
            print(f"Unable to stop synchronization monitor: {error}", file=sys.stderr)
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime_factory: RuntimeFactory = _create_runtime,
    lyrics_provider_factory: LyricsProviderFactory = _create_lyrics_provider,
    audio_latency_probe_factory: AudioLatencyProbeFactory = _create_audio_latency_probe,
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
    if arguments.command == "lyrics":
        return _run_lyrics(
            arguments, runtime_factory, lyrics_provider_factory, database_path
        )
    if arguments.command == "sync":
        return _run_sync(
            arguments,
            runtime_factory,
            lyrics_provider_factory,
            audio_latency_probe_factory,
            database_path,
        )
    return 2
