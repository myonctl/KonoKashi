"""Headless PySide6 widget, layout, Unicode, and escaping regressions."""

from __future__ import annotations

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QBoxLayout, QLabel, QPlainTextEdit, QWidget

from lyriflux.application.desktop_state import (
    DesktopLyricGroup,
    DesktopLyricsState,
    DesktopViewState,
)
from lyriflux.application.review_corrections import (
    ReviewCorrectionSnapshot,
    TrackAuditEvidence,
)
from lyriflux.application.settings import (
    DesktopInteractionSettings,
    default_settings_snapshot,
)
from lyriflux.domain.identity import YouTubeIdentity
from lyriflux.domain.lyrics import (
    LyricsAlternative,
    LyricsMatchConfidence,
    LyricsMatchDecision,
    LyricsProviderCandidate,
)
from lyriflux.domain.synchronization import ClockHealth, PlaybackState
from lyriflux.presentation.desktop.app import run_desktop
from lyriflux.presentation.desktop.main_window import (
    DiagnosticsDialog,
    MainWindow,
)
from lyriflux.presentation.desktop.review_dialog import (
    CorrectionActionKind,
    ReviewCorrectionDialog,
)
from lyriflux.presentation.desktop.settings_window import SettingsWindow


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    application = QApplication.instance() or QApplication(["lyriflux-test"])
    assert isinstance(application, QApplication)
    return application


def _state(
    *,
    original: str = "君の声が聞こえる",
    romanized: str | None = "Kimi no koe ga kikoeru",
) -> DesktopViewState:
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
                romanized,
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

    assert window.windowTitle() == "LyriFlux"
    assert window.active_band.isVisible()
    assert "君の声が聞こえる" in window.active_band.text()
    assert "Kimi no koe ga kikoeru" in window.active_band.text()
    assert "I can hear your voice" in window.active_band.text()
    group = window.active_band._group_widgets[0]
    original_size = group.original.font().pointSizeF()
    romanized_size = group.romanized.font().pointSizeF()
    translation_size = group.translation.font().pointSizeF()
    assert original_size > romanized_size > translation_size
    assert window.active_band.accessibleName() == "Current lyric"
    assert window.progress.value() == 420
    assert window.title_label.toolTip() == state.title
    window.close()


