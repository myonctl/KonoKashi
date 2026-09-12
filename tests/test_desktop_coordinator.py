"""Deterministic player-event wiring regressions for the desktop coordinator."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox

from konokashi import cli
from konokashi.application.artwork import (
    ArtworkAsset,
    ArtworkLoadResult,
    ArtworkLoadStatus,
)
from konokashi.application.clock_lifecycle import AdaptiveResampler
from konokashi.application.frontend_session import FrontendLyricsBundle
from konokashi.application.playback_clock import PlaybackClock
from konokashi.application.settings import (
    SETTINGS_SCHEMA,
    DesktopInteractionSettings,
    validate_settings_values,
)
from konokashi.application.settings_service import (
    CanonicalSettingsService,
    SettingsChange,
)
from konokashi.application.sync_session import PlaybackSyncSession
from konokashi.domain.identity import GenericMprisIdentity
from konokashi.domain.library import LibraryScanSummary
from konokashi.domain.lyric_corrections import LyricLineEdit
from konokashi.domain.lyrics import (
    LyricsResolutionResult,
    LyricsResolutionStatus,
    LyricTimingLevel,
)
from konokashi.domain.models import (
    PlayerEvent,
    PlayerEventKind,
    PlayerInspection,
    PlayerListResult,
    PlayerWatchStart,
)
from konokashi.domain.representations import RepresentationDisplaySettings
from konokashi.domain.synchronization import (
    LyricDocumentTiming,
    PlaybackState,
    PositionObservation,
)
from konokashi.domain.tracks import (
    PlayerAssessment,
    PlayerSelectionResult,
    ResolvedTrack,
)
from konokashi.infrastructure.configuration.toml_file import TomlSettingsFile
from konokashi.presentation.desktop.coordinator import DesktopCoordinator
from konokashi.presentation.desktop.main_window import MainWindow
from konokashi.presentation.desktop.review_dialog import (
    CorrectionActionKind,
    CorrectionActionRequest,
)
from konokashi.presentation.desktop.settings_window import OrderedStringListEditor
from tests.test_desktop_state import _snapshot, _track
from tests.test_desktop_widgets import _review_snapshot
from tests.test_lyrics_sync import document as lyric_document


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    application = QApplication.instance() or QApplication(["coordinator-test"])
    assert isinstance(application, QApplication)
    return application


class _Client:
    def __init__(self) -> None:
        self.result = PlayerListResult()

    def list_players(self) -> PlayerListResult:
        return self.result

    def inspect_player(self, service_name: str) -> PlayerInspection:
        return next(
            item for item in self.result.players if item.service_name == service_name
        )

    def list_players_async(self, callback):  # type: ignore[no-untyped-def]
        callback(self.result, None)
        return _Pending()


class _Pending:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _Monitor:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def start_async(self, _handler, callback):  # type: ignore[no-untyped-def]
        callback(PlayerWatchStart(), None)
        return _Pending()


class _Clock:
    def monotonic_ns(self) -> int:
        return 0

    def boottime_ns(self) -> int | None:
        return None


class _Runtime:
    def __init__(self) -> None:
        self.client = _Client()
        self.monitor = _Monitor()
        self.clock = _Clock()
        self.timing = None
        self.desktop = self

    def close(self) -> None:
        self.monitor.close()


class _ArtworkLoader:
    def __init__(self) -> None:
        self.calls: list[str | None] = []

    def load(self, uri: str | None) -> ArtworkLoadResult:
        self.calls.append(uri)
        color = "#AA2200" if uri is not None and uri.endswith("a.png") else "#0066CC"
        red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
        asset = ArtworkAsset(bytes((red, green, blue, 255)), 1, 1, color)
        return ArtworkLoadResult(ArtworkLoadStatus.LOADED, asset)


def _with_artwork(track: ResolvedTrack, uri: str) -> ResolvedTrack:
    snapshot = replace(
        track.raw_snapshot,
        metadata=replace(track.raw_snapshot.metadata, art_url=uri),
    )
    return replace(track, raw_snapshot=snapshot)


class _DeferredTiming:
    def __init__(self) -> None:
        self.calls = []

    def sample_async(
        self,
        snapshot,
        session_id,
        *,
        reason,
        callback,
    ):  # type: ignore[no-untyped-def]
        pending = _Pending()
        self.calls.append((snapshot, session_id, reason, callback, pending))
        return pending

    def complete(self, index: int, position_us: int) -> None:
        _snapshot_value, session_id, reason, callback, _pending = self.calls[index]
        callback(
            PositionObservation(
                session_id,
                position_us,
                PlaybackState.PLAYING,
                1.0,
                index * 1_000_000,
                index * 1_000_000 + 100_000,
                reason,
            ),
            None,
        )


class _Frontend:
    def __init__(self, selected: ResolvedTrack | None) -> None:
        self.selected = selected
        self.load_calls: list[ResolvedTrack] = []
        self.cancellations = 0
        self.display_settings: RepresentationDisplaySettings | None = None
        self.interaction_settings: DesktopInteractionSettings | None = None
        self.review_calls: list[FrontendLyricsBundle] = []
        self.review_options: list[dict[str, object]] = []
        self.correction_calls: list[tuple[str, object]] = []

    def select_track(
        self, players: PlayerListResult, *, player_override: str | None = None
    ) -> PlayerSelectionResult:
        del players
        self.player_override = player_override
        if self.selected is None:
            return PlayerSelectionResult(None)
        return PlayerSelectionResult(
            PlayerAssessment(self.selected, ("selected",), (1,)),
            warnings=(
                ()
                if player_override is None
                else (f"temporary player override active: {player_override}",)
            ),
        )

    def load_track(self, track: ResolvedTrack) -> FrontendLyricsBundle:
        self.load_calls.append(track)
        return FrontendLyricsBundle(
            track,
            LyricsResolutionResult(
                track.source_identity,
                LyricsResolutionStatus.NO_RESULT,
            ),
            (),
            RepresentationDisplaySettings(),
            None,
        )

    def put_display_settings(self, settings: RepresentationDisplaySettings) -> None:
        self.display_settings = settings

    def put_interaction_settings(self, settings: DesktopInteractionSettings) -> None:
        self.interaction_settings = settings

    def cancel_inflight(self) -> None:
        self.cancellations += 1

    def review_track(self, bundle: FrontendLyricsBundle, **kwargs):  # type: ignore[no-untyped-def]
        self.review_calls.append(bundle)
        self.review_options.append(kwargs)
        return _review_snapshot()

    def put_track_override(
        self,
        track: ResolvedTrack,
        *,
        title: str,
        artists: tuple[str, ...],
        album: str | None,
    ) -> None:
        self.correction_calls.append(("track", (track, title, artists, album)))

    def reset_track_override(self, track: ResolvedTrack) -> bool:
        self.correction_calls.append(("reset-track", track))
        return True

    def approve_current(self, bundle: FrontendLyricsBundle) -> None:
        self.correction_calls.append(("approve", bundle))

    def reject_current(self, bundle: FrontendLyricsBundle) -> None:
        self.correction_calls.append(("reject", bundle))

    def choose_alternative(self, track, alternative):  # type: ignore[no-untyped-def]
        self.correction_calls.append(("alternative", (track, alternative)))

    def reset_match(self, track: ResolvedTrack) -> bool:
        self.correction_calls.append(("reset-match", track))
        return True

    def set_document_language(
        self, bundle: FrontendLyricsBundle, language: str
    ) -> None:
        self.correction_calls.append(("language", (bundle, language)))

    def reset_document_language(self, bundle: FrontendLyricsBundle) -> bool:
        self.correction_calls.append(("reset-language", bundle))
        return True

    def put_translation(
        self,
        bundle: FrontendLyricsBundle,
        *,
        source_line_id: str,
        text: str,
    ) -> None:
        self.correction_calls.append(("translation", (bundle, source_line_id, text)))

    def reset_translation(
        self, bundle: FrontendLyricsBundle, *, source_line_id: str
    ) -> bool:
        self.correction_calls.append(("reset-translation", (bundle, source_line_id)))
        return True

    def set_display_delay(self, bundle: FrontendLyricsBundle, delay_us: int) -> None:
        self.correction_calls.append(("delay", (bundle, delay_us)))

    def reset_display_delay(self, bundle: FrontendLyricsBundle) -> bool:
        self.correction_calls.append(("reset-delay", bundle))
        return True

    def put_lyric_edits(
        self, bundle: FrontendLyricsBundle, edits: tuple[LyricLineEdit, ...]
    ) -> int:
        self.correction_calls.append(("lyric-edits", (bundle, edits)))
        return len(edits)

    def reset_lyric_edits(self, bundle: FrontendLyricsBundle) -> int:
        self.correction_calls.append(("reset-lyric-edits", bundle))
        return 1

    def import_lyric_text(self, bundle: FrontendLyricsBundle, text: str) -> int:
        self.correction_calls.append(("import-lyric-text", (bundle, text)))
        return 1


class _ImmediateCoordinator(DesktopCoordinator):
    """Run application jobs inline and omit clock sampling for wiring tests."""

    def __init__(
        self, application: QApplication, window: MainWindow, **options: object
    ) -> None:
        self.runtime = _Runtime()
        self.sync_ticks = 0
        self.started_bundles: list[FrontendLyricsBundle] = []
        super().__init__(
            application,
            window,
            runtime=self.runtime,  # type: ignore[arg-type]
            **options,
        )

    def _start_job(
        self,
        function: Callable[[], object],
        callback: Callable[[object | None, BaseException | None], None],
    ) -> None:
        try:
            result = function()
        except Exception as error:  # match the production worker boundary
            callback(None, error)
        else:
            callback(result, None)

    def _sync_tick(self) -> None:
        self.sync_ticks += 1

    def _start_playback_session(self, bundle: FrontendLyricsBundle) -> None:
        self.started_bundles.append(bundle)


class _DeferredCoordinator(_ImmediateCoordinator):
    """Finish worker functions now while deferring their UI-thread callbacks."""

    def __init__(self, application: QApplication, window: MainWindow) -> None:
        self.pending: list[
            tuple[
                Callable[[object | None, BaseException | None], None],
                object | None,
                BaseException | None,
            ]
        ] = []
        super().__init__(application, window)

    def _start_job(
        self,
        function: Callable[[], object],
        callback: Callable[[object | None, BaseException | None], None],
    ) -> None:
        try:
            pending = (callback, function(), None)
        except Exception as error:  # match the production worker boundary
            pending = (callback, None, error)
        self.pending.append(pending)

    def complete_next(self) -> None:
        callback, result, error = self.pending.pop(0)
        callback(result, error)

    def complete(self, index: int) -> None:
        callback, result, error = self.pending.pop(index)
        callback(result, error)


class _ControlledCoordinator(_ImmediateCoordinator):
    """Start selected application jobs only when the test releases them."""

    def __init__(self, application: QApplication, window: MainWindow) -> None:
        self.pending_work: list[
            tuple[
                Callable[[], object],
                Callable[[object | None, BaseException | None], None],
            ]
        ] = []
        super().__init__(application, window)

    def _start_job(
        self,
        function: Callable[[], object],
        callback: Callable[[object | None, BaseException | None], None],
    ) -> None:
        self.pending_work.append((function, callback))

    def complete(self, index: int) -> None:
        function, callback = self.pending_work.pop(index)
        try:
            result = function()
        except Exception as error:
            callback(None, error)
        else:
            callback(result, None)


class _CalibrationCoordinator(DesktopCoordinator):
    """Use production timing composition without starting MPRIS sampling."""

    def _sync_tick(self) -> None:
        pass


def test_settings_update_uses_canonical_service_and_applies_live_selection(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    window = MainWindow()
    coordinator = _ImmediateCoordinator(qt_app, window)
    config_path = tmp_path / "config.toml"
    service = CanonicalSettingsService(TomlSettingsFile(config_path))
    assert service.initialize().applied
    coordinator._settings_service = service
    coordinator._settings_subscription = service.subscribe(
        lambda change: coordinator.settings_change_observed.emit(change)
    )
    window.settings_action.trigger()

    coordinator._change_setting("desktop.lyrics.selectable", True)

    assert service.get("desktop.lyrics.selectable") is True
    assert "selectable = true" in config_path.read_text(encoding="utf-8")
    assert window.interaction_settings == DesktopInteractionSettings(True)
    assert (
        window.active_band.textInteractionFlags()
        & Qt.TextInteractionFlag.TextSelectableByMouse
    )
    coordinator.close()
    window.close()


def test_hot_reload_applies_live_desktop_interaction_setting(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    coordinator = _ImmediateCoordinator(qt_app, window)
    snapshot = validate_settings_values(
        {"desktop.lyrics.selectable": True},
        explicit_keys=frozenset({"desktop.lyrics.selectable"}),
    )

    previous = validate_settings_values({})
    coordinator._settings_changed(
        SettingsChange(
            previous,
            snapshot,
            ("desktop.lyrics.selectable",),
        )
    )

    assert window.interaction_settings == DesktopInteractionSettings(True)
    assert (
        window.active_band.textInteractionFlags()
        & Qt.TextInteractionFlag.TextSelectableByMouse
    )
    coordinator.close()
    window.close()


def test_settings_window_reuses_one_instance_and_round_trips_gui_file_cli(
    qt_app: QApplication,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "dotfiles" / "konokashi.toml"
    target.parent.mkdir()
    target.write_text("# retained comment\nschema_version = 1\n", encoding="utf-8")
    path = tmp_path / "config.toml"
    path.symlink_to(target)
    service = CanonicalSettingsService(TomlSettingsFile(path))
    assert service.initialize().applied

    window = MainWindow()
    window.show()
    qt_app.processEvents()
    coordinator = _ImmediateCoordinator(qt_app, window)
    coordinator._settings_service = service
    coordinator._settings_subscription = service.subscribe(
        lambda change: coordinator.settings_change_observed.emit(change)
    )
    coordinator._open_settings_window()
    settings_window = coordinator._settings_window
    assert settings_window is not None
    assert settings_window.isVisible()
    settings_window.close()
    assert window.isVisible()
    window.settings_action.trigger()
    assert coordinator._settings_window is settings_window
    assert len(service._subscriptions) == 1

    target.write_text(
        "# retained comment\nschema_version = 1\n[lyrics.display]\ntranslated = true\n",
        encoding="utf-8",
    )
    coordinator._settings_reload_observed(service.reload())
    translated = settings_window.rows["lyrics.display.translated"].editor
    assert isinstance(translated, QCheckBox)
    assert translated.isChecked()

    target.write_text(
        "# retained comment\nschema_version = 1\n"
        '[lyrics.display]\ntranslated = "invalid"\n',
        encoding="utf-8",
    )
    coordinator._settings_reload_observed(service.reload())
    assert translated.isChecked()
    assert "rejected" in settings_window.error_banner.text()

    target.write_text(
        "# retained comment\nschema_version = 1\n[lyrics.display]\ntranslated = true\n",
        encoding="utf-8",
    )
    coordinator._settings_reload_observed(service.reload())
    selectable = settings_window.rows["desktop.lyrics.selectable"].editor
    assert isinstance(selectable, QCheckBox)
    selectable.click()

    content = target.read_text(encoding="utf-8")
    assert path.is_symlink()
    assert "# retained comment" in content
    assert "translated = true" in content
    assert "selectable = true" in content
    assert (
        cli.main(
            ["config", "get", "desktop.lyrics.selectable"],
            database_path=tmp_path / "state.sqlite3",
            config_path=path,
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "desktop.lyrics.selectable = true" in output
    assert "origin: config-file" in output

    settings_window.rows["desktop.lyrics.selectable"].reset_button.click()
    assert service.get("desktop.lyrics.selectable") is False
    assert "selectable" not in target.read_text(encoding="utf-8")
    assert not settings_window.rows[
        "desktop.lyrics.selectable"
    ].reset_button.isEnabled()

    coordinator._change_setting("library.metadata_workers", 99)
    assert service.get("library.metadata_workers") == 4
    assert "previous value remains active" in settings_window.error_banner.text()

    coordinator.close()
    assert len(service._subscriptions) == 0
    assert QApplication.instance() is qt_app
    window.close()


def test_large_library_job_keeps_qt_event_loop_responsive(
    qt_app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = Event()
    release = Event()

    def large_scan(self, **kwargs):  # type: ignore[no-untyped-def]
        del self, kwargs
        started.set()
        release.wait(2)
        return LibraryScanSummary(1, 10_000, 10_000, 0, 0, 0, 0, 0, 0, 0)

    monkeypatch.setattr(
        "konokashi.application.library_scan.LibraryScanService.scan", large_scan
    )
    window = MainWindow()
    coordinator = DesktopCoordinator(
        qt_app,
        window,
        database_path=tmp_path / "desktop-library.sqlite3",
        runtime=_Runtime(),  # type: ignore[arg-type]
    )
    event_loop_progress: list[str] = []
    QTimer.singleShot(0, lambda: event_loop_progress.append("responsive"))

    coordinator._start_library_scan()
    try:
        for _ in range(200):
            if event_loop_progress:
                break
            QTest.qWait(5)

        assert event_loop_progress == ["responsive"]
        assert window.scan_action.text() == "Cancel &scan"
        assert started.wait(10), "bounded worker did not start within 10 seconds"
        release.set()
        for _ in range(400):
            if not window._library_scan_running:
                break
            QTest.qWait(5)
        assert window.scan_action.text() == "Scan &library"
    finally:
        release.set()
        assert coordinator._pool.waitForDone(10_000)
        coordinator.close()
        window.close()


def test_non_cooperative_worker_cannot_hold_desktop_process_shutdown() -> None:
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}

    completed = subprocess.run(
        [sys.executable, "-m", "tests.controlled_desktop_quit"],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=3,
    )

    assert completed.returncode == 0, completed.stderr


class _ReviewWindow(MainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.reviews = []

    def show_review(self, snapshot):  # type: ignore[no-untyped-def]
        self.reviews.append(snapshot)


def test_review_load_and_track_correction_dispatch_through_application_boundary(
    qt_app: QApplication,
) -> None:
    window = _ReviewWindow()
    coordinator = _ImmediateCoordinator(qt_app, window)
    track = _track("xa4WrgqI7q0", "Track A")
    frontend = _Frontend(track)
    coordinator._frontend = frontend
    coordinator._begin_track(track)

    coordinator._load_review()

    assert len(frontend.review_calls) == 1
    assert window.reviews == [_review_snapshot()]

    coordinator._apply_correction(
        CorrectionActionRequest(CorrectionActionKind.ENRICH_YOUTUBE)
    )
    assert len(frontend.review_calls) == 2
    assert frontend.review_options[-1] == {
        "refresh": False,
        "title": None,
        "artists": (),
        "enrich_youtube": True,
    }

    coordinator._apply_correction(
        CorrectionActionRequest(
            CorrectionActionKind.PUT_TRACK_OVERRIDE,
            title="Corrected",
            artists=("Artist",),
            album="Album",
        )
    )

    assert frontend.correction_calls[0][0] == "track"
    _track_value, title, artists, album = frontend.correction_calls[0][1]
    assert title == "Corrected"
    assert artists == ("Artist",)
    assert album == "Album"
    assert artists == ("Artist",)
    assert frontend.load_calls == [track, track]
    coordinator.close()
    window.close()


def test_representation_corrections_dispatch_exact_document_values(
    qt_app: QApplication,
) -> None:
    window = _ReviewWindow()
    coordinator = _ImmediateCoordinator(qt_app, window)
    track = _track("xa4WrgqI7q0", "Track A")
    frontend = _Frontend(track)
    coordinator._frontend = frontend
    coordinator._begin_track(track)

    requests = (
        CorrectionActionRequest(CorrectionActionKind.SET_LANGUAGE_ZH),
        CorrectionActionRequest(CorrectionActionKind.SET_LANGUAGE_JA),
        CorrectionActionRequest(CorrectionActionKind.RESET_LANGUAGE),
        CorrectionActionRequest(
            CorrectionActionKind.PUT_TRANSLATION,
            source_line_id="line-0001",
            text="Translated line",
        ),
        CorrectionActionRequest(
            CorrectionActionKind.RESET_TRANSLATION,
            source_line_id="line-0001",
        ),
    )
    for request in requests:
        coordinator._apply_correction(request)

    kinds = [kind for kind, _value in frontend.correction_calls]
    assert kinds == [
        "language",
        "language",
        "reset-language",
        "translation",
        "reset-translation",
    ]
    assert frontend.correction_calls[0][1][1] == "zh"
    assert frontend.correction_calls[1][1][1] == "ja"
    assert frontend.correction_calls[3][1][1:] == (
        "line-0001",
        "Translated line",
    )
    assert frontend.correction_calls[4][1][1] == "line-0001"
    coordinator.close()
    window.close()


def test_line_corrections_dispatch_through_shared_frontend_and_reload(
    qt_app: QApplication,
) -> None:
    window = _ReviewWindow()
    coordinator = _ImmediateCoordinator(qt_app, window)
    track = _track("xa4WrgqI7q0", "Track A")
    frontend = _Frontend(track)
    coordinator._frontend = frontend
    coordinator._begin_track(track)
    edit = LyricLineEdit("line-1", "Corrected", 1_250)

    for request in (
        CorrectionActionRequest(
            CorrectionActionKind.APPLY_LYRIC_EDITS, line_edits=(edit,)
        ),
        CorrectionActionRequest(CorrectionActionKind.RESET_LYRIC_EDITS),
        CorrectionActionRequest(
            CorrectionActionKind.IMPORT_LYRIC_TEXT,
            import_text="[00:01.000]Imported\n",
        ),
    ):
        coordinator._apply_correction(request)

    assert [kind for kind, _value in frontend.correction_calls] == [
        "lyric-edits",
        "reset-lyric-edits",
        "import-lyric-text",
    ]
    assert frontend.correction_calls[0][1][1] == (edit,)
    assert frontend.correction_calls[2][1][1] == "[00:01.000]Imported\n"
    assert frontend.load_calls == [track, track, track, track]
    coordinator.close()
    window.close()


def _install_timed_source(
    coordinator: _ImmediateCoordinator,
    window: MainWindow,
    track: ResolvedTrack,
) -> PlaybackSyncSession:
    token = coordinator.controller.begin_resolution(track)
    assert coordinator.controller.accept_snapshot(_snapshot(track, token.generation))
    window.render_state(coordinator.controller.state)
    session = PlaybackSyncSession(
        PlaybackClock(lambda: 0),
        track.raw_snapshot,
        f"desktop:{track.source_identity!r}",
    )
    coordinator._track = track
    coordinator._playback_session = session
    return session


def test_position_requests_are_single_flight_and_seek_coalesces_a_fresh_sample(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    runtime = _Runtime()
    timing = _DeferredTiming()
    runtime.timing = timing
    coordinator = DesktopCoordinator(qt_app, window, runtime=runtime)  # type: ignore[arg-type]
    track = _track("xa4WrgqI7q0", "Track A")
    token = coordinator.controller.begin_resolution(track)
    assert token.generation > 0
    clock = PlaybackClock(runtime.clock.monotonic_ns)
    session = PlaybackSyncSession(
        clock,
        track.raw_snapshot,
        f"desktop:{track.source_identity!r}",
    )
    coordinator._track = track
    coordinator._playback_clock = clock
    coordinator._playback_session = session
    coordinator._scheduler = AdaptiveResampler()

    coordinator._sync_tick()
    coordinator._sync_tick()
    coordinator._sync_tick()
    assert len(timing.calls) == 1

    coordinator._on_player_event(
        PlayerEvent(
            PlayerEventKind.SEEKED,
            track.raw_snapshot.service_name,
            position_us=4_000_000,
        )
    )
    assert len(timing.calls) == 1
    timing.complete(0, 1_000_000)
    qt_app.processEvents()

    assert len(timing.calls) == 2
    assert timing.calls[1][2].value == "seek"
    estimate = session.estimate(duration_us=None)
    assert estimate is not None
    assert estimate.position_us == 4_000_000

    coordinator.close()
    # Even a misbehaving adapter callback cannot mutate closed coordinator state.
    timing.complete(1, 4_100_000)
    assert coordinator._position_request is None
    window.close()


def test_unrelated_player_event_preserves_lyrics_when_selection_stays_same(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    coordinator = _ImmediateCoordinator(qt_app, window)
    track = _track("xa4WrgqI7q0", "Track A")
    session = _install_timed_source(coordinator, window, track)
    frontend = _Frontend(track)
    coordinator._frontend = frontend

    coordinator._on_player_event(
        PlayerEvent(
            PlayerEventKind.PLAYBACK_STATUS_CHANGED,
            "org.mpris.MediaPlayer2.other",
            playback_status="Playing",
        )
    )

    assert coordinator.controller.state.active
    assert coordinator._playback_session is session
    assert session.requires_reload is True
    coordinator._refresh_selection()
    assert coordinator.controller.state.active
    assert coordinator._playback_session is session
    assert session.requires_reload is False
    assert frontend.load_calls == []
    coordinator.close()
    window.close()


def test_selection_change_loads_new_source_without_old_lyric_flash(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    coordinator = _ImmediateCoordinator(qt_app, window)
    track_a = _track("xa4WrgqI7q0", "Track A")
    track_b = _track("kFqGyp60d8s", "Track B")
    _install_timed_source(coordinator, window, track_a)
    frontend = _Frontend(track_b)
    coordinator._frontend = frontend

    coordinator._on_player_event(
        PlayerEvent(
            PlayerEventKind.PLAYBACK_STATUS_CHANGED,
            track_b.raw_snapshot.service_name + ".other",
            playback_status="Playing",
        )
    )
    assert coordinator.controller.state.title == "Track A"
    assert coordinator.controller.state.active

    coordinator._refresh_selection()

    assert coordinator.controller.state.title == "Track B"
    assert coordinator.controller.state.active == ()
    assert coordinator.controller.state.state.value == "no-result"
    assert frontend.load_calls == [track_b]
    coordinator.close()
    window.close()


def _generic_recording(
    title: str,
    *,
    artists: tuple[str, ...] = ("Artist",),
) -> ResolvedTrack:
    track = _track("xa4WrgqI7q0", title)
    return replace(
        track,
        source_identity=GenericMprisIdentity(
            track.raw_snapshot.service_name,
            "/reused/track/1",
            "https://radio.example/current",
        ),
        candidate=replace(track.candidate, title=title, artists=artists),
        raw_snapshot=replace(
            track.raw_snapshot,
            metadata=replace(
                track.raw_snapshot.metadata,
                title=title,
                artists=artists,
                url="https://radio.example/current",
                track_id="/reused/track/1",
            ),
        ),
    )


def test_reused_generic_track_id_with_new_metadata_starts_new_load(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    coordinator = _ImmediateCoordinator(qt_app, window)
    track_a = _generic_recording("Track A")
    track_b = _generic_recording("Track B", artists=("Different Artist",))
    assert track_a.source_identity == track_b.source_identity
    _install_timed_source(coordinator, window, track_a)
    frontend = _Frontend(track_b)
    coordinator._frontend = frontend

    coordinator._refresh_selection()

    assert coordinator.controller.state.title == "Track B"
    assert coordinator.controller.state.active == ()
    assert frontend.load_calls == [track_b]
    coordinator.close()
    window.close()


def test_same_generic_recording_metadata_enrichment_refreshes_without_reload(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    coordinator = _ImmediateCoordinator(qt_app, window)
    track = _generic_recording("Track A")
    session = _install_timed_source(coordinator, window, track)
    updated = replace(
        track,
        candidate=replace(
            track.candidate,
            album="New album evidence",
            duration_us=181_000_000,
        ),
        raw_snapshot=replace(
            track.raw_snapshot,
            playback_status="Paused",
            metadata=replace(
                track.raw_snapshot.metadata,
                album="New album evidence",
                duration_us=181_000_000,
                art_url="https://images.example/cover.jpg",
            ),
        ),
    )
    frontend = _Frontend(updated)
    coordinator._frontend = frontend

    coordinator._refresh_selection()

    assert coordinator._track is updated
    assert coordinator._playback_session is session
    assert session.snapshot is updated.raw_snapshot
    assert frontend.load_calls == []
    coordinator.close()
    window.close()


def test_temporary_player_override_reaches_shared_selection_and_diagnostics(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    coordinator = _ImmediateCoordinator(qt_app, window, player_override="strawberry")
    track = _track("xa4WrgqI7q0", "Track A")
    frontend = _Frontend(track)
    coordinator._frontend = frontend

    coordinator._refresh_selection()

    assert frontend.player_override == "strawberry"
    assert "temporary player override active: strawberry" in (
        coordinator.controller.state.diagnostics
    )
    coordinator.close()
    window.close()


@pytest.mark.parametrize(
    "kind",
    (PlayerEventKind.METADATA_CHANGED, PlayerEventKind.PLAYER_DISAPPEARED),
)
def test_selected_source_invalidation_clears_immediately_and_recovers_to_waiting(
    qt_app: QApplication,
    kind: PlayerEventKind,
) -> None:
    window = MainWindow()
    coordinator = _ImmediateCoordinator(qt_app, window)
    track = _track("xa4WrgqI7q0", "Track A")
    _install_timed_source(coordinator, window, track)
    frontend = _Frontend(None)
    coordinator._frontend = frontend

    coordinator._on_player_event(
        PlayerEvent(
            kind,
            track.raw_snapshot.service_name,
        )
    )
    assert coordinator.controller.state.state.value == "resolving"
    assert coordinator.controller.state.active == ()
    assert coordinator._playback_session is None

    coordinator._refresh_selection()
    assert coordinator.controller.state.state.value == "waiting"
    assert coordinator.controller.state.title is None
    coordinator.close()
    window.close()


def test_no_player_can_transition_to_current_track_without_restart(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    coordinator = _ImmediateCoordinator(qt_app, window)
    track = _track("xa4WrgqI7q0", "Appeared")
    frontend = _Frontend(track)
    coordinator._frontend = frontend

    assert coordinator.controller.state.state.value == "waiting"
    coordinator._on_player_event(
        PlayerEvent(
            PlayerEventKind.PLAYER_APPEARED,
            track.raw_snapshot.service_name,
        )
    )
    assert coordinator._selection_timer.isActive()
    coordinator._refresh_selection()

    assert coordinator.controller.state.title == "Appeared"
    assert coordinator.controller.state.state.value == "no-result"
    assert frontend.load_calls == [track]
    coordinator.close()
    window.close()


def test_player_event_rejects_selection_result_captured_before_the_event(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    coordinator = _DeferredCoordinator(qt_app, window)
    track_a = _track("xa4WrgqI7q0", "Stale A")
    track_b = _track("kFqGyp60d8s", "Current B")
    frontend = _Frontend(track_a)
    coordinator._frontend = frontend

    coordinator._refresh_selection()
    assert len(coordinator.pending) == 1
    frontend.selected = track_b
    coordinator._on_player_event(
        PlayerEvent(
            PlayerEventKind.PLAYER_APPEARED,
            track_b.raw_snapshot.service_name,
        )
    )
    coordinator.complete_next()

    assert coordinator.controller.state.state.value == "waiting"
    assert frontend.load_calls == []

    coordinator._refresh_selection()
    coordinator.complete_next()
    coordinator.complete_next()
    assert coordinator.controller.state.title == "Current B"
    assert coordinator.controller.state.state.value == "no-result"
    assert frontend.load_calls == [track_b]
    coordinator.close()
    window.close()


def test_rapid_track_loads_accept_only_latest_generation(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    coordinator = _ControlledCoordinator(qt_app, window)
    track_a = _track("xa4WrgqI7q0", "Track A")
    track_b = _track("kFqGyp60d8s", "Track B")
    track_c = _track("4fndeDfaWCg", "Track C")
    frontend = _Frontend(track_a)
    coordinator._frontend = frontend

    coordinator._begin_track(track_a)
    coordinator._begin_track(track_b)
    coordinator._begin_track(track_c)
    assert coordinator.controller.state.title == "Track C"
    assert len(coordinator.pending_work) == 3

    coordinator.complete(2)  # C finishes before the deliberately delayed A and B.
    assert coordinator.controller.state.title == "Track C"
    assert coordinator.controller.state.state.value == "no-result"
    assert coordinator._bundle is not None
    assert coordinator._bundle.track is track_c
    coordinator.complete(1)  # B's late result is discarded.
    assert coordinator.controller.state.title == "Track C"
    assert coordinator.controller.state.state.value == "no-result"
    coordinator.complete(0)  # A finishes last and is also discarded.

    assert coordinator.controller.state.title == "Track C"
    assert coordinator.controller.state.state.value == "no-result"
    assert coordinator._bundle is not None
    assert coordinator._bundle.track is track_c
    assert frontend.cancellations == 3
    assert frontend.correction_calls == []
    coordinator.close()
    window.close()


def test_local_artwork_loads_through_the_background_boundary(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    loader = _ArtworkLoader()
    coordinator = _ImmediateCoordinator(qt_app, window, artwork_loader=loader)
    track = _with_artwork(_track("xa4WrgqI7q0", "Artwork Track"), "file:///a.png")
    coordinator._frontend = _Frontend(track)

    coordinator._begin_track(track)
    qt_app.processEvents()

    assert loader.calls == ["file:///a.png"]
    assert window.artwork.has_artwork
    assert "Artwork Track" in window.artwork.accessibleDescription()
    assert window._background_wash_color is not None
    coordinator.close()
    window.close()


def test_late_artwork_cannot_overwrite_a_new_track(qt_app: QApplication) -> None:
    window = MainWindow()
    coordinator = _ControlledCoordinator(qt_app, window)
    loader = _ArtworkLoader()
    coordinator._artwork_loader = loader
    track_a = _with_artwork(_track("xa4WrgqI7q0", "Track A"), "file:///a.png")
    track_b = _with_artwork(_track("kFqGyp60d8s", "Track B"), "file:///b.png")
    coordinator._frontend = _Frontend(track_a)

    coordinator._begin_track(track_a)
    coordinator._begin_track(track_b)
    assert len(coordinator.pending_work) == 4
    coordinator.complete(0)  # stale artwork A
    assert not window.artwork.has_artwork
    coordinator.complete(1)  # current artwork B after stale artwork was removed
    assert window.artwork.has_artwork
    assert window._artwork_asset is not None
    assert window._artwork_asset.palette_color == "#0066CC"

    coordinator.close()
    window.close()


def test_temporary_timing_offset_composes_once_and_survives_session_events(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    coordinator = _CalibrationCoordinator(
        qt_app,
        window,
        runtime=_Runtime(),  # type: ignore[arg-type]
        lyrics_offset_us=350_000,
    )
    track_a = _track("xa4WrgqI7q0", "Track A")
    frontend = _Frontend(track_a)
    bundle_a = replace(
        frontend.load_track(track_a),
        document_timing=LyricDocumentTiming("document-a", 125_000),
    )

    coordinator._start_playback_session(bundle_a)
    assert coordinator._calibration.lyrics.shift_us == 475_000
    assert "temporary invocation" in coordinator._calibration.lyrics.source
    coordinator._on_player_event(
        PlayerEvent(
            PlayerEventKind.SEEKED,
            track_a.raw_snapshot.service_name,
            position_us=5_000_000,
        )
    )
    coordinator._on_player_event(
        PlayerEvent(
            PlayerEventKind.PLAYBACK_STATUS_CHANGED,
            track_a.raw_snapshot.service_name,
            playback_status="Paused",
        )
    )
    assert coordinator._calibration.lyrics.shift_us == 475_000

    track_b = _track("kFqGyp60d8s", "Track B")
    bundle_b = replace(
        frontend.load_track(track_b),
        document_timing=LyricDocumentTiming("document-b", -50_000),
    )
    coordinator._start_playback_session(bundle_b)
    assert coordinator._calibration.lyrics.shift_us == 300_000

    restarted = _CalibrationCoordinator(
        qt_app,
        window,
        runtime=_Runtime(),  # type: ignore[arg-type]
    )
    restarted._start_playback_session(bundle_a)
    assert restarted._calibration.lyrics.shift_us == 125_000
    restarted.close()
    coordinator.close()
    window.close()


def test_rich_timing_uses_a_smooth_local_projection_cadence(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    coordinator = _CalibrationCoordinator(
        qt_app,
        window,
        runtime=_Runtime(),  # type: ignore[arg-type]
    )
    track = _track("xa4WrgqI7q0", "Track")
    frontend = _Frontend(track)
    line_bundle = frontend.load_track(track)
    document = lyric_document()
    line_bundle = replace(
        line_bundle,
        resolution=LyricsResolutionResult(
            track.source_identity,
            LyricsResolutionStatus.FOUND_TIMED,
            document=document,
        ),
    )
    rich_bundle = replace(
        line_bundle,
        resolution=replace(
            line_bundle.resolution,
            document=replace(document, timing_level=LyricTimingLevel.WORD),
        ),
    )

    coordinator._start_playback_session(rich_bundle)
    assert coordinator._sync_timer.interval() == 33
    coordinator._start_playback_session(line_bundle)
    assert coordinator._sync_timer.interval() == 100
    coordinator.close()
    window.close()


def test_menu_visibility_and_preset_use_canonical_service_without_opening_settings(
    qt_app: QApplication, tmp_path: Path
) -> None:
    from konokashi.application.appearance import AppearancePreset

    window = MainWindow()
    coordinator = _ImmediateCoordinator(qt_app, window)
    path = tmp_path / "config.toml"
    service = CanonicalSettingsService(TomlSettingsFile(path))
    service.initialize()
    coordinator._settings_service = service
    coordinator._settings_subscription = service.subscribe(
        lambda change: coordinator.settings_change_observed.emit(change)
    )
    window.application_menu.visibility_actions["progress"].trigger()
    assert service.get("appearance.visibility.progress") is False
    assert not window.appearance_profile.visibility.progress
    assert coordinator._settings_window is not None
    assert not coordinator._settings_window.isVisible()
    window.application_menu.preset_actions[AppearancePreset.COMPACT].trigger()
    assert service.get("appearance.preset") == "compact"
    reopened = CanonicalSettingsService(TomlSettingsFile(path))
    reopened.initialize()
    assert reopened.current.appearance == window.appearance_profile
    service.set("appearance.visibility.progress", True)
    assert window.application_menu.visibility_actions["progress"].isChecked()
    coordinator.close()
    window.close()


def test_reset_appearance_is_atomic_and_preserves_unrelated_settings(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = MainWindow()
    coordinator = _ImmediateCoordinator(qt_app, window)
    path = tmp_path / "config.toml"
    service = CanonicalSettingsService(TomlSettingsFile(path))
    service.initialize()
    service.set("appearance.colors.background", "#12345678")
    service.set("appearance.typography.lyric_scale_percent", 150)
    service.set("lyrics.display.translated", True)
    service.set("players.preferred", ("strawberry",))
    service.set("library.roots", (str(tmp_path),))
    coordinator._settings_service = service
    coordinator._settings_subscription = service.subscribe(
        lambda change: coordinator.settings_change_observed.emit(change)
    )
    coordinator._ensure_settings_window()

    coordinator._reset_appearance()

    for definition in SETTINGS_SCHEMA:
        if definition.key.startswith("appearance."):
            assert service.get(definition.key) == definition.default
    assert service.get("lyrics.display.translated") is True
    assert service.get("players.preferred") == ("strawberry",)
    assert service.get("library.roots") == (str(tmp_path),)
    restarted = CanonicalSettingsService(TomlSettingsFile(path))
    restarted.initialize()
    for definition in SETTINGS_SCHEMA:
        if definition.key.startswith("appearance."):
            assert restarted.get(definition.key) == definition.default
    assert restarted.get("lyrics.display.translated") is True
    coordinator.close()
    window.close()


def test_every_gui_collection_persists_reloads_and_projects_external_updates(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = MainWindow()
    coordinator = _ImmediateCoordinator(qt_app, window)
    path = tmp_path / "config.toml"
    service = CanonicalSettingsService(TomlSettingsFile(path))
    service.initialize()
    coordinator._settings_service = service
    coordinator._settings_subscription = service.subscribe(
        lambda change: coordinator.settings_change_observed.emit(change)
    )
    settings = coordinator._ensure_settings_window()
    values = {
        "players.preferred": "strawberry",
        "players.ignored": "firefox",
        "library.roots": str(tmp_path),
    }

    for key, value in values.items():
        editor = settings.rows[key].editor
        assert isinstance(editor, OrderedStringListEditor)
        editor.input.setText(value)
        editor.add_button.click()
        assert service.get(key) == (value,)

    preferred = settings.rows["players.preferred"].editor
    assert isinstance(preferred, OrderedStringListEditor)
    preferred.input.setText("vlc")
    preferred.add_button.click()
    preferred.up_button.click()
    assert service.get("players.preferred") == ("vlc", "strawberry")

    restarted = CanonicalSettingsService(TomlSettingsFile(path))
    restarted.initialize()
    assert restarted.get("players.preferred") == ("vlc", "strawberry")
    assert restarted.get("players.ignored") == ("firefox",)
    assert restarted.get("library.roots") == (str(tmp_path),)

    service.set("players.ignored", ("plasma-browser-integration",))
    ignored = settings.rows["players.ignored"].editor
    assert isinstance(ignored, OrderedStringListEditor)
    assert ignored.value() == ("plasma-browser-integration",)
    coordinator.close()
    window.close()
