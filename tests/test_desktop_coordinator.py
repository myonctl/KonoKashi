"""Deterministic player-event wiring regressions for the desktop coordinator."""

from __future__ import annotations

import os
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from lyricflow.application.frontend_session import FrontendLyricsBundle
from lyricflow.application.playback_clock import PlaybackClock
from lyricflow.application.sync_session import PlaybackSyncSession
from lyricflow.domain.lyrics import LyricsResolutionResult, LyricsResolutionStatus
from lyricflow.domain.models import (
    PlayerEvent,
    PlayerEventKind,
    PlayerInspection,
    PlayerListResult,
)
from lyricflow.domain.representations import RepresentationDisplaySettings
from lyricflow.domain.tracks import (
    PlayerAssessment,
    PlayerSelectionResult,
    ResolvedTrack,
)
from lyricflow.presentation.desktop.coordinator import DesktopCoordinator
from lyricflow.presentation.desktop.main_window import MainWindow
from tests.test_desktop_state import _snapshot, _track


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


class _Monitor:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


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


class _Frontend:
    def __init__(self, selected: ResolvedTrack | None) -> None:
        self.selected = selected
        self.load_calls: list[ResolvedTrack] = []
        self.cancellations = 0

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
        del settings

    def cancel_inflight(self) -> None:
        self.cancellations += 1


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
