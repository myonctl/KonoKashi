"""Qt lifecycle coordinator for presentation-neutral application services."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from lyriflux.application.clock_lifecycle import AdaptiveResampler
from lyriflux.application.desktop_state import DesktopStateController
from lyriflux.application.frontend_session import (
    FrontendLyricsBundle,
    FrontendSessionPort,
)
from lyriflux.application.lyrics_sync import synchronize
from lyriflux.application.playback_clock import PlaybackClock
from lyriflux.application.ports import MprisRuntimePort
from lyriflux.application.review_corrections import ReviewCorrectionSnapshot
from lyriflux.application.settings import DesktopInteractionSettings
from lyriflux.application.sync_session import PlaybackSyncSession
from lyriflux.application.sync_state import (
    SnapshotSubscription,
    SynchronizationPublisher,
    SynchronizationSnapshot,
    build_sync_snapshot,
)
from lyriflux.domain.library import LibraryScanSummary
from lyriflux.domain.lyrics import LyricDocumentKind, LyricsResolutionStatus
from lyriflux.domain.models import PlayerEvent, PlayerEventKind
from lyriflux.domain.representations import RepresentationDisplaySettings
from lyriflux.domain.synchronization import (
    AudioOutputLatency,
    LyricTimingCalibration,
    PresentationLatency,
    SynchronizationCalibration,
)
from lyriflux.domain.tracks import PlayerSelectionResult, ResolvedTrack
from lyriflux.infrastructure.frontend import create_frontend_session
from lyriflux.infrastructure.lyrics.lrclib import LrclibLyricsProvider
from lyriflux.infrastructure.mpris.backend import MprisBackendError
from lyriflux.infrastructure.mpris.qt_dbus_client import create_qt_mpris_runtime
from lyriflux.infrastructure.storage.bootstrap import open_storage
from lyriflux.presentation.desktop.main_window import DesktopSettingsUpdate, MainWindow
from lyriflux.presentation.desktop.review_dialog import (
    CorrectionActionKind,
    CorrectionActionRequest,
)


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
    interactions: DesktopInteractionSettings


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
        self._review_serial = 0
        self._library_cancellation: Event | None = None

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
        self._window.review_requested.connect(self._load_review)
        self._window.correction_requested.connect(self._apply_correction)
        self._window.library_scan_requested.connect(self._start_library_scan)
        self._window.library_scan_cancel_requested.connect(self._cancel_library_scan)
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
            storage.settings.get_desktop_interaction(),
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
                    "LyriFlux storage could not be initialized.", (diagnostic,)
                )
            )
            return
        self._frontend = result.frontend
        self._controller.set_representation_settings(result.settings)
        self._window.set_representation_settings(result.settings)
        self._window.set_interaction_settings(result.interactions)
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

    def _save_display_settings(self, value: Any) -> None:
        if not isinstance(value, DesktopSettingsUpdate):
            return
        representations = value.representations
        interactions = value.interactions
        self._controller.set_representation_settings(representations)
        self._window.set_representation_settings(representations)
        self._window.set_interaction_settings(interactions)
        bundle = self._bundle
        token = self._controller.current_token
        if bundle is not None and token is not None:
            self._controller.accept_resolution(
                token,
                bundle.resolution,
                bundle.representations,
                representations,
            )
            if self._publisher is not None and self._publisher.current is not None:
                self._controller.accept_snapshot(self._publisher.current)
            self._window.render_state(self._controller.state)
        frontend = self._frontend
        if frontend is None:
            return
        self._start_job(
            lambda: self._persist_display_settings(
                frontend, representations, interactions
            ),
            self._settings_saved,
        )

    @staticmethod
    def _persist_display_settings(
        frontend: FrontendSessionPort,
        representations: RepresentationDisplaySettings,
        interactions: DesktopInteractionSettings,
    ) -> None:
        frontend.put_display_settings(representations)
        frontend.put_interaction_settings(interactions)

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

    def _load_review(self) -> None:
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
            return frontend.review_track(bundle)

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
            elif action.kind is CorrectionActionKind.RESET_MATCH:
                frontend.reset_match(track)
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
        from lyriflux.application.library_scan import LibraryScanService
        from lyriflux.infrastructure.lyrics.library_download import (
            LibraryLyricsDownloader,
        )
        from lyriflux.infrastructure.metadata.library import (
            MusicDirectoryFilesystem,
            MutagenLibraryMetadataReader,
        )

        cancellation = Event()
        self._library_cancellation = cancellation
        self._window.set_library_scan_state(
            True, "Scanning configured roots in background; activate to cancel."
        )

        def scan() -> LibraryScanSummary:
            storage = open_storage(self._database_path)
            settings = storage.library.get_settings()
            downloader = (
                LibraryLyricsDownloader(storage, LrclibLyricsProvider())
                if settings.automatic_downloads
                else None
            )
            return LibraryScanService(
                MusicDirectoryFilesystem(),
                MutagenLibraryMetadataReader(),
                storage.library,
                downloader=downloader,
                overrides=storage.track_overrides,
            ).scan(cancellation=cancellation)

        def scanned(result: object | None, error: BaseException | None) -> None:
            if self._closed or cancellation is not self._library_cancellation:
                return
            self._library_cancellation = None
            if error is not None or not isinstance(result, LibraryScanSummary):
                diagnostic = "unknown scan failure" if error is None else str(error)
                self._window.set_library_scan_state(
                    False, f"Library scan could not start: {diagnostic}"
                )
                return
            state = "cancelled" if result.cancelled else "complete"
            self._window.set_library_scan_state(
                False,
                f"Library scan {state}: {result.processed} processed, "
                f"{result.unchanged} unchanged, {result.review} for review.",
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
        if self._frontend is not None:
            self._frontend.cancel_inflight()
        if self._library_cancellation is not None:
            self._library_cancellation.set()
            self._library_cancellation = None
        self._selection_timer.stop()
        self._idle_timer.stop()
        self._clear_source()
        self._pool.clear()
        self._jobs.clear()
        if self._monitor_started:
            with suppress(RuntimeError):
                self._runtime.monitor.close()
        self._monitor_started = False
