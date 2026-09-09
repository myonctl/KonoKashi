"""Deterministic player-event wiring regressions for the desktop coordinator."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox

from konokashi import cli
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
from konokashi.domain.library import LibraryScanSummary
from konokashi.domain.lyrics import LyricsResolutionResult, LyricsResolutionStatus
from konokashi.domain.models import (
    PlayerEvent,
    PlayerEventKind,
    PlayerInspection,
    PlayerListResult,
    PlayerWatchStart,
)
from konokashi.domain.representations import RepresentationDisplaySettings
from konokashi.domain.synchronization import PlaybackState, PositionObservation
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

    def select_track(self, players: PlayerListResult) -> PlayerSelectionResult:
        del players
        if self.selected is None:
            return PlayerSelectionResult(None)
        return PlayerSelectionResult(
            PlayerAssessment(self.selected, ("selected",), (1,))
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
        self, track: ResolvedTrack, *, title: str, artists: tuple[str, ...]
    ) -> None:
        self.correction_calls.append(("track", (track, title, artists)))

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


class _ImmediateCoordinator(DesktopCoordinator):
    """Run application jobs inline and omit clock sampling for wiring tests."""

    def __init__(self, application: QApplication, window: MainWindow) -> None:
        self.runtime = _Runtime()
        self.sync_ticks = 0
        self.started_bundles: list[FrontendLyricsBundle] = []
        super().__init__(application, window, runtime=self.runtime)  # type: ignore[arg-type]

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
    window.settings_button.click()

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
    window.settings_button.click()
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
        assert window.library_button.text() == "Cancel scan"
        assert started.wait(10), "bounded worker did not start within 10 seconds"
        release.set()
        for _ in range(400):
            if not window.library_button.property("scanRunning"):
                break
            QTest.qWait(5)
        assert window.library_button.text() == "Scan library"
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
        )
    )

    assert frontend.correction_calls[0][0] == "track"
    _track_value, title, artists = frontend.correction_calls[0][1]
    assert title == "Corrected"
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
