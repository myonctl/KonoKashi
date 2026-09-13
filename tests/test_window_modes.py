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
    assert window.mode_controls.isVisible()
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
    assert window.mode_controls.isHidden()
    assert window.overlay_controls.isVisible()
    assert window._metadata_widget.isHidden()
    assert window.playback_widget.isHidden()
    assert window.previous_band.isHidden()
    assert window.next_band.isHidden()
    assert window.active_band.isVisible()
    assert window.restore_window_action.isEnabled()
    floating_active_size = window.active_band.font().pointSizeF()

    window.set_window_mode(DesktopWindowMode.FULLSCREEN)
    QTest.qWait(100)
    assert window.isFullScreen()
    assert window.application_menu.isHidden()
    assert window.fullscreen_controls.isVisible()
    assert window.fullscreen_exit_button.isVisible()
    assert window._metadata_widget.isHidden()
    assert window.playback_label.isHidden()
    assert window.time_label.isHidden()
    assert window.active_band.isVisible()
    assert window.active_band.font().pointSizeF() > floating_active_size
    visible_bands = tuple(
        band
        for band in (window.previous_band, window.active_band, window.next_band)
        if not band.isHidden()
    )
    viewport_width = window._lyric_column.viewport().width()
    expected_document_height = (
        sum(band.heightForWidth(viewport_width) for band in visible_bands)
        + max(0, len(visible_bands) - 1) * window._lyric_layout.spacing()
    )
    assert window._lyric_column.document_height == expected_document_height
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
    assert safe.tray_show_action.text() == "Unlock floating lyrics"

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
    assert window.mode_controls.isVisible()
    assert set(window.mode_buttons) == set(DesktopWindowMode)
    assert all(button.isVisible() for button in window.mode_buttons.values())
    assert window.mode_buttons[DesktopWindowMode.NORMAL].isChecked()
    window.application_menu.mode_actions[DesktopWindowMode.COMPACT].trigger()
    qt_app.processEvents()
    assert window.window_mode is DesktopWindowMode.COMPACT
    assert window.application_menu.mode_actions[DesktopWindowMode.COMPACT].isChecked()
    assert window.mode_buttons[DesktopWindowMode.COMPACT].isChecked()
    assert window.mode_popup_button.isVisible()
    assert window.mode_popup_button.text() == "Mode: Compact"
    assert all(button.isHidden() for button in window.mode_buttons.values())
    assert (
        window.application_menu.mode_actions[DesktopWindowMode.OVERLAY].text()
        == "&Floating lyrics"
    )
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


def test_visible_mode_chooser_and_floating_controls_are_directly_operable(
    qt_app: QApplication,
) -> None:
    window = MainWindow(overlay_recovery_available=True)
    requested: list[tuple[str, object]] = []
    window.setting_requested.connect(lambda key, value: requested.append((key, value)))
    window.show()

    QTest.mouseClick(
        window.mode_buttons[DesktopWindowMode.OVERLAY], Qt.MouseButton.LeftButton
    )
    qt_app.processEvents()
    assert window.window_mode is DesktopWindowMode.OVERLAY
    assert window.windowTitle() == "KonoKashi — Floating lyrics"
    assert window.overlay_controls.isVisible()
    assert window.overlay_opacity_slider.value() == 100

    window.overlay_opacity_slider.setValue(55)
    assert window.overlay_opacity_label.text() == "Opacity 55%"
    assert requested[-1] == ("appearance.opacity.background", 55)

    QTest.mouseClick(window.overlay_exit_button, Qt.MouseButton.LeftButton)
    qt_app.processEvents()
    assert window.window_mode is DesktopWindowMode.NORMAL
    assert window.mode_controls.isVisible()

    QTest.mouseClick(
        window.mode_buttons[DesktopWindowMode.FULLSCREEN],
        Qt.MouseButton.LeftButton,
    )
    qt_app.processEvents()
    assert window.window_mode is DesktopWindowMode.FULLSCREEN
    QTest.mouseClick(window.fullscreen_exit_button, Qt.MouseButton.LeftButton)
    qt_app.processEvents()
    assert window.window_mode is DesktopWindowMode.NORMAL
    window.close()
