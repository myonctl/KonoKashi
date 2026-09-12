"""Subprocess checks for Qt logical geometry under representative scale factors."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("scale", ("1", "1.25", "1.5", "2"))
def test_desktop_uses_logical_geometry_at_qt_scale_factors(scale: str) -> None:
    script = """
from pathlib import Path
from dataclasses import replace
import os
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication
from konokashi.application.desktop_state import (
    DesktopLyricGroup,
    DesktopLyricsState,
    DesktopViewState,
)
from konokashi.presentation.desktop.main_window import MainWindow
from konokashi.application.settings import default_settings_snapshot
from konokashi.presentation.desktop.settings_window import SettingsWindow

app = QApplication(["dpi-test"])
window = MainWindow()
long_original = (
    "Я слышу твой очень длинный голос через ночные улицы и далёкие города, "
    "когда музыка снова возвращается к нам. " * 2
)
long_romanized = (
    "Ya slyshu tvoy ochen dlinnyy golos cherez nochnye ulitsy i dalekie "
    "goroda, kogda muzyka snova vozvrashchaetsya k nam. " * 2
)
long_translation = (
    "I hear your very long voice across the night streets and distant cities "
    "when the music returns to us again. " * 2
)
window.render_state(
    DesktopViewState(
        DesktopLyricsState.TIMED,
        "Synchronized lyrics",
        previous=(DesktopLyricGroup("previous", long_original),),
        active=(
            DesktopLyricGroup(
                "line",
                long_original,
                long_romanized,
                long_translation,
            ),
        ),
        next=(DesktopLyricGroup("next", long_original),),
    )
)
window.set_appearance_profile(
    replace(window.appearance_profile, lyric_scale_percent=200)
)
window.resize(520, 620)
window.show()
app.processEvents()
window._lyric_column.relayout(center_active=True)
app.processEvents()
assert window.width() == 520
assert window.height() == 620
assert window.active_band.isVisible()
assert window.active_band.width() > 0
assert window.active_band.height() > 0
assert app.primaryScreen() is not None
assert window.devicePixelRatioF() >= float(os.environ["QT_SCALE_FACTOR"])
labels = tuple(
    label
    for band in (window.previous_band, window.active_band, window.next_band)
    for group in band._group_widgets
    if not group.isHidden()
    for label in (group.original, group.romanized, group.translation)
    if not label.isHidden()
)
active_labels = tuple(
    label
    for group in window.active_band._group_widgets
    if not group.isHidden()
    for label in (group.original, group.romanized, group.translation)
    if not label.isHidden()
)
rectangles = tuple(
    QRect(
        label.mapTo(window._lyric_column._content, label.rect().topLeft()),
        label.size(),
    )
    for label in labels
)
assert all(label.height() >= label.heightForWidth(label.width()) for label in labels)
assert all(
    not left.intersects(right)
    for index, left in enumerate(rectangles)
    for right in rectangles[index + 1:]
)
viewport = window._lyric_column.viewport().rect()
assert all(
    viewport.contains(
        QRect(
            label.mapTo(window._lyric_column.viewport(), label.rect().topLeft()),
            label.size(),
        )
    )
    for label in active_labels
)
assert window.previous_band.visible_group_count == 0
assert window.next_band.visible_group_count == 0
assert window._lyric_column.adaptive_fit_scale < 1.0
assert window._lyric_column.verticalScrollBar().maximum() == 0
settings = SettingsWindow(Path("/tmp/konokashi-dpi-config.toml"), window)
settings.set_snapshot(default_settings_snapshot())
settings.set_diagnostics(())
settings.resize(880, 640)
settings.show()
app.processEvents()
assert settings.width() == 880
assert settings.height() == 640
assert settings.categories.isVisible()
assert settings.path_display.isVisible()
settings.close()
window.close()
"""
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QT_SCALE_FACTOR"] = scale
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