def test_waiting_state_is_calm_and_does_not_show_false_playback_progress(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    window.show()
    qt_app.processEvents()

    assert window.content_stack.currentWidget() is window._state_page
    assert window.status_label.text() == "Waiting for media…"
    assert not window.playback_widget.isVisible()
    assert not window.progress.isVisible()
    assert window.progress.maximum() == 1000
    window.close()


def test_untimed_lyrics_use_a_readable_bounded_content_page(
    qt_app: QApplication,
) -> None:
    state = replace(
        _state(),
        state=DesktopLyricsState.UNTIMED,
        status_message="Untimed lyrics",
        previous=(),
        active=(),
        next=(),
        static_lines=tuple(
            DesktopLyricGroup(f"line-{index}", f"Readable lyric line {index}")
            for index in range(30)
        ),
    )
    window = MainWindow()
    window.render_state(state)
    window.resize(1_440, 900)
    window.show()
    qt_app.processEvents()

    assert window.content_stack.currentWidget() is window._static_page
    assert window.static_lyrics.isVisible()
    assert window.static_lyrics.maximumWidth() == 920
    assert window.static_lyrics.height() > 300
    assert (
        window.static_lyrics.font().pointSizeF()
        > window.previous_band.font().pointSizeF()
    )
    window.close()


def test_normal_status_uses_human_readable_source_confidence_and_sync_copy(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    window.render_state(replace(_state(), lyrics_source="local-sidecar"))
    window.show()
    qt_app.processEvents()

    assert window.source_label.text() == (
        "Local lyrics · High-confidence match · In sync"
    )
    window.close()


def test_main_actions_have_hierarchy_tooltips_and_settings_shortcut(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    requests: list[str] = []
    window.settings_requested.connect(lambda: requests.append("settings"))
    window.show()
    window.activateWindow()
    qt_app.processEvents()

    assert not window.settings_button.isFlat()
    assert window.review_button.isFlat()
    assert window.details_button.isFlat()
    assert window.library_button.isFlat()
    assert "track and lyrics match" in window.review_button.toolTip()
    assert "synchronization" in window.details_button.toolTip()
    QTest.keyClick(window, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
    qt_app.processEvents()
    assert requests == ["settings"]
    window.close()


def test_chinese_original_and_pinyin_share_one_group_without_blank_row(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    state = _state(
        original="阳光彩虹小白马",
        romanized="Yáng guāng cǎi hóng xiǎo bái mǎ",
    )
    window.render_state(state)
    window.show()
    qt_app.processEvents()

    group = window.active_band._group_widgets[0]
    assert group.original.text() == "阳光彩虹小白马"
    assert group.romanized.text() == "Yáng guāng cǎi hóng xiǎo bái mǎ"
    assert group.original.isVisible()
    assert group.romanized.isVisible()

    window.render_state(_state(original="Latin only", romanized=None))
    qt_app.processEvents()
    assert not window.active_band._group_widgets[0].romanized.isVisible()
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

    assert run_desktop(["lyriflux"], coordinator_factory=factory) == 0
    assert qt_app.applicationName() == "LyriFlux"
    assert qt_app.organizationName() == "LyriFlux"
    assert qt_app.desktopFileName() == "io.github.myonctl.LyriFlux"
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

    assert window.active_band.text().splitlines()[0] == text
    assert window.active_band._group_widgets[0].original.text() == text
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


def test_compact_header_preserves_track_metadata_above_actions(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    window.render_state(_state())
    window.resize(420, 420)
    window.show()
    qt_app.processEvents()

    assert window._header_layout.direction() is QBoxLayout.Direction.TopToBottom
    assert window.title_label.isVisible()
    assert window.title_label.width() >= 200

    window.resize(760, 720)
    qt_app.processEvents()
    assert window._header_layout.direction() is QBoxLayout.Direction.LeftToRight
    window.close()


def test_lyric_typography_scales_with_logical_window_area(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    window.render_state(_state())
    window.resize(420, 420)
    window.show()
    qt_app.processEvents()
    QTest.qWait(100)
    compact_active_size = window.active_band.font().pointSizeF()
    compact_title_size = window.title_label.font().pointSizeF()

    window.resize(1440, 900)
    qt_app.processEvents()
    QTest.qWait(100)

    assert window.active_band.font().pointSizeF() > compact_active_size
    assert window.title_label.font().pointSizeF() > compact_title_size
    assert (
        window.previous_band.font().pointSizeF()
        < window.active_band.font().pointSizeF()
    )
    assert window.styleSheet() == ""
    window.close()


def test_ultrawide_width_does_not_run_away_with_typography_or_line_length(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    window.render_state(_state(original="A long active lyric line " * 12))
    window.resize(2_560, 720)
    window.show()
    qt_app.processEvents()
    QTest.qWait(100)

    assert window._responsive_scale(2_560, 720) == 1.0
    assert window._responsive_scale(3_440, 1_440) == 1.65
    assert window._lyric_column.maximumWidth() == 1_040
    assert window._lyric_column.width() <= 1_040
    assert window.active_band.font().pointSizeF() <= window._base_point_size * 1.75
    window.close()


def test_resize_burst_bounds_expensive_lyric_relayout(
    qt_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()
    window.render_state(_state())
    window.show()
    qt_app.processEvents()
    resize_calls = 0
    set_responsive_size = window.active_band.set_responsive_size

    def counted_resize(point_size: float, minimum_height: int) -> None:
        nonlocal resize_calls
        resize_calls += 1
        set_responsive_size(point_size, minimum_height)

    monkeypatch.setattr(window.active_band, "set_responsive_size", counted_resize)
    for width in range(760, 1_161, 2):
        window.resize(width, 720)
        qt_app.processEvents()
    QTest.qWait(100)

    assert resize_calls <= 1
    assert 15 <= window.active_band.font().pointSizeF() <= 18
    window.close()


def test_scaled_title_has_vertical_painting_room_and_selection_is_opt_in(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    window.render_state(_state())
    window.resize(1440, 900)
    window.show()
    qt_app.processEvents()

    title_metrics = QFontMetrics(window.title_label.font())
    assert window.title_label.height() >= title_metrics.lineSpacing() + 4
    assert (
        window.active_band.textInteractionFlags()
        is Qt.TextInteractionFlag.NoTextInteraction
    )
    assert window.active_band.cursor().shape() is Qt.CursorShape.ArrowCursor
    assert (
        window.static_lyrics.textInteractionFlags()
        is Qt.TextInteractionFlag.NoTextInteraction
    )

    window.set_interaction_settings(DesktopInteractionSettings(True))
    assert (
        window.active_band.textInteractionFlags()
        & Qt.TextInteractionFlag.TextSelectableByMouse
    )
    assert window.active_band.cursor().shape() is Qt.CursorShape.IBeamCursor
    assert (
        window.static_lyrics.textInteractionFlags()
        & Qt.TextInteractionFlag.TextSelectableByMouse
    )
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
    assert window.previous_band.foregroundRole() is QPalette.ColorRole.PlaceholderText
    assert window.source_label.foregroundRole() is QPalette.ColorRole.PlaceholderText
    window.close()


def test_open_window_follows_runtime_application_palette_change(
    qt_app: QApplication,
) -> None:
    original_palette = qt_app.palette()
    window = MainWindow()
    window.render_state(_state())
    window.show()
    qt_app.processEvents()

    changed_palette = QPalette(original_palette)
    changed_palette.setColor(QPalette.ColorRole.Window, QColor("#f4f1ea"))
    changed_palette.setColor(QPalette.ColorRole.WindowText, QColor("#241f1a"))
    changed_palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#6d655e"))
    qt_app.setPalette(changed_palette)
    qt_app.processEvents()

    assert window.palette().color(QPalette.ColorRole.Window) == QColor("#f4f1ea")
    assert window.title_label.palette().color(QPalette.ColorRole.WindowText) == QColor(
        "#241f1a"
    )
    assert window.previous_band.palette().color(
        QPalette.ColorRole.PlaceholderText
    ) == QColor("#6d655e")

    window.close()
    qt_app.setPalette(original_palette)


def test_keyboard_focus_order_and_escape_dialog_behavior(
    qt_app: QApplication,
    tmp_path,
) -> None:
    window = MainWindow()
    window.render_state(_state())
    window.show()
    window.settings_button.setFocus()
    qt_app.processEvents()

    assert qt_app.focusWidget() is window.settings_button
    QTest.keyClick(window.settings_button, Qt.Key.Key_Tab)
    assert qt_app.focusWidget() is window.review_button
    QTest.keyClick(window.review_button, Qt.Key.Key_Tab)
    assert qt_app.focusWidget() is window.details_button

    dialogs = (
        SettingsWindow(tmp_path / "config.toml", window),
        DiagnosticsDialog(window.state, window),
    )
    dialogs[0].set_snapshot(default_settings_snapshot())
    for dialog in dialogs:
        dialog.show()
        qt_app.processEvents()
        assert dialog.isVisible()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        qt_app.processEvents()
        assert not dialog.isVisible()
    window.close()


def test_review_is_enabled_only_after_source_resolution(qt_app: QApplication) -> None:
    window = MainWindow()
    timed = _state()

    window.render_state(replace(timed, state=DesktopLyricsState.RESOLVING))
    assert not window.review_button.isEnabled()
    window.render_state(timed)
    assert window.review_button.isEnabled()
    window.render_state(replace(timed, state=DesktopLyricsState.ERROR))
    assert not window.review_button.isEnabled()
    window.close()


def test_library_scan_button_emits_typed_start_and_cancel_intents(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    starts: list[str] = []
    cancels: list[str] = []
    window.library_scan_requested.connect(lambda: starts.append("start"))
    window.library_scan_cancel_requested.connect(lambda: cancels.append("cancel"))

    QTest.mouseClick(window.library_button, Qt.MouseButton.LeftButton)
    window.set_library_scan_state(True, "Scanning in background")
    QTest.mouseClick(window.library_button, Qt.MouseButton.LeftButton)

    assert starts == ["start"]
    assert cancels == ["cancel"]
    assert window.library_button.text() == "Cancel scan"
    assert window.library_button.accessibleDescription() == "Scanning in background"
    window.close()


def _review_snapshot() -> ReviewCorrectionSnapshot:
    candidate = LyricsProviderCandidate(
        "LRCLIB",
        "42",
        "Provider <title>",
        "Provider & artist",
        "Provider album",
        180_000,
        False,
        "short plain excerpt",
        "[00:01.00]short synced excerpt",
    )
    return ReviewCorrectionSnapshot(
        YouTubeIdentity("xa4WrgqI7q0"),
        True,
        TrackAuditEvidence(
            'Raw <title> & "video"',
            ("Raw uploader",),
            "Raw album",
            "https://example.invalid/?private=value",
            180_000_000,
            "Automatic title",
            ("Automatic artist",),
            "Automatic album",
            "High",
            "Effective title",
            ("Effective artist",),
            "Effective album",
            "Approved",
            ("removed suffix",),
            ("preserved raw metadata",),
            ("uncertain uploader",),
        ),
        True,
        "document-1",
        "LRCLIB",
        "Provider current title",
        "Provider current artist",
        "Provider current album",
        180_000,
        LyricsMatchDecision.CANDIDATE,
        LyricsMatchConfidence.HIGH,
        ("normalized title matches",),
        125_000,
        (
            LyricsAlternative(
                "document-42",
                candidate,
                LyricsMatchConfidence.MEDIUM,
                ("duration differs by 3000 ms",),
            ),
        ),
        ("provider diagnostic <literal>",),
    )


def test_review_dialog_exposes_bounded_audit_and_explicit_actions(
    qt_app: QApplication,
) -> None:
    dialog = ReviewCorrectionDialog(_review_snapshot())
    dialog.show()
    qt_app.processEvents()

    audit = dialog.findChild(QPlainTextEdit)
    assert audit is not None
    assert 'Raw <title> & "video"' in audit.toPlainText()
    assert "Automatic interpretation" in audit.toPlainText()
    assert "Effective interpretation" in audit.toPlainText()
    assert dialog.alternatives.count() == 1
    assert "Provider <title>" in dialog.alternatives.itemText(0)
    assert dialog.reset_delay_button.isEnabled()

    QTest.mouseClick(dialog.choose_button, Qt.MouseButton.LeftButton)
    action = dialog.action()
    assert action is not None
    assert action.kind is CorrectionActionKind.CHOOSE_ALTERNATIVE
    assert action.alternative is not None
    assert action.alternative.candidate.record_id == "42"


def test_review_dialog_track_and_delay_requests_are_typed(
    qt_app: QApplication,
) -> None:
    track_dialog = ReviewCorrectionDialog(_review_snapshot())
    track_dialog.title_edit.setText(" Corrected title ")
    track_dialog.artists_edit.setText("Artist A; Artist B")
    QTest.mouseClick(track_dialog.save_track_button, Qt.MouseButton.LeftButton)
    track_action = track_dialog.action()
    assert track_action is not None
    assert track_action.kind is CorrectionActionKind.PUT_TRACK_OVERRIDE
    assert track_action.title == "Corrected title"
    assert track_action.artists == ("Artist A", "Artist B")

    delay_dialog = ReviewCorrectionDialog(_review_snapshot())
    delay_dialog.delay_ms.setValue(-250)
    QTest.mouseClick(delay_dialog.save_delay_button, Qt.MouseButton.LeftButton)
    delay_action = delay_dialog.action()
    assert delay_action is not None
    assert delay_action.kind is CorrectionActionKind.SET_DELAY
    assert delay_action.delay_us == -250_000


def test_review_dialog_offers_only_match_reset_when_rejected_document_is_hidden(
    qt_app: QApplication,
) -> None:
    snapshot = replace(
        _review_snapshot(),
        current_document_id=None,
        current_match_decision=LyricsMatchDecision.REJECTED,
    )
    dialog = ReviewCorrectionDialog(snapshot)

    assert not dialog.approve_button.isEnabled()
    assert not dialog.reject_button.isEnabled()
    assert dialog.reset_match_button.isEnabled()
    assert not dialog.save_delay_button.isEnabled()
    dialog.close()

    session_only = ReviewCorrectionDialog(replace(_review_snapshot(), durable=False))
    assert not session_only.save_delay_button.isEnabled()
    assert not session_only.reset_delay_button.isEnabled()
    session_only.close()


def test_lyric_transitions_reuse_the_existing_widget_tree(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    window.render_state(_state())
    window.show()
    qt_app.processEvents()
    initial_widgets = {id(widget) for widget in window.findChildren(QWidget)}

    for index in range(100):
        state = replace(
            _state(original=f"line {index}"),
            position_us=index * 1_000_000,
            progress_fraction=index / 100,
        )
        window.render_state(state)
    qt_app.processEvents()

    assert {id(widget) for widget in window.findChildren(QWidget)} == initial_widgets
    window.close()
