"""Qt lifecycle coordinator for presentation-neutral application services."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Condition, Event, Thread
from time import monotonic
from typing import Any, cast

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from konokashi.application.appearance import AppearanceProfile
from konokashi.application.clock_lifecycle import AdaptiveResampler
from konokashi.application.desktop_state import DesktopStateController
from konokashi.application.frontend_session import (
    FrontendLyricsBundle,
    FrontendSessionPort,
)
from konokashi.application.lyrics_sync import synchronize
from konokashi.application.playback_clock import PlaybackClock
from konokashi.application.player_selectors import stable_player_suggestions
from konokashi.application.ports import (
    DesktopMprisRuntimePort,
    MprisRuntimePort,
    PendingOperationPort,
)
from konokashi.application.review_corrections import ReviewCorrectionSnapshot
from konokashi.application.settings import (
    SETTINGS_SCHEMA,
    DesktopInteractionSettings,
    SettingsSnapshot,
    SettingsValidationError,
    SettingValue,
)
from konokashi.application.settings_service import (
    CanonicalSettingsService,
    SettingsChange,
    SettingsFileError,
    SettingsReloadResult,
    SettingsSubscription,
)
from konokashi.application.sync_session import PlaybackSyncSession
from konokashi.application.sync_state import (
    SnapshotSubscription,
    SynchronizationPublisher,
    SynchronizationSnapshot,
    build_sync_snapshot,
)
from konokashi.domain.library import LibraryReviewItem, LibraryScanSummary
from konokashi.domain.lyrics import LyricDocumentKind, LyricsResolutionStatus
from konokashi.domain.models import (
    PlayerEvent,
    PlayerEventKind,
    PlayerListResult,
    PlayerWatchStart,
)
from konokashi.domain.representations import RepresentationDisplaySettings
from konokashi.domain.synchronization import (
    AudioOutputLatency,
    LyricTimingCalibration,
    PositionObservation,
    PresentationLatency,
    SynchronizationCalibration,
)
from konokashi.domain.tracks import PlayerSelectionResult, ResolvedTrack
from konokashi.infrastructure.configuration.bootstrap import open_settings
from konokashi.infrastructure.configuration.paths import default_config_path
from konokashi.infrastructure.configuration.qt_watcher import QtSettingsWatcher
from konokashi.infrastructure.frontend import create_frontend_session
from konokashi.infrastructure.lyrics.lrclib import LrclibLyricsProvider
from konokashi.infrastructure.mpris.backend import MprisBackendError
from konokashi.infrastructure.mpris.qt_dbus_client import create_qt_mpris_runtime
from konokashi.infrastructure.storage.bootstrap import open_storage
from konokashi.presentation.desktop.main_window import MainWindow
from konokashi.presentation.desktop.review_dialog import (
    CorrectionActionKind,
    CorrectionActionRequest,
)
from konokashi.presentation.desktop.settings_window import SettingsWindow


class _JobSignals(QObject):
    completed = Signal(int, object, object)


@dataclass(frozen=True, slots=True)
class _LibraryScanResult:
    summary: LibraryScanSummary
    review_items: tuple[LibraryReviewItem, ...]


class _FunctionJob:
    """Run one bounded blocking application operation outside the UI thread."""

    def __init__(
        self,
        job_id: int,
        function: Callable[[], object],
        signals: _JobSignals,
    ) -> None:
        self.job_id = job_id
        self._function = function
        self._signals = signals

    def run(self) -> None:
        try:
            result = self._function()
        except Exception as error:  # boundary converts to a controlled UI state
            with suppress(RuntimeError):
                self._signals.completed.emit(self.job_id, None, error)
        else:
            with suppress(RuntimeError):
                self._signals.completed.emit(self.job_id, result, None)


class _DaemonWorkerPool:
    """Bounded daemon workers that cannot make GUI process exit unbounded."""

    _STOP = object()

    def __init__(self, max_workers: int) -> None:
        if max_workers <= 0:
            raise ValueError("worker count must be positive")
        self._queue: Queue[_FunctionJob | object] = Queue()
        self._condition = Condition()
        self._pending = 0
        self._closed = False
        self._workers = tuple(
            Thread(
                target=self._work,
                name=f"konokashi-desktop-{index + 1}",
                daemon=True,
            )
            for index in range(max_workers)
        )
        for worker in self._workers:
            worker.start()

    def start(self, job: _FunctionJob) -> None:
        with self._condition:
            if self._closed:
                return
            self._pending += 1
        self._queue.put(job)

    def clear(self) -> None:
        """Discard queued work while allowing at most two running jobs to unwind."""

        cleared = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except Empty:
                break
            if item is self._STOP:
                self._queue.put(item)
                break
            cleared += 1
        if cleared:
            with self._condition:
                self._pending -= cleared
                self._condition.notify_all()

    def close(self) -> None:
        """Stop accepting work and wake idle daemon workers without waiting."""

        with self._condition:
            if self._closed:
                return
            self._closed = True
        self.clear()
        for _worker in self._workers:
            self._queue.put(self._STOP)

    def waitForDone(self, timeout_ms: int) -> bool:
        """Compatibility helper for deterministic tests and bounded handoff."""

        deadline = monotonic() + max(0, timeout_ms) / 1_000
        with self._condition:
            while self._pending:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is self._STOP:
                return
            assert isinstance(item, _FunctionJob)
            try:
                item.run()
            finally:
                with self._condition:
                    self._pending -= 1
                    self._condition.notify_all()


@dataclass(frozen=True, slots=True)
class _InitializedServices:
    frontend: FrontendSessionPort
    canonical: CanonicalSettingsService
    settings: RepresentationDisplaySettings
    interactions: DesktopInteractionSettings
    appearance: AppearanceProfile


def _session_id(track: ResolvedTrack) -> str:
    """Use a stable process-local source representation for clock resets."""

    return f"desktop:{track.source_identity!r}"


class DesktopCoordinator(QObject):
    """Keep widgets passive while coordinating Qt events and background work."""

    settings_change_observed = Signal(object)

    def __init__(
        self,
        application: QApplication,
        window: MainWindow,
        *,
        database_path: Path | None = None,
        config_path: Path | None = None,
        runtime: MprisRuntimePort | None = None,
    ) -> None:
        super().__init__(application)
        self._application = application
        self._window = window
        self._database_path = database_path
        self._config_path = config_path
        self._runtime: MprisRuntimePort = runtime or create_qt_mpris_runtime()
        self._desktop_runtime: DesktopMprisRuntimePort = self._runtime.desktop
        self._controller = DesktopStateController()
        self._frontend: FrontendSessionPort | None = None
        self._settings_service: CanonicalSettingsService | None = None
        self._settings_watcher: QtSettingsWatcher | None = None
        self._settings_subscription: SettingsSubscription | None = None
        self._settings_window: SettingsWindow | None = None
        self._track: ResolvedTrack | None = None
        self._bundle: FrontendLyricsBundle | None = None
        self._playback_session: PlaybackSyncSession | None = None
        self._playback_clock: PlaybackClock | None = None
        self._scheduler: AdaptiveResampler | None = None
        self._calibration = SynchronizationCalibration()
        self._publisher: SynchronizationPublisher | None = None
        self._snapshot_subscription: SnapshotSubscription | None = None
        self._monitor_started = False
        self._monitor_request: PendingOperationPort | None = None
        self._selection_request: PendingOperationPort | None = None
        self._position_request: PendingOperationPort | None = None
        self._position_request_serial = 0
        self._position_coalesced = False
        self._closed = False
        self._selection_serial = 0
        self._load_serial = 0
        self._review_serial = 0
        self._library_cancellation: Event | None = None
        self._player_suggestions: tuple[str, ...] = ()

        self._pool = _DaemonWorkerPool(2)
        self._job_signals = _JobSignals(self)
        self._job_signals.completed.connect(self._job_completed)
        self._next_job_id = 1
        self._jobs: dict[
            int, Callable[[object | None, BaseException | None], None]
        ] = {}

        self._selection_timer = QTimer(self)
        self._selection_timer.setSingleShot(True)
        self._selection_timer.setInterval(75)
        self._selection_timer.timeout.connect(self._refresh_selection)
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(2_000)
        self._idle_timer.timeout.connect(self._refresh_if_idle)
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(100)
        self._sync_timer.timeout.connect(self._sync_tick)

        self._window.settings_requested.connect(self._open_settings_window)
        self._window.setting_requested.connect(self._change_setting)
        self._window.review_requested.connect(self._load_review)
        self._window.correction_requested.connect(self._apply_correction)
        self._window.library_scan_requested.connect(self._start_library_scan)
        self._window.library_scan_cancel_requested.connect(self._cancel_library_scan)
        self._application.aboutToQuit.connect(self.close)
        self.settings_change_observed.connect(self._settings_changed)

    @property
    def controller(self) -> DesktopStateController:
        """Expose semantic state for deterministic lifecycle tests."""

        return self._controller

    def start(self) -> None:
        """Start monitoring immediately and initialize storage off-thread."""

        self._window.render_state(self._controller.state)
        try:
            completed = False

            def monitor_started(
                started: object | None,
                error: BaseException | None,
            ) -> None:
                nonlocal completed
                completed = True
                self._monitor_request = None
                if self._closed:
                    return
                if error is not None or not isinstance(started, PlayerWatchStart):
                    diagnostic = str(error or "unknown monitor startup failure")
                    self._window.render_state(
                        self._controller.application_error(
                            "Unable to monitor media players.", (diagnostic,)
                        )
                    )
                    return
                if started.error is not None:
                    self._window.render_state(
                        self._controller.application_error(
                            "Unable to monitor media players.", (started.error,)
                        )
                    )
                    return
                self._monitor_started = True
                self._idle_timer.start()
                self._start_job(self._initialize_services, self._services_initialized)

            request = self._desktop_runtime.monitor.start_async(
                self._on_player_event,
                monitor_started,
            )
            if not completed:
                self._monitor_request = request
        except RuntimeError as error:
            self._window.render_state(
                self._controller.application_error(
                    "Unable to monitor media players.", (str(error),)
                )
            )
            return

    def _initialize_services(self) -> _InitializedServices:
        storage = open_storage(self._database_path)
        canonical = open_settings(storage, config_path=self._config_path)
        frontend = create_frontend_session(storage, LrclibLyricsProvider(), canonical)
        return _InitializedServices(
            frontend,
            canonical,
            canonical.get_representation_display(),
            canonical.get_desktop_interaction(),
            canonical.current.appearance,
        )

    def _services_initialized(
        self, result: object | None, error: BaseException | None
    ) -> None:
        if self._closed:
            return
        if error is not None or not isinstance(result, _InitializedServices):
            diagnostic = (
                "unknown initialization failure" if error is None else str(error)
            )
            self._window.render_state(
                self._controller.application_error(
                    "KonoKashi storage could not be initialized.", (diagnostic,)
                )
            )
            return
        self._frontend = result.frontend
        self._settings_service = result.canonical
        self._settings_watcher = QtSettingsWatcher(
            result.canonical, self._settings_reload_observed, parent=self
        )
        self._settings_subscription = result.canonical.subscribe(
            lambda change: self.settings_change_observed.emit(change)
        )
        self._controller.set_representation_settings(result.settings)
        self._window.set_representation_settings(result.settings)
        self._window.set_interaction_settings(result.interactions)
        self._window.set_appearance_profile(result.appearance)
        for settings_diagnostic in result.canonical.diagnostics:
            self._window.render_state(
                self._controller.add_diagnostic(settings_diagnostic.render())
            )
        if self._settings_window is not None:
            self._settings_window.set_snapshot(result.canonical.current)
            self._settings_window.set_diagnostics(result.canonical.diagnostics)
        self._refresh_selection()

    def _settings_reload_observed(self, result: SettingsReloadResult) -> None:
        if self._closed:
            return
        if not result.applied:
            if self._settings_window is not None:
                self._settings_window.set_snapshot(result.snapshot)
                self._settings_window.set_diagnostics(
                    result.diagnostics,
                    context=(
                        "The configuration file was rejected; current values "
                        "remain active."
                    ),
                )
            for diagnostic in result.diagnostics:
                self._window.render_state(
                    self._controller.add_diagnostic(
                        f"configuration reload rejected: {diagnostic.render()}"
                    )
                )
            return
        if self._settings_window is not None:
            self._settings_window.set_snapshot(result.snapshot)
            self._settings_window.set_diagnostics(())

    @Slot(object)
    def _settings_changed(self, value: object) -> None:
        if self._closed or not isinstance(value, SettingsChange):
            return
        self._apply_settings_snapshot(value.current, value.changed_keys)

    def _apply_settings_snapshot(
        self, snapshot: SettingsSnapshot, changed_keys: tuple[str, ...]
    ) -> None:
        if self._settings_window is not None:
            self._settings_window.set_snapshot(snapshot)
            self._settings_window.set_diagnostics(())
        live_keys = set(changed_keys)
        display_keys = {
            "lyrics.display.original",
            "lyrics.display.romanized",
            "lyrics.display.translated",
        }
        if live_keys & display_keys:
            display = snapshot.representation_display
            self._controller.set_representation_settings(display)
            self._window.set_representation_settings(display)
            bundle = self._bundle
            token = self._controller.current_token
            if bundle is not None and token is not None:
                status_loader = getattr(self._frontend, "representation_statuses", None)
                statuses = (
                    status_loader(bundle, display)
                    if callable(status_loader)
                    else bundle.layer_statuses
                )
                self._controller.accept_resolution(
                    token,
                    bundle.resolution,
                    bundle.representations,
                    display,
                    statuses,
                    bundle.routing,
                )
                if self._publisher is not None and self._publisher.current is not None:
                    self._controller.accept_snapshot(self._publisher.current)
                self._window.render_state(self._controller.state)
        if "desktop.lyrics.selectable" in live_keys:
            self._window.set_interaction_settings(snapshot.desktop_interaction)
        if any(key.startswith("appearance.") for key in live_keys) or (
            live_keys & display_keys
        ):
            self._window.set_appearance_profile(snapshot.appearance)
        if live_keys & {"players.preferred", "players.ignored"}:
            self._refresh_selection()

    def _refresh_if_idle(self) -> None:
        if self._frontend is not None and self._track is None:
            self._refresh_selection()

    def _refresh_selection(self) -> None:
        if self._closed or self._frontend is None:
            return
        self._selection_serial += 1
        serial = self._selection_serial
        if self._selection_request is not None:
            self._selection_request.cancel()
            self._selection_request = None
        completed = False

        def players_discovered(
            players: object | None,
            error: BaseException | None,
        ) -> None:
            nonlocal completed
            completed = True
            self._selection_request = None
            if serial != self._selection_serial or self._closed:
                return
            if error is not None or not isinstance(players, PlayerListResult):
                self._clear_source()
                self._window.render_state(
                    self._controller.no_player(
                        (f"player discovery failed: {error or 'unknown failure'}",)
                    )
                )
                return
            self._player_suggestions = stable_player_suggestions(players)
            if self._settings_window is not None:
                self._settings_window.set_player_suggestions(self._player_suggestions)
            frontend = self._frontend
            if frontend is None:
                return

            def select() -> PlayerSelectionResult:
                return frontend.select_track(players)

            def selected(result: object | None, error: BaseException | None) -> None:
                if serial != self._selection_serial or self._closed:
                    return
                if error is not None or not isinstance(result, PlayerSelectionResult):
                    diagnostic = (
                        "unknown selection failure" if error is None else str(error)
                    )
                    self._clear_source()
                    self._window.render_state(
                        self._controller.no_player(
                            (f"player selection failed: {diagnostic}",)
                        )
                    )
                    return
                if result.selected is None:
                    self._clear_source()
                    self._window.render_state(
                        self._controller.no_player(
                            (*result.warnings, *result.unavailable_diagnostics)
                        )
                    )
                    return
                selected_track = result.selected.track
                session = self._playback_session
                current_track = self._track
                if (
                    session is not None
                    and current_track is not None
                    and selected_track.source_identity == current_track.source_identity
                    and selected_track.raw_snapshot.service_name
                    == current_track.raw_snapshot.service_name
                ):
                    # Selection-affecting events can still resolve to the current
                    # source. Refresh its raw playback context without flashing a
                    # resolving state or performing provider/storage work again.
                    self._track = selected_track
                    session.replace_source(
                        selected_track.raw_snapshot,
                        _session_id(selected_track),
                    )
                    self._sync_tick()
                    return
                self._begin_track(selected_track)

            self._start_job(select, selected)

        try:
            request = self._desktop_runtime.client.list_players_async(
                players_discovered
            )
        except (MprisBackendError, RuntimeError) as error:
            players_discovered(None, error)
            return
        if not completed:
            self._selection_request = request

    def _begin_track(self, track: ResolvedTrack) -> None:
        self._review_serial += 1
        if self._frontend is not None:
            self._frontend.cancel_inflight()
        self._clear_sync_only()
        self._track = track
        token = self._controller.begin_resolution(track)
        self._window.render_state(self._controller.state)
        self._load_serial += 1
        serial = self._load_serial
        frontend = self._frontend
        if frontend is None:
            return

        def load() -> FrontendLyricsBundle:
            return frontend.load_track(track)

        def loaded(result: object | None, error: BaseException | None) -> None:
            if serial != self._load_serial or self._closed:
                return
            if error is not None or not isinstance(result, FrontendLyricsBundle):
                diagnostic = (
                    "unknown lyric loading failure" if error is None else str(error)
                )
                if self._controller.fail_resolution(
                    token,
                    "Lyrics could not be loaded safely.",
                    (diagnostic,),
                ):
                    self._window.render_state(self._controller.state)
                return
            if not self._controller.accept_resolution(
                token,
                result.resolution,
                result.representations,
                result.display_settings,
                result.layer_statuses,
                result.routing,
            ):
                return
            self._bundle = result
            self._window.set_representation_settings(result.display_settings)
            self._window.render_state(self._controller.state)
            self._start_playback_session(result)

        self._start_job(load, loaded)

    def _start_playback_session(self, bundle: FrontendLyricsBundle) -> None:
        track = bundle.track
        clock = PlaybackClock(self._runtime.clock.monotonic_ns)
        self._playback_clock = clock
        self._playback_session = PlaybackSyncSession(
            clock,
            track.raw_snapshot,
            _session_id(track),
        )
        self._scheduler = AdaptiveResampler()
        timing = bundle.document_timing
        self._calibration = SynchronizationCalibration(
            AudioOutputLatency(
                None,
                None,
                "desktop output path is not route-qualified",
                limitations=(
                    "audio output latency is unavailable for automatic compensation",
                ),
            ),
            LyricTimingCalibration(
                0 if timing is None else timing.lyrics_display_delay_us,
                source="durable per-document display delay",
                limitations=("provider lyric timestamp error is unmeasured",),
            ),
            PresentationLatency(0, None, "desktop-widgets"),
        )
        self._publisher = SynchronizationPublisher()
        self._snapshot_subscription = self._publisher.subscribe(self._accept_snapshot)
        self._sync_timer.start()
        self._sync_tick()

    def _sync_tick(self) -> None:
        session = self._playback_session
        scheduler = self._scheduler
        track = self._track
        token = self._controller.current_token
        if session is None or scheduler is None or track is None or token is None:
            return
        now_ns = self._desktop_runtime.clock.monotonic_ns()
        scheduler.trigger(session.requested_reason, now_ns)
        if scheduler.due(now_ns):
            if self._position_request is None:
                self._start_position_sample(session)
            else:
                # Timer and signal demand collapse into the next scheduler pass.
                self._position_coalesced = True
        self._project_sync_state()

    def _start_position_sample(self, session: PlaybackSyncSession) -> None:
        """Start at most one desktop Position request for the active source."""

        request_context = session.begin_sample()
        self._position_request_serial += 1
        serial = self._position_request_serial
        completed = False

        def sampled(
            observation: PositionObservation | None,
            error: BaseException | None,
        ) -> None:
            nonlocal completed
            completed = True
            if serial != self._position_request_serial:
                return
            self._position_request = None
            if self._closed or session is not self._playback_session:
                return
            update = None
            if error is not None:
                if session.sample_is_current(request_context):
                    clock = self._playback_clock
                    if clock is not None:
                        update = clock.mark_sampling_failure(
                            f"Position sampling failed: {error}"
                        )
            elif observation is not None:
                update = session.complete_sample(request_context, observation)
            if update is not None:
                estimate = session.estimate(
                    duration_us=(
                        None
                        if self._track is None
                        else self._track.candidate.duration_us
                    )
                )
                scheduler = self._scheduler
                if estimate is not None and scheduler is not None:
                    scheduler.record(
                        update,
                        estimate.state,
                        estimate.monotonic_ns,
                        estimate.diagnostics.health,
                    )
            self._project_sync_state()
            coalesced = self._position_coalesced
            self._position_coalesced = False
            if coalesced and not self._closed:
                QTimer.singleShot(0, self._sync_tick)

        try:
            operation = self._desktop_runtime.timing.sample_async(
                request_context.snapshot,
                request_context.session_id,
                reason=request_context.reason,
                callback=sampled,
            )
        except (MprisBackendError, RuntimeError) as error:
            sampled(None, error)
            return
        if not completed:
            self._position_request = operation

    def _project_sync_state(self) -> None:
        """Interpolate and render locally without performing any D-Bus operation."""

        session = self._playback_session
        track = self._track
        token = self._controller.current_token
        if session is None or track is None or token is None:
            return
        estimate = session.estimate(duration_us=track.candidate.duration_us)
        if estimate is None:
            return
        if self._controller.update_playback(
            token.generation,
            estimate.state,
            estimate.position_us,
            track.candidate.duration_us,
        ):
            self._window.update_playback(self._controller.state)
        bundle = self._bundle
        if (
            bundle is None
            or bundle.resolution.status is not LyricsResolutionStatus.FOUND_TIMED
            or bundle.resolution.document is None
            or bundle.resolution.document.kind is not LyricDocumentKind.SYNCED
        ):
            return
        frame = synchronize(bundle.resolution.document, estimate, self._calibration)
        snapshot = build_sync_snapshot(
            generation=token.generation,
            track=track,
            document=bundle.resolution.document,
            estimate=estimate,
            frame=frame,
            calibration=self._calibration,
            representations=bundle.representations,
            lyrics_match_confidence=(
                None
                if bundle.resolution.confidence is None
                else bundle.resolution.confidence.value
            ),
        )
        if self._publisher is not None:
            self._publisher.publish(snapshot)

    def _accept_snapshot(self, snapshot: SynchronizationSnapshot) -> None:
        if self._controller.accept_snapshot(snapshot):
            self._window.render_state(self._controller.state)

    def _on_player_event(self, event: PlayerEvent) -> None:
        if self._closed:
            return
        session = self._playback_session
        if session is None:
            if self._track is not None and event.kind in {
                PlayerEventKind.PLAYER_APPEARED,
                PlayerEventKind.PLAYER_DISAPPEARED,
                PlayerEventKind.METADATA_CHANGED,
            }:
                self._load_serial += 1
                if self._frontend is not None:
                    self._frontend.cancel_inflight()
                self._track = None
                self._window.render_state(self._controller.source_changed())
            self._schedule_selection()
            return
        selected_service = session.snapshot.service_name
        if not session.handle_event(event):
            return
        selected_source_invalidated = (
            event.service_name == selected_service
            and event.kind
            in {
                PlayerEventKind.PLAYER_DISAPPEARED,
                PlayerEventKind.METADATA_CHANGED,
            }
        )
        if selected_source_invalidated:
            self._load_serial += 1
            self._window.render_state(self._controller.source_changed())
            self._clear_sync_only()
            self._schedule_selection()
            return
        if (
            session.requires_reload
            or event.kind is PlayerEventKind.PLAYBACK_STATUS_CHANGED
        ):
            # Re-run shared selection when another player may now outrank the
            # current one. Keep the valid current presentation until selection
            # actually chooses a different source.
            self._schedule_selection()
        if not session.requires_reload:
            self._sync_tick()

    def _schedule_selection(self) -> None:
        """Debounce selection and reject any result captured before this event."""

        self._selection_serial += 1
        self._selection_timer.start()

    @Slot()
    def _open_settings_window(self) -> None:
        settings_window = self._ensure_settings_window()
        settings_window.show()
        settings_window.raise_()
        settings_window.activateWindow()

    def _ensure_settings_window(self) -> SettingsWindow:
        if self._settings_window is not None:
            return self._settings_window
        service = self._settings_service
        path = (
            service.path
            if service is not None
            else (self._config_path or default_config_path())
        )
        if self._settings_window is None:
            settings_window = SettingsWindow(path, self._window)
            settings_window.change_requested.connect(self._change_setting)
            settings_window.reset_requested.connect(self._reset_setting)
            settings_window.reset_appearance_requested.connect(self._reset_appearance)
            self._settings_window = settings_window
            settings_window.set_player_suggestions(self._player_suggestions)
        if service is not None:
            self._settings_window.set_snapshot(service.current)
            self._settings_window.set_diagnostics(service.diagnostics)
        else:
            self._settings_window.set_loading()
        return self._settings_window

    @Slot(str, object)
    def _change_setting(self, key: str, value: object) -> None:
        service = self._settings_service
        if service is None:
            self._window.application_menu.project(self._window.appearance_profile)
            return
        settings_window = self._ensure_settings_window()
        if not settings_window.mark_pending(key):
            self._window.application_menu.project(service.current.appearance)
            return
        self._start_job(
            lambda: service.set(key, cast(SettingValue, value)),
            self._settings_operation_finished,
        )

    @Slot(str)
    def _reset_setting(self, key: str) -> None:
        service = self._settings_service
        settings_window = self._settings_window
        if service is None or settings_window is None:
            return
        if not settings_window.mark_pending(key):
            return
        self._start_job(
            lambda: service.reset(key),
            self._settings_operation_finished,
        )

    @Slot()
    def _reset_appearance(self) -> None:
        service = self._settings_service
        settings_window = self._settings_window
        if service is None or settings_window is None:
            return
        marker = "appearance"
        if not settings_window.mark_pending(marker):
            return
        keys = tuple(
            definition.key
            for definition in SETTINGS_SCHEMA
            if definition.key.startswith("appearance.")
        )
        self._start_job(
            lambda: service.reset_many(keys),
            self._settings_operation_finished,
        )

    def _settings_operation_finished(
        self, result: object | None, error: BaseException | None
    ) -> None:
        if self._closed:
            return
        settings_window = self._settings_window
        service = self._settings_service
        if settings_window is None or service is None:
            return
        settings_window.set_snapshot(service.current)
        if error is None and isinstance(result, SettingsReloadResult):
            settings_window.set_snapshot(result.snapshot)
            settings_window.set_diagnostics(())
            return
        if isinstance(error, SettingsValidationError):
            settings_window.set_diagnostics(
                error.diagnostics,
                context=(
                    "The proposed value was rejected; the previous value remains "
                    "active."
                ),
            )
        elif isinstance(error, SettingsFileError):
            settings_window.set_diagnostics(
                (error.diagnostic,),
                context=(
                    "The setting could not be saved; the previous value remains active."
                ),
            )
        else:
            settings_window.set_operation_error(
                "The setting could not be saved; the previous value remains active."
                + (f"\n{error}" if error is not None else "")
            )

    def _load_review(
        self,
        *,
        refresh: bool = False,
        title: str | None = None,
        artists: tuple[str, ...] = (),
        enrich_youtube: bool = False,
    ) -> None:
        """Load provider alternatives and audit evidence outside the UI thread."""

        frontend = self._frontend
        bundle = self._bundle
        track = self._track
        if frontend is None or bundle is None or track is None or self._closed:
            return
        self._review_serial += 1
        serial = self._review_serial
        source = track.source_identity

        def load() -> ReviewCorrectionSnapshot:
            if title is None and not artists and not refresh and not enrich_youtube:
                return frontend.review_track(bundle)
            return frontend.review_track(
                bundle,
                refresh=refresh,
                title=title,
                artists=artists,
                enrich_youtube=enrich_youtube,
            )

        def loaded(result: object | None, error: BaseException | None) -> None:
            current = self._track
            if (
                serial != self._review_serial
                or self._closed
                or current is None
                or current.source_identity != source
            ):
                return
            if error is not None or not isinstance(result, ReviewCorrectionSnapshot):
                diagnostic = "unknown review failure" if error is None else str(error)
                self._window.render_state(
                    self._controller.add_diagnostic(
                        f"review information could not be loaded: {diagnostic}"
                    )
                )
                return
            self._window.show_review(result)

        self._start_job(load, loaded)

    def _apply_correction(self, value: Any) -> None:
        """Dispatch one dialog request to the shared application service."""

        if not isinstance(value, CorrectionActionRequest):
            return
        frontend = self._frontend
        bundle = self._bundle
        track = self._track
        if frontend is None or bundle is None or track is None or self._closed:
            return
        if value.kind in {
            CorrectionActionKind.SEARCH_MATCHES,
            CorrectionActionKind.REFRESH_MATCHES,
            CorrectionActionKind.ENRICH_YOUTUBE,
        }:
            self._load_review(
                refresh=value.kind is CorrectionActionKind.REFRESH_MATCHES,
                title=value.title,
                artists=value.artists,
                enrich_youtube=value.kind is CorrectionActionKind.ENRICH_YOUTUBE,
            )
            return
        source = track.source_identity
        action = value

        def apply() -> None:
            if action.kind is CorrectionActionKind.PUT_TRACK_OVERRIDE:
                frontend.put_track_override(
                    track,
                    title=action.title or "",
                    artists=action.artists,
                )
            elif action.kind is CorrectionActionKind.RESET_TRACK_OVERRIDE:
                frontend.reset_track_override(track)
            elif action.kind is CorrectionActionKind.APPROVE_CURRENT:
                frontend.approve_current(bundle)
            elif action.kind is CorrectionActionKind.REJECT_CURRENT:
                frontend.reject_current(bundle)
            elif action.kind is CorrectionActionKind.CHOOSE_ALTERNATIVE:
                if action.alternative is None:
                    raise ValueError("no alternative lyric result was selected")
                frontend.choose_alternative(track, action.alternative)
            elif action.kind is CorrectionActionKind.REJECT_ALTERNATIVE:
                if action.alternative is None:
                    raise ValueError("no alternative lyric result was selected")
                frontend.reject_alternative(track, action.alternative)
            elif action.kind is CorrectionActionKind.RESET_MATCH:
                frontend.reset_match(track)
            elif action.kind is CorrectionActionKind.SET_LANGUAGE_ZH:
                frontend.set_document_language(bundle, "zh")
            elif action.kind is CorrectionActionKind.SET_LANGUAGE_JA:
                frontend.set_document_language(bundle, "ja")
            elif action.kind is CorrectionActionKind.RESET_LANGUAGE:
                frontend.reset_document_language(bundle)
            elif action.kind is CorrectionActionKind.PUT_TRANSLATION:
                if action.source_line_id is None or not action.text:
                    raise ValueError(
                        "an exact original line and non-blank translation are required"
                    )
                frontend.put_translation(
                    bundle,
                    source_line_id=action.source_line_id,
                    text=action.text,
                )
            elif action.kind is CorrectionActionKind.RESET_TRANSLATION:
                if action.source_line_id is None:
                    raise ValueError("an exact original lyric line is required")
                frontend.reset_translation(
                    bundle,
                    source_line_id=action.source_line_id,
                )
            elif action.kind is CorrectionActionKind.SET_DELAY:
                document = bundle.resolution.document
                if document is None or action.delay_us is None:
                    raise ValueError("no lyric document delay can be changed")
                frontend.set_display_delay(bundle, action.delay_us)
            elif action.kind is CorrectionActionKind.RESET_DELAY:
                document = bundle.resolution.document
                if document is None:
                    raise ValueError("no lyric document delay can be reset")
                frontend.reset_display_delay(bundle)

        def applied(result: object | None, error: BaseException | None) -> None:
            del result
            current = self._track
            if self._closed or current is None or current.source_identity != source:
                return
            if error is not None:
                self._window.render_state(
                    self._controller.add_diagnostic(
                        f"correction could not be applied: {error}"
                    )
                )
                return
            if action.kind in {
                CorrectionActionKind.PUT_TRACK_OVERRIDE,
                CorrectionActionKind.RESET_TRACK_OVERRIDE,
            }:
                self._load_serial += 1
                self._review_serial += 1
                self._clear_sync_only()
                self._track = None
                self._window.render_state(self._controller.source_changed())
                self._refresh_selection()
            else:
                self._begin_track(current)

        self._start_job(apply, applied)

    def _clear_sync_only(self) -> None:
        self._sync_timer.stop()
        self._position_request_serial += 1
        if self._position_request is not None:
            self._position_request.cancel()
        self._position_request = None
        self._position_coalesced = False
        if self._snapshot_subscription is not None:
            self._snapshot_subscription.close()
        self._snapshot_subscription = None
        self._publisher = None
        self._playback_session = None
        self._playback_clock = None
        self._scheduler = None
        self._bundle = None

    def _clear_source(self) -> None:
        self._load_serial += 1
        self._review_serial += 1
        self._clear_sync_only()
        self._track = None

    def _start_library_scan(self) -> None:
        """Run a configured library scan on the bounded desktop worker pool."""

        if self._closed or self._library_cancellation is not None:
            return
        from konokashi.application.library_scan import LibraryScanService
        from konokashi.infrastructure.lyrics.library_download import (
            LibraryLyricsDownloader,
        )
        from konokashi.infrastructure.metadata.library import (
            MusicDirectoryFilesystem,
            MutagenLibraryMetadataReader,
        )

        cancellation = Event()
        self._library_cancellation = cancellation
        self._window.set_library_scan_state(
            True, "Scanning configured roots in background; activate to cancel."
        )

        def scan() -> _LibraryScanResult:
            storage = open_storage(self._database_path)
            canonical = self._settings_service or open_settings(
                storage, config_path=self._config_path
            )
            settings = canonical.get_library()
            downloader = (
                LibraryLyricsDownloader(storage, LrclibLyricsProvider())
                if settings.automatic_downloads
                else None
            )
            summary = LibraryScanService(
                MusicDirectoryFilesystem(),
                MutagenLibraryMetadataReader(),
                storage.library,
                downloader=downloader,
                overrides=storage.track_overrides,
                settings=settings,
            ).scan(cancellation=cancellation)
            return _LibraryScanResult(summary, storage.library.review_items(limit=100))

        def scanned(result: object | None, error: BaseException | None) -> None:
            if self._closed or cancellation is not self._library_cancellation:
                return
            self._library_cancellation = None
            if error is not None or not isinstance(result, _LibraryScanResult):
                diagnostic = "unknown scan failure" if error is None else str(error)
                self._window.set_library_scan_state(
                    False, f"Library scan could not start: {diagnostic}"
                )
                return
            summary = result.summary
            self._window.set_library_scan_result(summary, result.review_items)
            self._window.set_library_scan_state(
                False,
                f"Library scan {summary.status.value}: "
                f"{summary.discovered} discovered, {summary.processed} processed, "
                f"{summary.unchanged} unchanged, {summary.moved} moved, "
                f"{summary.missing} missing, {summary.review} for review, "
                f"{summary.downloaded} downloaded, "
                f"{summary.download_misses} download misses, "
                f"{summary.errors} errors.",
            )

        self._start_job(scan, scanned)

    def _cancel_library_scan(self) -> None:
        """Cooperatively stop discovery without reconciling unseen deletions."""

        if self._library_cancellation is not None:
            self._library_cancellation.set()
            self._window.set_library_scan_state(True, "Cancelling library scan safely…")

    def _start_job(
        self,
        function: Callable[[], object],
        callback: Callable[[object | None, BaseException | None], None],
    ) -> None:
        if self._closed:
            return
        job_id = self._next_job_id
        self._next_job_id += 1
        self._jobs[job_id] = callback
        self._pool.start(_FunctionJob(job_id, function, self._job_signals))

    @Slot(int, object, object)
    def _job_completed(
        self,
        job_id: int,
        result: object | None,
        error: BaseException | None,
    ) -> None:
        callback = self._jobs.pop(job_id, None)
        if callback is not None:
            callback(result, error)

    @Slot()
    def close(self) -> None:
        """Stop timers/subscriptions and reject every late worker result."""

        if self._closed:
            return
        self._closed = True
        if self._settings_watcher is not None:
            self._settings_watcher.close()
            self._settings_watcher = None
        if self._settings_subscription is not None:
            self._settings_subscription.close()
            self._settings_subscription = None
        if self._settings_window is not None:
            self._settings_window.close()
            self._settings_window = None
        if self._frontend is not None:
            self._frontend.cancel_inflight()
        if self._library_cancellation is not None:
            self._library_cancellation.set()
            self._library_cancellation = None
        self._selection_timer.stop()
        self._idle_timer.stop()
        if self._monitor_request is not None:
            self._monitor_request.cancel()
            self._monitor_request = None
        if self._selection_request is not None:
            self._selection_request.cancel()
            self._selection_request = None
        self._clear_source()
        self._pool.clear()
        self._pool.close()
        self._jobs.clear()
        if self._monitor_started:
            with suppress(RuntimeError):
                self._desktop_runtime.monitor.close()
        self._monitor_started = False
        with suppress(RuntimeError):
            self._desktop_runtime.close()
