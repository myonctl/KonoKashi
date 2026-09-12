"""Headless PySide6 widget, layout, Unicode, and escaping regressions."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QWidget,
)

from konokashi.application.desktop_state import (
    DesktopKaraokeSegment,
    DesktopLyricGroup,
    DesktopLyricsState,
    DesktopRepresentationMetadata,
    DesktopViewState,
)
from konokashi.application.review_corrections import (
    ReviewCorrectionService,
    ReviewCorrectionSnapshot,
    TrackAuditEvidence,
    TranslationReviewLine,
)
from konokashi.application.settings import (
    DesktopInteractionSettings,
    default_settings_snapshot,
)
from konokashi.domain.identity import YouTubeIdentity
from konokashi.domain.library import (
    LibraryReviewItem,
    LibraryScanIssue,
    LibraryScanIssueCategory,
    LibraryScanSummary,
)
from konokashi.domain.lyric_corrections import (
    LyricEditorLine,
    LyricEditorSnapshot,
)
from konokashi.domain.lyrics import (
    ApprovalState,
    ContentProvenance,
    LyricsAlternative,
    LyricsAlternativeResult,
    LyricsMatchConfidence,
    LyricsMatchDecision,
    LyricsProviderCandidate,
    LyricsResolutionResult,
    LyricsResolutionStatus,
    RepresentationKind,
)
from konokashi.domain.representations import (
    LanguageRoutingStatus,
    RepresentationAvailability,
    RepresentationLayerStatus,
)
from konokashi.domain.synchronization import ClockHealth, PlaybackState
from konokashi.infrastructure.lyrics.provider_documents import (
    ProviderLyricDocumentBuilder,
)
from konokashi.infrastructure.storage.bootstrap import open_storage
from konokashi.presentation.desktop.app import run_desktop
from konokashi.presentation.desktop.library_review_dialog import LibraryReviewDialog
from konokashi.presentation.desktop.main_window import (
    DiagnosticsDialog,
    MainWindow,
)
from konokashi.presentation.desktop.review_dialog import (
    CorrectionActionKind,
    LyricEditorDialog,
    ReviewCorrectionDialog,
)
from konokashi.presentation.desktop.settings_window import SettingsWindow
from tests.stage2_helpers import resolver as track_resolver
from tests.stage2_helpers import snapshot as player_snapshot


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    application = QApplication.instance() or QApplication(["konokashi-test"])
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

    assert window.windowTitle() == "KonoKashi"
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


def test_line_only_active_and_context_colors_keep_their_visual_hierarchy(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    colors = replace(
        window.appearance_profile.colors,
        active_lyric="#FF0000",
        inactive_lyric="#0000FF",
    )
    window.set_appearance_profile(replace(window.appearance_profile, colors=colors))
    window.render_state(_state())
    window.show()
    qt_app.processEvents()

    active = (
        window.active_band._group_widgets[0]
        .original.palette()
        .color(QPalette.ColorRole.WindowText)
    )
    previous = (
        window.previous_band._group_widgets[0]
        .original.palette()
        .color(QPalette.ColorRole.WindowText)
    )
    assert active.red() > active.blue()
    assert previous.blue() > previous.red()
    window.close()


@pytest.mark.parametrize(
    ("kind", "language", "provenance", "expected"),
    (
        ("romanized", "ja-Latn", "generated", "Romaji · Generated"),
        ("romanized", "zh-Latn-pinyin", "provider", "Pinyin · Provider"),
        ("romanized", "ko-Latn", "user", "Korean reading · Your version"),
        ("transliterated", None, "imported", "Transliteration · Imported"),
    ),
)
def test_active_reading_caption_names_language_kind_and_provenance(
    qt_app: QApplication,
    kind: str,
    language: str | None,
    provenance: str,
    expected: str,
) -> None:
    window = MainWindow()
    active = replace(
        _state().active[0],
        reading_metadata=DesktopRepresentationMetadata(
            kind,
            provenance,
            source_name="<local engine>",
            source_version="1 & 2",
            language=language,
        ),
        translation_metadata=DesktopRepresentationMetadata(
            "translated", "provider", source_name="translation fixture"
        ),
    )
    window.render_state(replace(_state(), active=(active,)))
    window.show()
    qt_app.processEvents()

    group = window.active_band._group_widgets[0]
    assert group.reading_caption.text() == expected
    assert group.reading_caption.isVisible()
    assert group.reading_caption.toolTip() == (
        f"{expected}. &lt;local engine&gt; 1 &amp; 2"
    )
    assert group.romanized.accessibleName() == f"{expected} current lyric"
    assert group.translation_caption.text() == "Translation · Provider"
    assert (
        group.original.font().pointSizeF() > group.reading_caption.font().pointSizeF()
    )
    assert all(
        caption.isHidden()
        for context in (
            window.previous_band._group_widgets[0],
            window.next_band._group_widgets[0],
        )
        for caption in (context.reading_caption, context.translation_caption)
    )
    window.close()


def test_word_timing_remains_bound_to_original_not_shorter_translation(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    original = "one two three"
    active = DesktopLyricGroup(
        "active",
        original,
        "reading with a different count",
        "短い",
        karaoke_segments=(
            DesktopKaraokeSegment("one", 0, 4, 1.0),
            DesktopKaraokeSegment("two", 4, 8, 0.5),
            DesktopKaraokeSegment("three", 8, len(original), 0.0),
        ),
        reading_metadata=DesktopRepresentationMetadata(
            "romanized", "generated", language="ja-Latn"
        ),
        translation_metadata=DesktopRepresentationMetadata("translated", "user"),
    )
    window.render_state(replace(_state(), active=(active,)))
    window.show()
    qt_app.processEvents()

    group = window.active_band._group_widgets[0]
    assert group.original.karaoke_segments == active.karaoke_segments
    assert group.romanized.karaoke_segments == ()
    assert group.translation.karaoke_segments == ()
    assert group.translation_caption.text() == "Translation · Your version"
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


def test_paused_state_is_not_repeated_in_source_status(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    window.render_state(
        replace(
            _state(),
            playback_state=PlaybackState.PAUSED,
            sync_health=ClockHealth.PAUSED,
        )
    )
    window.show()
    qt_app.processEvents()

    assert window.playback_label.text() == "Paused"
    assert window.source_label.text() == "Lyrics from LRCLIB · High-confidence match"
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

    def factory(  # type: ignore[no-untyped-def]
        application,
        window,
        database_path,
        config_path,
        player_override,
        lyrics_offset_us,
    ):
        assert application is qt_app
        assert database_path is None
        assert config_path is None
        assert player_override is None
        assert lyrics_offset_us == 0
        captured.append(window)
        return Lifecycle()

    assert run_desktop(["konokashi"], coordinator_factory=factory) == 0
    assert qt_app.applicationName() == "KonoKashi"
    assert qt_app.organizationName() == "KonoKashi"
    assert qt_app.desktopFileName() == "io.github.myonctl.KonoKashi"
    assert not qt_app.windowIcon().isNull()
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


@pytest.mark.parametrize(
    "text",
    (
        "A long karaoke phrase that wraps naturally across the narrow window",
        "君の声が聞こえる夜の街を越えて歌が続いていく",
        "أسمع صوتك عبر شوارع الليل البعيدة",
    ),
)
def test_karaoke_overlay_preserves_plain_wrapped_multiscript_text(
    qt_app: QApplication, text: str
) -> None:
    window = MainWindow()
    colors = replace(
        window.appearance_profile.colors,
        active_lyric="#FF0000",
        inactive_lyric="#0000FF",
    )
    window.set_appearance_profile(replace(window.appearance_profile, colors=colors))
    split = max(1, len(text) // 2)
    active = DesktopLyricGroup(
        "active",
        text,
        karaoke_segments=(
            DesktopKaraokeSegment("first", 0, split, 0.5),
            DesktopKaraokeSegment("second", split, len(text), 0.0),
        ),
    )
    window.resize(420, 520)
    window.render_state(replace(_state(), active=(active,)))
    window.show()
    qt_app.processEvents()

    label = window.active_band._group_widgets[0].original
    image = label.grab().toImage()
    pixels = (
        image.pixelColor(x, y)
        for y in range(image.height())
        for x in range(image.width())
    )
    colors_seen = tuple(color for color in pixels if color.alpha() > 0)
    assert any(color.red() > color.blue() for color in colors_seen)
    assert any(color.blue() > color.red() for color in colors_seen)
    assert label.text() == text
    assert label.accessibleDescription() == "Word-timed lyric highlighting"
    window.close()


def test_progress_only_karaoke_update_does_not_relayout_or_restart_transition(
    qt_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()
    text = "karaoke line"
    initial = DesktopLyricGroup(
        "active",
        text,
        karaoke_segments=(DesktopKaraokeSegment("word", 0, len(text), 0.25),),
    )
    state = replace(_state(), active=(initial,))
    window.render_state(state)
    window.show()
    qt_app.processEvents()
    geometry = window.active_band._group_widgets[0].geometry()
    relayouts = 0
    original_relayout = window._lyric_column.relayout

    def counted_relayout(*, center_active: bool = False) -> None:
        nonlocal relayouts
        relayouts += 1
        original_relayout(center_active=center_active)

    monkeypatch.setattr(window._lyric_column, "relayout", counted_relayout)
    updated = replace(
        initial,
        karaoke_segments=(DesktopKaraokeSegment("word", 0, len(text), 0.75),),
    )
    window.render_state(replace(state, active=(updated,), position_us=42_030_000))

    label = window.active_band._group_widgets[0].original
    assert label.karaoke_segments[0].highlight_fraction == 0.75
    assert window.active_band._group_widgets[0].geometry() == geometry
    assert relayouts == 0
    assert not window._lyric_column.animation_running
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


def test_no_result_recovery_actions_remain_reachable_with_long_metadata(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    window.render_state(replace(_state(), state=DesktopLyricsState.NO_RESULT))
    window.resize(600, 720)
    window.show()
    qt_app.processEvents()

    assert window._header_layout.direction() is QBoxLayout.Direction.LeftToRight
    for button in (
        window.settings_button,
        window.review_button,
        window.details_button,
        window.library_button,
    ):
        top_left = button.mapTo(window, QPoint())
        assert button.isVisible()
        assert top_left.x() >= 0
        assert top_left.x() + button.width() <= window.width()

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


def test_open_window_semantic_profile_remains_authority_after_palette_change(
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

    assert window.palette().color(QPalette.ColorRole.Window) == QColor("#202124")
    assert window.title_label.palette().color(QPalette.ColorRole.WindowText) == QColor(
        "#f1f3f4"
    )

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
        if isinstance(dialog, SettingsWindow):
            assert dialog.categories.hasFocus()
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
    window.render_state(replace(timed, state=DesktopLyricsState.AMBIGUOUS))
    assert window.review_button.text() == "Possible lyrics matches…"
    assert window.review_action.text() == "&Possible lyrics matches…"
    window.render_state(replace(timed, state=DesktopLyricsState.NO_RESULT))
    assert "Search, refresh" in window.review_button.toolTip()
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


def test_library_results_are_local_actionable_and_include_every_counter(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_path = "/music/秘密/Artist - Song.flac"
    summary = LibraryScanSummary(
        7,
        discovered=9,
        processed=8,
        unchanged=7,
        moved=6,
        missing=5,
        review=4,
        downloaded=3,
        download_misses=2,
        errors=1,
        issues=(
            LibraryScanIssue(
                LibraryScanIssueCategory.METADATA_READ,
                private_path,
                "Audio tags could not be read.",
            ),
        ),
    )
    review = (LibraryReviewItem(private_path, "Song", ("Artist",), "review required"),)
    window = MainWindow()
    assert window.library_results_button.isHidden()
    window.set_library_scan_result(summary, review)
    assert not window.library_results_button.isHidden()

    dialog = LibraryReviewDialog(summary, review, window)
    counter_text = " ".join(label.text() for label in dialog.findChildren(QLabel))
    for value in range(1, 10):
        assert str(value) in counter_text
    assert dialog.items.topLevelItemCount() == 2
    assert "not included in diagnostic exports" in counter_text

    opened: list[str] = []
    monkeypatch.setattr(
        "konokashi.presentation.desktop.library_review_dialog.QDesktopServices.openUrl",
        lambda url: opened.append(url.toLocalFile()) or True,
    )
    dialog.items.setCurrentItem(dialog.items.topLevelItem(0))
    QTest.mouseClick(dialog.copy_path_button, Qt.MouseButton.LeftButton)
    assert qt_app.clipboard().text() == private_path
    QTest.mouseClick(dialog.open_folder_button, Qt.MouseButton.LeftButton)
    assert opened == ["/music/秘密"]

    rescans: list[bool] = []
    dialog.scan_again_requested.connect(lambda: rescans.append(True))
    QTest.mouseClick(dialog.scan_again_button, Qt.MouseButton.LeftButton)
    assert rescans == [True]
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
    assert "Record: 42" in dialog.alternative_details.text()
    assert "Text confidence:" in dialog.alternative_details.text()
    assert "Timing confidence:" in dialog.alternative_details.text()
    assert dialog.reset_delay_button.isEnabled()

    QTest.mouseClick(dialog.choose_button, Qt.MouseButton.LeftButton)
    action = dialog.action()
    assert action is not None
    assert action.kind is CorrectionActionKind.CHOOSE_ALTERNATIVE
    assert action.alternative is not None
    assert action.alternative.candidate.record_id == "42"

    reject_dialog = ReviewCorrectionDialog(_review_snapshot())
    QTest.mouseClick(
        reject_dialog.reject_alternative_button,
        Qt.MouseButton.LeftButton,
    )
    reject_action = reject_dialog.action()
    assert reject_action is not None
    assert reject_action.kind is CorrectionActionKind.REJECT_ALTERNATIVE
    assert reject_action.alternative is not None
    assert reject_action.alternative.candidate.record_id == "42"


def test_review_maps_recording_and_provider_title_artist_semantics_end_to_end(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    raw = player_snapshot(
        "strawberry",
        title="Moonlit Circuit",
        artists=("Aoi Test Artist", "Second Test Artist"),
        url="file:///music/moonlit-circuit.flac",
    )
    resolver, _overrides = track_resolver()
    track = resolver.resolve(raw)
    source_before = track.source_identity
    provider_candidate = LyricsProviderCandidate(
        provider="LRCLIB",
        record_id="moonlit-42",
        track_name="Moonlit Circuit",
        artist_name="Aoi Test Artist & Second Test Artist",
        album_name="Synthetic Album",
        duration_ms=180_000,
        instrumental=False,
        plain_lyrics="Synthetic lyric",
        synced_lyrics="[00:01.00]Synthetic lyric",
    )
    builder = ProviderLyricDocumentBuilder()
    document, diagnostics = builder.build(
        provider_candidate, datetime(2026, 9, 10, tzinfo=UTC)
    )
    assert document is not None
    assert diagnostics == ()
    storage = open_storage(tmp_path / "review-mapping.sqlite3")
    service = ReviewCorrectionService(
        track_overrides=storage.track_overrides,
        lyrics=storage.lyrics,
        matches=storage.lyrics_matches,
        provider_documents=builder,
        timing=storage.timing_calibrations,
    )
    alternative = LyricsAlternative(
        document_id=builder.document_id(provider_candidate),
        candidate=provider_candidate,
        confidence=LyricsMatchConfidence.HIGH,
        evidence=("synthetic exact semantics",),
    )
    review = service.snapshot(
        track,
        LyricsResolutionResult(
            source_identity=track.source_identity,
            status=LyricsResolutionStatus.FOUND_TIMED,
            document=document,
        ),
        LyricsAlternativeResult(track.source_identity, (alternative,)),
    )

    dialog = ReviewCorrectionDialog(review)

    assert dialog.title_edit.text() == "Moonlit Circuit"
    assert dialog.artists_edit.text() == ("Aoi Test Artist; Second Test Artist")
    assert "Aoi Test Artist & Second Test Artist — Moonlit Circuit" in (
        dialog.alternatives.itemText(0)
    )
    assert "Artist: Aoi Test Artist & Second Test Artist" in (
        dialog.alternative_details.text()
    )
    assert "Title: Moonlit Circuit" in dialog.alternative_details.text()
    assert track.raw_snapshot.metadata.title == "Moonlit Circuit"
    assert track.raw_snapshot.metadata.artists == (
        "Aoi Test Artist",
        "Second Test Artist",
    )
    assert track.source_identity == source_before
    assert storage.lyrics_matches.get(track.source_identity) is None
    dialog.close()


def test_review_dialog_track_and_delay_requests_are_typed(
    qt_app: QApplication,
) -> None:
    track_dialog = ReviewCorrectionDialog(_review_snapshot())
    track_dialog.title_edit.setText(" Corrected title ")
    track_dialog.artists_edit.setText("Artist A; Artist B")
    track_dialog.album_edit.setText(" Corrected album ")
    QTest.mouseClick(track_dialog.save_track_button, Qt.MouseButton.LeftButton)
    track_action = track_dialog.action()
    assert track_action is not None
    assert track_action.kind is CorrectionActionKind.PUT_TRACK_OVERRIDE
    assert track_action.title == "Corrected title"
    assert track_action.artists == ("Artist A", "Artist B")
    assert track_action.album == "Corrected album"

    delay_dialog = ReviewCorrectionDialog(_review_snapshot())
    delay_dialog.delay_ms.setValue(-250)
    QTest.mouseClick(delay_dialog.save_delay_button, Qt.MouseButton.LeftButton)
    delay_action = delay_dialog.action()
    assert delay_action is not None
    assert delay_action.kind is CorrectionActionKind.SET_DELAY
    assert delay_action.delay_us == -250_000

    search_dialog = ReviewCorrectionDialog(
        replace(_review_snapshot(), youtube_enrichment_available=True)
    )
    search_dialog.search_title_edit.setText(" Manual title ")
    search_dialog.search_artists_edit.setText("Artist A; Artist B")
    QTest.mouseClick(search_dialog.search_button, Qt.MouseButton.LeftButton)
    search_action = search_dialog.action()
    assert search_action is not None
    assert search_action.kind is CorrectionActionKind.SEARCH_MATCHES
    assert search_action.title == "Manual title"
    assert search_action.artists == ("Artist A", "Artist B")

    enrich_dialog = ReviewCorrectionDialog(
        replace(_review_snapshot(), youtube_enrichment_available=True)
    )
    assert enrich_dialog.enrich_button.isEnabled()
    QTest.mouseClick(enrich_dialog.enrich_button, Qt.MouseButton.LeftButton)
    enrich_action = enrich_dialog.action()
    assert enrich_action is not None
    assert enrich_action.kind is CorrectionActionKind.ENRICH_YOUTUBE


def test_review_dialog_exposes_routing_layers_and_aligned_translation_actions(
    qt_app: QApplication,
) -> None:
    status = RepresentationLayerStatus(
        RepresentationKind.TRANSLATED,
        RepresentationAvailability.AVAILABLE_HIDDEN,
        1,
        1,
        0,
        0,
        0,
        0,
        1,
        1,
        0,
        ("LRCLIB",),
        ("automatic translation backend is not implemented",),
    )
    snapshot = replace(
        _review_snapshot(),
        routing_status=LanguageRoutingStatus.AMBIGUOUS,
        routing_language=None,
        routing_diagnostic="Han-only text needs an exact-document choice",
        language_override="zh",
        layer_statuses=(status,),
        translation_lines=(
            TranslationReviewLine(
                "line-0001",
                "君の声",
                "I hear your voice",
                ContentProvenance.PROVIDER,
                ApprovalState.UNREVIEWED,
            ),
        ),
    )
    dialog = ReviewCorrectionDialog(snapshot)
    dialog.show()
    qt_app.processEvents()

    assert dialog.translation_original.text() == "Original (line-0001): 君の声"
    assert dialog.translation_edit.text() == "I hear your voice"
    assert dialog.automatic_language_button.isEnabled()
    audit = dialog.findChild(QPlainTextEdit)
    assert audit is not None
    assert "state: ambiguous" in audit.toPlainText()
    assert "translated: available-but-hidden" in audit.toPlainText()
    assert "origins LRCLIB" in audit.toPlainText()

    dialog.translation_edit.setText(" I can hear your voice ")
    QTest.mouseClick(dialog.save_translation_button, Qt.MouseButton.LeftButton)
    action = dialog.action()
    assert action is not None
    assert action.kind is CorrectionActionKind.PUT_TRANSLATION
    assert action.source_line_id == "line-0001"
    assert action.text == "I can hear your voice"

    reset = ReviewCorrectionDialog(snapshot)
    QTest.mouseClick(reset.reset_translation_button, Qt.MouseButton.LeftButton)
    reset_action = reset.action()
    assert reset_action is not None
    assert reset_action.kind is CorrectionActionKind.RESET_TRANSLATION
    assert reset_action.source_line_id == "line-0001"

    japanese = ReviewCorrectionDialog(snapshot)
    QTest.mouseClick(japanese.japanese_button, Qt.MouseButton.LeftButton)
    assert japanese.action() is not None
    assert japanese.action().kind is CorrectionActionKind.SET_LANGUAGE_JA  # type: ignore[union-attr]

    automatic = ReviewCorrectionDialog(snapshot)
    QTest.mouseClick(
        automatic.automatic_language_button,
        Qt.MouseButton.LeftButton,
    )
    assert automatic.action() is not None
    assert automatic.action().kind is CorrectionActionKind.RESET_LANGUAGE  # type: ignore[union-attr]


def _editor_snapshot() -> LyricEditorSnapshot:
    return LyricEditorSnapshot(
        "document-editor",
        "Test provider",
        180_000,
        (
            LyricEditorLine("line-1", "First", "First", None, None),
            LyricEditorLine("line-2", "Second", "Second", None, None),
        ),
        0,
        0,
        True,
    )


def test_line_editor_previews_stamps_advances_undoes_and_exports(
    qt_app: QApplication,
) -> None:
    position = [1_250]
    dialog = LyricEditorDialog(_editor_snapshot(), lambda: position[0])
    dialog.show()
    qt_app.processEvents()

    dialog.text_edit.setText("Corrected first")
    QTest.mouseClick(dialog.stamp_button, Qt.MouseButton.LeftButton)
    assert dialog.line_selector.currentIndex() == 1
    assert dialog.edits()[0].text == "Corrected first"
    assert dialog.edits()[0].start_ms == 1_250
    position[0] = 2_500
    QTest.mouseClick(dialog.stamp_button, Qt.MouseButton.LeftButton)
    assert dialog.edits()[1].start_ms == 2_500
    assert "[00:01.250]Corrected first" in dialog.preview.toPlainText()
    assert "line 2: Second" in dialog.preview_now.text()

    QTest.mouseClick(dialog.copy_lrc_button, Qt.MouseButton.LeftButton)
    assert QApplication.clipboard().text() == (
        "[00:01.250]Corrected first\n[00:02.500]Second\n"
    )
    QTest.mouseClick(dialog.undo_button, Qt.MouseButton.LeftButton)
    assert dialog.edits()[1].start_ms is None
    dialog.close()


def test_line_editor_imports_clipboard_and_review_can_revert_all(
    qt_app: QApplication,
) -> None:
    QApplication.clipboard().setText("[00:01.000]One\n[00:02.000]Two\n")
    editor = LyricEditorDialog(_editor_snapshot(), lambda: None)
    QTest.mouseClick(editor.import_button, Qt.MouseButton.LeftButton)
    assert editor.result() == QDialog.DialogCode.Accepted
    assert editor.imported_text() == "[00:01.000]One\n[00:02.000]Two\n"

    snapshot = replace(
        _review_snapshot(),
        lyric_editor=replace(_editor_snapshot(), corrected_lines=1),
    )
    review = ReviewCorrectionDialog(snapshot)
    assert review.open_editor_button.isEnabled()
    assert review.reset_lyrics_button.isEnabled()
    QTest.mouseClick(review.reset_lyrics_button, Qt.MouseButton.LeftButton)
    action = review.action()
    assert action is not None
    assert action.kind is CorrectionActionKind.RESET_LYRIC_EDITS


def test_review_dialog_keeps_actions_reachable_in_narrow_geometry(
    qt_app: QApplication,
) -> None:
    dialog = ReviewCorrectionDialog(_review_snapshot())
    dialog.resize(520, 620)
    dialog.show()
    qt_app.processEvents()

    buttons = dialog.findChild(QDialogButtonBox)
    assert buttons is not None
    assert buttons.isVisible()
    assert dialog.scroll_area.verticalScrollBar().maximum() > 0
    dialog.scroll_area.ensureWidgetVisible(dialog.save_delay_button)
    qt_app.processEvents()
    assert dialog.save_delay_button.isVisible()
    assert (
        dialog.scroll_area.viewport()
        .rect()
        .intersects(
            QRect(
                dialog.save_delay_button.mapTo(
                    dialog.scroll_area.viewport(),
                    dialog.save_delay_button.rect().topLeft(),
                ),
                dialog.save_delay_button.size(),
            )
        )
    )
    dialog.close()


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


def test_adjacent_lyric_change_animates_and_coalesces_without_losing_identity(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    first = _state()
    second = replace(
        first,
        previous=(first.active[0],),
        active=(first.next[0],),
        next=(DesktopLyricGroup("after-next", "after next lyric"),),
        position_us=43_000_000,
    )
    third = replace(
        second,
        previous=(second.active[0],),
        active=(second.next[0],),
        next=(DesktopLyricGroup("later", "later lyric"),),
        position_us=44_000_000,
    )
    window.show()
    window.render_state(first)
    qt_app.processEvents()

    window.render_state(second)
    assert window.active_band.text() == "next lyric"
    assert window._lyric_column.animation_running
    assert window._lyric_column.lyric_offset() > 0

    window.render_state(third)
    assert window.active_band.text() == "after next lyric"
    assert window._lyric_column.animation_running
    QTest.qWait(window.appearance_profile.motion.transition_ms + 40)
    assert not window._lyric_column.animation_running
    assert window._lyric_column.lyric_offset() == 0
    assert window._lyric_column.active_opacity == pytest.approx(1.0)
    window.close()


def test_seek_track_change_and_disabled_motion_snap_immediately(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    first = _state()
    window.render_state(first)
    window.show()
    qt_app.processEvents()

    seek = replace(
        first,
        previous=(DesktopLyricGroup("distant-before", "distant before"),),
        active=(DesktopLyricGroup("distant", "seek target"),),
        next=(DesktopLyricGroup("distant-after", "distant after"),),
        position_us=90_000_000,
    )
    window.render_state(seek)
    assert not window._lyric_column.animation_running
    assert window._lyric_column.lyric_offset() == 0

    changed_track = replace(seek, generation=2, title="Another track")
    window.render_state(changed_track)
    assert not window._lyric_column.animation_running

    no_motion = replace(
        window.appearance_profile,
        motion=replace(
            window.appearance_profile.motion,
            smooth_scrolling=False,
            reduced_motion=True,
        ),
    )
    window.set_appearance_profile(no_motion)
    adjacent = replace(
        changed_track,
        previous=changed_track.active,
        active=changed_track.next,
        next=(DesktopLyricGroup("final", "final"),),
    )
    window.render_state(adjacent)
    assert not window._lyric_column.animation_running
    assert window._lyric_column.active_opacity == pytest.approx(1.0)
    window.close()


def test_native_menu_alt_navigation_and_shortcuts(qt_app: QApplication) -> None:
    window = MainWindow()
    settings: list[bool] = []
    window.settings_requested.connect(lambda: settings.append(True))
    window.show()
    qt_app.processEvents()
    menu = window.application_menu
    assert [action.text() for action in menu.actions()] == [
        "&File",
        "&View",
        "&Lyrics",
        "&Help",
    ]
    QTest.keyClick(window, Qt.Key.Key_Alt)
    assert menu.hasFocus()
    assert menu.activeAction() is menu.actions()[0]
    QTest.keyClick(menu, Qt.Key.Key_Right)
    assert menu.activeAction() is menu.actions()[1]
    QTest.keyClick(menu, Qt.Key.Key_Escape)
    QTest.keyClick(window, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
    assert settings == [True]
    assert window.settings_action.shortcut().toString() == "Ctrl+,"
    assert window.quit_action.shortcut().toString() == "Ctrl+Q"
    assert not any(
        action.shortcut().toString() == "Ctrl+F" for action in window.actions()
    )
    QTest.keyClick(window, Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier)
    assert not window.isVisible()


def test_workspace_owns_stable_panels_across_projection(qt_app: QApplication) -> None:
    from konokashi.presentation.desktop.workspace import PanelId

    window = MainWindow()
    workspace = window.workspace
    assert workspace.panel_ids == tuple(PanelId)
    panels = tuple(workspace.panel(identity) for identity in PanelId)
    assert workspace.panel(PanelId.LYRICS) is window.content_stack
    assert workspace.panel(PanelId.PROGRESS) is window.playback_widget
    assert workspace.panel(PanelId.STATUS) is window.source_label
    assert all(panel.parentWidget() is workspace for panel in panels)
    window.render_state(_state())
    window.update_playback(_state())
    window.set_appearance_profile(window.appearance_profile)
    assert tuple(workspace.panel(identity) for identity in PanelId) == panels
    with pytest.raises(ValueError, match="already registered"):
        workspace.add_panel(PanelId.LYRICS, window.content_stack)
    window.close()
