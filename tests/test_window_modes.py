"""Desktop mode, overlay safety, and shared-renderer regressions."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from konokashi.presentation.desktop.main_window import MainWindow
from konokashi.presentation.desktop.window_surface import DesktopWindowMode
from konokashi.presentation.desktop.workspace import PanelId
from tests.test_desktop_widgets import _state


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    application = QApplication.instance() or QApplication(["window-mode-test"])
    assert isinstance(application, QApplication)
    return application


def test_four_modes_reuse_one_workspace_and_lyric_renderer(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    state = _state()
    window.render_state(state)
    window.show()
    qt_app.processEvents()
    workspace = window.workspace
    panels = tuple(workspace.panel(identity) for identity in PanelId)
    lyric_bands = (window.previous_band, window.active_band, window.next_band)

    for mode in DesktopWindowMode:
        window.set_window_mode(mode)
        qt_app.processEvents()
        assert window.window_mode is mode
        assert window.state is state
        assert window.workspace is workspace
        assert tuple(workspace.panel(identity) for identity in PanelId) == panels
        assert (
            window.previous_band,
            window.active_band,
            window.next_band,
        ) == lyric_bands
        assert "君の声が聞こえる" in window.active_band.text()

    window.set_window_mode(DesktopWindowMode.NORMAL)
    window.close()


def test_mode_projection_keeps_overlay_focused_and_fullscreen_escapable(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    window.render_state(_state())
    window.show()

    window.set_window_mode(DesktopWindowMode.COMPACT)
    qt_app.processEvents()
    assert window.application_menu.isVisible()
    assert window.title_label.isVisible()
    assert window.album_label.isHidden()
    assert window.source_label.isHidden()
    assert window._actions_widget.isHidden()
    assert window.previous_band.isHidden()
    assert window.next_band.isHidden()
    assert window.active_band.isVisible()

    window.set_window_mode(DesktopWindowMode.OVERLAY)
    qt_app.processEvents()
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert window.application_menu.isHidden()
    assert window.overlay_controls.isVisible()
    assert window._metadata_widget.isHidden()
    assert window.playback_widget.isHidden()
    assert window.active_band.isVisible()
    assert window.restore_window_action.isEnabled()

    window.set_window_mode(DesktopWindowMode.FULLSCREEN)
    qt_app.processEvents()
    assert window.isFullScreen()
    assert window.application_menu.isHidden()
    assert window.active_band.isVisible()
    QTest.keyClick(window, Qt.Key.Key_Escape)
    qt_app.processEvents()
    assert window.window_mode is DesktopWindowMode.NORMAL
    assert not window.isFullScreen()
    window.close()


def test_click_through_requires_and_retains_an_external_escape_route(
    qt_app: QApplication,
) -> None:
    unsafe = MainWindow(overlay_recovery_available=False)
    unsafe.show()
    unsafe.set_window_mode(DesktopWindowMode.OVERLAY)
    qt_app.processEvents()
    assert not unsafe.overlay_lock_button.isEnabled()
    assert unsafe.overlay_lock_button.text() == "Click-through unavailable"
    assert not unsafe.application_menu.overlay_lock_action.isEnabled()
    assert not unsafe.set_overlay_locked(True)
    assert not unsafe.windowFlags() & Qt.WindowType.WindowTransparentForInput
    unsafe.close()

    safe = MainWindow(overlay_recovery_available=True)
    safe.show()
    safe.set_window_mode(DesktopWindowMode.OVERLAY)
    assert safe.set_overlay_locked(True)
    qt_app.processEvents()
    assert safe.overlay_locked
    assert safe.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert safe.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert safe.overlay_controls.isHidden()
    assert safe.tray_show_action.text() == "Unlock lyrics overlay"

    safe._show_from_tray()
    qt_app.processEvents()
    assert not safe.overlay_locked
    assert not safe.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert safe.overlay_controls.isVisible()
    safe.set_overlay_recovery_available(False)
    assert not safe.set_overlay_locked(True)
    safe.set_window_mode(DesktopWindowMode.NORMAL)
    safe.close()


def test_click_through_fails_open_if_the_system_tray_disappears(
    qt_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    tray_available = True
    monkeypatch.setattr(
        QSystemTrayIcon,
        "isSystemTrayAvailable",
        lambda: tray_available,
    )
    window = MainWindow()
    window.show()
    window.set_window_mode(DesktopWindowMode.OVERLAY)
    assert window.set_overlay_locked(True)
    assert window.overlay_locked
    assert window._overlay_safety_timer.isActive()

    tray_available = False
    window._verify_overlay_recovery()
    qt_app.processEvents()
    assert not window.overlay_locked
    assert not window.overlay_recovery_available
    assert not window.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert not window._overlay_safety_timer.isActive()
    assert window.overlay_controls.isVisible()
    window.set_window_mode(DesktopWindowMode.NORMAL)
    window.close()


def test_mode_menu_and_screen_targeting_project_current_native_state(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    window.show()
    window.application_menu.mode_actions[DesktopWindowMode.COMPACT].trigger()
    qt_app.processEvents()
    assert window.window_mode is DesktopWindowMode.COMPACT
    assert window.application_menu.mode_actions[DesktopWindowMode.COMPACT].isChecked()
    assert (
        window.application_menu.mode_actions[DesktopWindowMode.FULLSCREEN]
        .shortcut()
        .toString()
        == "F11"
    )
    assert len(window.application_menu.screen_menu.actions()) == len(
        window.screen_names
    )
    assert (
        window.application_menu.screen_menu.menuAction() in window.tray_menu.actions()
    )
    assert window.application_menu.overlay_lock_action in window.tray_menu.actions()
    assert window.move_to_screen(window.current_screen_index)
    assert not window.move_to_screen(-1)
    assert not window.move_to_screen(len(window.screen_names))
    window.close()
