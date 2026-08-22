"""Headless PySide6 widget, layout, Unicode, and escaping regressions."""

from __future__ import annotations

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLabel

from lyricflow.application.desktop_state import (
    DesktopLyricGroup,
    DesktopLyricsState,
    DesktopViewState,
)
from lyricflow.domain.synchronization import ClockHealth, PlaybackState
from lyricflow.presentation.desktop.app import run_desktop
from lyricflow.presentation.desktop.main_window import MainWindow


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    application = QApplication.instance() or QApplication(["lyricflow-test"])
    assert isinstance(application, QApplication)
    return application


def _state(*, original: str = "君の声が聞こえる") -> DesktopViewState:
    return DesktopViewState(
        DesktopLyricsState.TIMED,
        "Synchronized lyrics",
        generation=1,
        title="A very long title " * 30,
        artists=("Artist",),
        player="Test Player",
        playback_state=PlaybackState.PLAYING,
        progress_fraction=0.42,
        position_us=42_000_000,
        duration_us=100_000_000,
        previous=(DesktopLyricGroup("previous", "previous lyric"),),
        active=(
            DesktopLyricGroup(
                "active",
                original,
                "Kimi no koe ga kikoeru",
                "I can hear your voice",
                ("generated", "user"),
            ),
        ),
        next=(DesktopLyricGroup("next", "next lyric"),),
        lyrics_source="LRCLIB",
        match_confidence="High",
        sync_health=ClockHealth.LOCKED,
    )


def test_main_window_launches_and_renders_plain_multilingual_text(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    state = _state()
    window.render_state(state)
    window.show()
    qt_app.processEvents()

    assert window.windowTitle() == "LyricFlow"
    assert window.active_band.isVisible()
    assert "君の声が聞こえる" in window.active_band.text()
    assert "Kimi no koe ga kikoeru" in window.active_band.text()
    assert "I can hear your voice" in window.active_band.text()
    assert window.active_band.accessibleName() == "Current lyric"
    assert window.progress.value() == 420
    assert window.title_label.toolTip() == state.title
    window.close()


def test_desktop_entry_point_launches_and_shuts_down_cleanly(
    qt_app: QApplication,
) -> None:
    captured: list[MainWindow] = []

    class Lifecycle:
        def start(self) -> None:
            QTimer.singleShot(0, qt_app.quit)

    def factory(application, window, database_path):  # type: ignore[no-untyped-def]
        assert application is qt_app
        assert database_path is None
        captured.append(window)
        return Lifecycle()

    assert run_desktop(["lyricflow"], coordinator_factory=factory) == 0
    assert len(captured) == 1
    assert captured[0].state.state is DesktopLyricsState.WAITING
    captured[0].close()


@pytest.mark.parametrize(
    "text",
    (
        "君の声が聞こえる",
        "너의 목소리가 들려",
        "我听见你的声音",
        "Я слышу твой голос",
        "Ακούω τη φωνή σου",  # noqa: RUF001 - intentional Greek fixture
        "أسمع صوتك",
        "君と dance tonight",
        '<b>not bold</b> & <img src="file:///etc/passwd">',
        '<a href="https://example.invalid">not a link</a>',
        "quotes ' \" and malformed <tag",
    ),
)
def test_untrusted_unicode_and_markup_render_as_plain_text(
    qt_app: QApplication,
    text: str,
) -> None:
    window = MainWindow()
    window.render_state(_state(original=text))
    window.show()
    qt_app.processEvents()

    assert text in window.active_band.text()
    assert window.active_band.textFormat() is Qt.TextFormat.PlainText
    assert not window.active_band.openExternalLinks()
    window.close()


def test_untrusted_metadata_is_inert_in_labels_and_tooltips(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    state = replace(_state(), title='<img src="file:///private"> & <b>title</b>')
    window.render_state(state)
    window.show()
    qt_app.processEvents()

    assert window.title_label.text() == '<img src="file:///private"> & <b>title</b>'
    assert window.title_label.toolTip() == (
        "&lt;img src=&quot;file:///private&quot;&gt; &amp; &lt;b&gt;title&lt;/b&gt;"
    )
    window.close()


@pytest.mark.parametrize(
    ("width", "height"),
    ((420, 420), (760, 720), (1440, 900), (2560, 720)),
)
def test_representative_logical_sizes_keep_current_lyric_valid_and_visible(
    qt_app: QApplication,
    width: int,
    height: int,
) -> None:
    window = MainWindow()
    window.render_state(_state(original="A deliberately very long lyric line " * 25))
    window.resize(width, height)
    window.show()
    qt_app.processEvents()

    geometry = window.active_band.geometry()
    assert window.active_band.isVisible()
    assert geometry.width() > 0
    assert geometry.height() > 0
    assert geometry.x() >= 0
    assert geometry.y() >= 0
    assert window.minimumWidth() <= width
    assert window.styleSheet() == ""
    window.close()


@pytest.mark.parametrize("dark", (False, True))
def test_system_palette_remains_the_theme_authority(
    qt_app: QApplication, dark: bool
) -> None:
    palette = QPalette()
    background = QColor("#202124") if dark else QColor("#f8f9fa")
    foreground = QColor("#f1f3f4") if dark else QColor("#202124")
    palette.setColor(QPalette.ColorRole.Window, background)
    palette.setColor(QPalette.ColorRole.WindowText, foreground)
    palette.setColor(QPalette.ColorRole.PlaceholderText, foreground.darker(150))
    qt_app.setPalette(palette)
    window = MainWindow()
    window.render_state(_state())
    window.show()
    qt_app.processEvents()

    labels = window.findChildren(QLabel)
    assert labels
    assert all(label.styleSheet() == "" for label in labels)
    assert window.active_band.palette().color(QPalette.ColorRole.WindowText).isValid()
    window.close()
