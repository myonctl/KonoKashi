"""Subprocess checks for Qt logical geometry under representative scale factors."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("scale", ("1", "1.25", "1.5", "2"))
def test_desktop_uses_logical_geometry_at_qt_scale_factors(scale: str) -> None:
    script = """
from PySide6.QtWidgets import QApplication
from lyricflow.application.desktop_state import (
    DesktopLyricGroup,
    DesktopLyricsState,
    DesktopViewState,
)
from lyricflow.presentation.desktop.main_window import MainWindow

app = QApplication(["dpi-test"])
window = MainWindow()
window.render_state(
    DesktopViewState(
        DesktopLyricsState.TIMED,
        "Synchronized lyrics",
        active=(DesktopLyricGroup("line", "君の声が聞こえる", "Kimi no koe"),),
    )
)
window.resize(760, 720)
window.show()
app.processEvents()
assert window.width() == 760
assert window.height() == 720
assert window.active_band.isVisible()
assert window.active_band.width() > 0
assert window.active_band.height() > 0
assert app.primaryScreen() is not None
assert app.primaryScreen().devicePixelRatio() >= 1.0
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
