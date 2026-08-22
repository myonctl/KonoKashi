"""Qt lifecycle coordinator for the presentation-neutral Stage 1-6 services."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from lyricflow.application.clock_lifecycle import AdaptiveResampler
from lyricflow.application.desktop_state import DesktopStateController
from lyricflow.application.frontend_session import (
    FrontendLyricsBundle,
    FrontendSessionPort,
)
from lyricflow.application.lyrics_sync import synchronize
from lyricflow.application.playback_clock import PlaybackClock
from lyricflow.application.ports import MprisRuntimePort
from lyricflow.application.sync_session import PlaybackSyncSession
from lyricflow.application.sync_state import (
    SnapshotSubscription,
    SynchronizationPublisher,
    SynchronizationSnapshot,
    build_sync_snapshot,
)
from lyricflow.domain.lyrics import LyricDocumentKind, LyricsResolutionStatus
from lyricflow.domain.models import PlayerEvent, PlayerEventKind
from lyricflow.domain.representations import RepresentationDisplaySettings
from lyricflow.domain.synchronization import (
    AudioOutputLatency,
    LyricTimingCalibration,
    PresentationLatency,
    SynchronizationCalibration,
)
from lyricflow.domain.tracks import PlayerSelectionResult, ResolvedTrack
from lyricflow.infrastructure.frontend import create_frontend_session
from lyricflow.infrastructure.lyrics.lrclib import LrclibLyricsProvider
from lyricflow.infrastructure.mpris.backend import MprisBackendError
from lyricflow.infrastructure.mpris.qt_dbus_client import create_qt_mpris_runtime
from lyricflow.infrastructure.storage.bootstrap import open_storage
from lyricflow.presentation.desktop.main_window import MainWindow


class _JobSignals(QObject):
    completed = Signal(int, object, object)


class _FunctionJob(QRunnable):
    """Run one bounded blocking application operation outside the UI thread."""

    def __init__(
        self,
        job_id: int,
        function: Callable[[], object],
        signals: _JobSignals,
    ) -> None:
        super().__init__()
        self.job_id = job_id
        self._function = function
        self._signals = signals

    @Slot()
    def run(self) -> None:
        try:
            result = self._function()
        except Exception as error:  # boundary converts to a controlled UI state
            self._signals.completed.emit(self.job_id, None, error)
        else:
            self._signals.completed.emit(self.job_id, result, None)


@dataclass(frozen=True, slots=True)
class _InitializedServices:
    frontend: FrontendSessionPort
    settings: RepresentationDisplaySettings


def _session_id(track: ResolvedTrack) -> str:
    """Use a stable process-local source representation for clock resets."""

    return f"desktop:{track.source_identity!r}"


class DesktopCoordinator(QObject):
    """Keep widgets passive while coordinating Qt events and background work."""

    def __init__(
        self,
        application: QApplication,
        window: MainWindow,
        *,
        database_path: Path | None = None,
        runtime: MprisRuntimePort | None = None,
    ) -> None:
        super().__init__(application)
        self._application = application
        self._window = window
        self._database_path = database_path
        self._runtime: MprisRuntimePort = runtime or create_qt_mpris_runtime()
        self._controller = DesktopStateController()
        self._frontend: FrontendSessionPort | None = None
        self._track: ResolvedTrack | None = None
        self._bundle: FrontendLyricsBundle | None = None
        self._playback_session: PlaybackSyncSession | None = None
        self._playback_clock: PlaybackClock | None = None
        self._scheduler: AdaptiveResampler | None = None
        self._calibration = SynchronizationCalibration()
        self._publisher: SynchronizationPublisher | None = None
        self._snapshot_subscription: SnapshotSubscription | None = None
        self._monitor_started = False
        self._closed = False
        self._selection_serial = 0
        self._load_serial = 0

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
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

        self._window.settings_requested.connect(self._save_display_settings)
        self._application.aboutToQuit.connect(self.close)

    @property
    def controller(self) -> DesktopStateController:
        """Expose semantic state for deterministic lifecycle tests."""

        return self._controller

    def start(self) -> None:
        """Start monitoring immediately and initialize storage off-thread."""

        self._window.render_state(self._controller.state)
        try:
            started = self._runtime.monitor.start(self._on_player_event)
        except RuntimeError as error:
            self._window.render_state(
                self._controller.application_error(
                    "Unable to monitor media players.", (str(error),)
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

    def _initialize_services(self) -> _InitializedServices:
        storage = open_storage(self._database_path)
        frontend = create_frontend_session(storage, LrclibLyricsProvider())
        return _InitializedServices(
            frontend,
            storage.settings.get_representation_display(),
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
                    "LyricFlow storage could not be initialized.", (diagnostic,)
                )
            )
            return
        self._frontend = result.frontend
        self._controller.set_representation_settings(result.settings)
        self._window.set_representation_settings(result.settings)
        self._refresh_selection()

    def _refresh_if_idle(self) -> None:
        if self._frontend is not None and self._track is None:
            self._refresh_selection()

    def _refresh_selection(self) -> None:
        if self._closed or self._frontend is None:
            return
        self._selection_serial += 1
        serial = self._selection_serial
        try:
            players = self._runtime.client.list_players()
        except (MprisBackendError, RuntimeError) as error:
            self._clear_source()
            self._window.render_state(
                self._controller.no_player((f"player discovery failed: {error}",))
            )
            return
        frontend = self._frontend

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
            self._begin_track(result.selected.track)

        self._start_job(select, selected)

    def _begin_track(self, track: ResolvedTrack) -> None:
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
        now_ns = self._runtime.clock.monotonic_ns()
        scheduler.trigger(session.requested_reason, now_ns)
        sampled = scheduler.due(now_ns)
        update = None
        if sampled:
            try:
                update = session.sample(self._runtime.timing)
            except (MprisBackendError, RuntimeError) as error:
                clock = self._playback_clock
                if clock is not None:
                    update = clock.mark_sampling_failure(
                        f"Position sampling failed: {error}"
                    )
        estimate = session.estimate(duration_us=track.candidate.duration_us)
        if estimate is None:
            return
        if update is not None:
            scheduler.record(
                update,
                estimate.state,
                estimate.monotonic_ns,
                estimate.diagnostics.health,
            )
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
            self._selection_timer.start()
            return
        if session.handle_event(event):
            if session.requires_reload:
                self._load_serial += 1
                self._window.render_state(self._controller.source_changed())
                self._clear_sync_only()
                self._selection_timer.start()
            else:
                self._sync_tick()

    def _save_display_settings(self, value: Any) -> None:
        if not isinstance(value, RepresentationDisplaySettings):
            return
        self._controller.set_representation_settings(value)
        bundle = self._bundle
        token = self._controller.current_token
        if bundle is not None and token is not None:
            self._controller.accept_resolution(
                token,
                bundle.resolution,
                bundle.representations,
                value,
            )
            if self._publisher is not None and self._publisher.current is not None:
                self._controller.accept_snapshot(self._publisher.current)
            self._window.render_state(self._controller.state)
        frontend = self._frontend
        if frontend is None:
            return
        self._start_job(
            lambda: self._persist_display_settings(frontend, value),
            self._settings_saved,
        )

    @staticmethod
    def _persist_display_settings(
        frontend: FrontendSessionPort,
        value: RepresentationDisplaySettings,
    ) -> None:
        frontend.put_display_settings(value)

    def _settings_saved(
        self, result: object | None, error: BaseException | None
    ) -> None:
        del result
        if error is not None and not self._closed:
            self._window.render_state(
                self._controller.add_diagnostic(
                    f"display settings could not be saved: {error}"
                )
            )

    def _clear_sync_only(self) -> None:
        self._sync_timer.stop()
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
        self._clear_sync_only()
        self._track = None

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
        if self._frontend is not None:
            self._frontend.cancel_inflight()
        self._selection_timer.stop()
        self._idle_timer.stop()
        self._clear_source()
        self._pool.clear()
        self._jobs.clear()
        if self._monitor_started:
            with suppress(RuntimeError):
                self._runtime.monitor.close()
        self._monitor_started = False
