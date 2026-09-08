"""Background alpha must not fade lyrics, dialogs, or ordinary window controls."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from konokashi.application.settings import validate_settings_values
from konokashi.application.settings_service import CanonicalSettingsService
from konokashi.infrastructure.configuration.toml_file import TomlSettingsFile
from konokashi.presentation.desktop.main_window import MainWindow
from konokashi.presentation.desktop.settings_window import SettingsWindow


@pytest.fixture(scope="module")
def app() -> QApplication:
    result = QApplication.instance() or QApplication([])
    assert isinstance(result, QApplication)
    return result


def test_background_alpha_paints_live_without_fading_text_or_settings(
    app: QApplication, tmp_path: Path
) -> None:
    window = MainWindow()
    window.show()
    app.processEvents()
    foreground = window.title_label.palette().color(QPalette.ColorRole.WindowText)
    handle = window.windowHandle()
    for opacity in (100, 50, 10, 0, 100):
        values = {"appearance.opacity.background": opacity}
        profile = validate_settings_values(
            values, explicit_keys=frozenset(values)
        ).appearance
        window.set_appearance_profile(profile)
        app.processEvents()
        assert window.windowHandle() is handle
        assert window.windowOpacity() == 1
        assert (
            window.title_label.palette().color(QPalette.ColorRole.WindowText)
            == foreground
        )
        pixel = window.grab().toImage().pixelColor(2, window.height() - 2)
        assert abs(pixel.alpha() - round(255 * opacity / 100)) <= 1
    dialog = SettingsWindow(tmp_path / "config.toml", window)
    dialog.show()
    app.processEvents()
    assert dialog.palette().color(QPalette.ColorRole.Window).alpha() == 255
    assert not dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert not window.windowFlags() & Qt.WindowType.FramelessWindowHint
    dialog.close()
    window.close()


def test_unsupported_platform_falls_back_to_opaque(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QGuiApplication, "platformName", lambda: "minimal")
    window = MainWindow()
    values = {"appearance.opacity.background": 10}
    window.set_appearance_profile(
        validate_settings_values(values, explicit_keys=frozenset(values)).appearance
    )
    window.show()
    app.processEvents()
    assert not window.background_transparency_available
    assert window.grab().toImage().pixelColor(2, window.height() - 2).alpha() == 255
    window.close()


def test_background_alpha_and_opacity_persist_as_one_combined_paint(
    app: QApplication, tmp_path: Path
) -> None:
    path = tmp_path / "config.toml"
    service = CanonicalSettingsService(TomlSettingsFile(path))
    service.initialize()
    service.set_many(
        {
            "appearance.colors.background": "#20304080",
            "appearance.opacity.background": 50,
        }
    )
    restarted = CanonicalSettingsService(TomlSettingsFile(path))
    restarted.initialize()
    window = MainWindow(appearance=restarted.current.appearance)
    window.show()
    app.processEvents()
    assert window.grab().toImage().pixelColor(2, window.height() - 2).alpha() == 64
    window.close()
