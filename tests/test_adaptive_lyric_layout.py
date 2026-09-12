"""Adaptive multilingual lyric layout across supported desktop projections."""

from __future__ import annotations

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from konokashi.application.desktop_state import (
    DesktopLyricGroup,
    DesktopLyricsState,
    DesktopViewState,
)
from konokashi.presentation.desktop.main_window import MainWindow
from konokashi.presentation.desktop.window_surface import DesktopWindowMode

MULTILINGUAL_CASES = (
    (
        "japanese-romaji",
        "遠い空の向こうまで君の声を探して歩き続ける夜に光が差し込む",
        "tooi sora no mukou made kimi no koe o sagashite arukitsuzukeru "
        "yoru ni hikari ga sashikomu",
        "I keep walking through the night, searching beyond the distant sky "
        "for your voice.",
    ),
    (
        "chinese-pinyin",
        "穿过安静的城市我们终于看见清晨第一道温柔的光",
        "chuān guò ān jìng de chéng shì wǒ men zhōng yú kàn jiàn qīng "
        "chén dì yī dào wēn róu de guāng",
        "Crossing the quiet city, we finally see the first gentle light of morning.",
    ),
    (
        "korean-reading",
        "조용한 밤길을 따라서 너의 목소리가 들리는 곳까지 걸어가",
        "joyonghan bamgireul ttaraseo neoui moksoriga deullineun gotkkaji georeoga",
        "I follow the quiet night road to the place where I can hear your voice.",
    ),
    (
        "three-independent-layers",
        "星明かりの下で新しい朝を待ちながら歌い続ける",
        "hoshiakari no shita de atarashii asa o machinagara utaitsuzukeru",
        "We keep singing under the starlight while waiting for a new morning.",
    ),
    (
        "long-english",
        "When the city falls silent, I follow every distant light until the "
        "long road carries me safely home again.",
        None,
        None,
    ),
)

MODE_SCENARIOS = (
    ("narrow-normal", DesktopWindowMode.NORMAL, (420, 420), 100),
    ("short-compact", DesktopWindowMode.COMPACT, (380, 280), 150),
    ("short-overlay", DesktopWindowMode.OVERLAY, (420, 180), 100),
    ("ultrawide-large-text", DesktopWindowMode.NORMAL, (1_440, 900), 200),
    ("fullscreen-large-text", DesktopWindowMode.FULLSCREEN, None, 200),
)


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    application = QApplication.instance() or QApplication(["adaptive-layout-test"])
    assert isinstance(application, QApplication)
    return application


def _state(
    original: str,
    reading: str | None,
    translation: str | None,
    *,
    active: tuple[DesktopLyricGroup, ...] | None = None,
    context_count: int = 2,
) -> DesktopViewState:
    previous = tuple(
        DesktopLyricGroup(f"previous-{index}", f"Previous lyric {index}")
        for index in range(context_count)
    )
    following = tuple(
        DesktopLyricGroup(f"following-{index}", f"Following lyric {index}")
        for index in range(context_count)
    )
    return DesktopViewState(
        DesktopLyricsState.TIMED,
        "Synchronized lyrics",
        previous=previous,
        active=active or (DesktopLyricGroup("active", original, reading, translation),),
        next=following,
    )


def _settle_layout(window: MainWindow, app: QApplication) -> None:
    app.processEvents()
    window._finish_resize_typography()
    window._lyric_column.relayout(center_active=True)
    app.processEvents()


def _visible_labels(window: MainWindow) -> tuple[QLabel, ...]:
    return tuple(
        label
        for band in (window.previous_band, window.active_band, window.next_band)
        for group in band._group_widgets
        if not group.isHidden()
        for label in (
            group.original,
            group.romanized,
            group.translation,
        )
        if not label.isHidden()
    )


def _semantic_active_labels(window: MainWindow) -> tuple[QLabel, ...]:
    return tuple(
        label
        for group in window.active_band._group_widgets
        if not group.isHidden()
        for label in (group.original, group.romanized, group.translation)
        if not label.isHidden()
    )


def _mapped_rect(label: QLabel, target: QWidget) -> QRect:
    top_left = label.mapTo(target, label.rect().topLeft())
    return QRect(top_left, label.size())


@pytest.mark.parametrize(
    ("case_name", "original", "reading", "translation"), MULTILINGUAL_CASES
)
@pytest.mark.parametrize(("scenario", "mode", "size", "lyric_scale"), MODE_SCENARIOS)
def test_multilingual_lyrics_adapt_without_label_clipping_or_collision(
    qt_app: QApplication,
    case_name: str,
    original: str,
    reading: str | None,
    translation: str | None,
    scenario: str,
    mode: DesktopWindowMode,
    size: tuple[int, int] | None,
    lyric_scale: int,
) -> None:
    del case_name, scenario
    window = MainWindow(overlay_recovery_available=True)
    window.set_appearance_profile(
        replace(window.appearance_profile, lyric_scale_percent=lyric_scale)
    )
    window.render_state(_state(original, reading, translation))
    window.show()
    if mode is not DesktopWindowMode.NORMAL:
        window.set_window_mode(mode)
    if size is not None:
        window.resize(*size)
    _settle_layout(window, qt_app)

    visible = _visible_labels(window)
    active = _semantic_active_labels(window)
    content_rectangles = tuple(
        _mapped_rect(label, window._lyric_column._content) for label in visible
    )
    assert visible
    assert len(active) == 1 + int(reading is not None) + int(translation is not None)
    assert all(
        label.height() >= label.heightForWidth(label.width()) for label in visible
    )
    assert all(
        not left.intersects(right)
        for index, left in enumerate(content_rectangles)
        for right in content_rectangles[index + 1 :]
    )
    assert (
        max(rect.bottom() for rect in content_rectangles)
        < window._lyric_column._scene.height()
    )

    scrollbar = window._lyric_column.verticalScrollBar()
    if scrollbar.maximum() == 0:
        viewport = window._lyric_column.viewport().rect()
        assert all(
            viewport.contains(_mapped_rect(label, window._lyric_column.viewport()))
            for label in active
        )
    else:
        # Only the deliberately bounded last resort may scroll; text widgets still
        # own their complete measured height, so scrolling never reveals cut glyphs.
        assert window.previous_band.visible_group_count == 0
        assert window.next_band.visible_group_count == 0
        assert window._lyric_column.adaptive_fit_scale == pytest.approx(0.35)
    window.close()


def test_context_reduction_preserves_nearest_lines_and_restores_roomy_layout(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    profile = replace(
        window.appearance_profile,
        context=replace(window.appearance_profile.context, previous=4, following=4),
    )
    window.set_appearance_profile(profile)
    window.render_state(
        _state(
            "君の声が聞こえる夜を歩いてゆく",
            "kimi no koe ga kikoeru yoru o aruite yuku",
            "I walk through the night where I can hear your voice.",
            context_count=4,
        )
    )
    window.resize(760, 720)
    window.show()
    _settle_layout(window, qt_app)
    assert window.previous_band.visible_group_count == 4
    assert window.next_band.visible_group_count == 4

    window.resize(420, 420)
    _settle_layout(window, qt_app)
    previous_count = window.previous_band.visible_group_count
    following_count = window.next_band.visible_group_count
    assert previous_count + following_count < 8
    assert window._lyric_column.adaptive_fit_scale == pytest.approx(1.0)
    assert tuple(group.line_id for group in window.previous_band._rendered_groups) == (
        tuple(f"previous-{index}" for index in range(4))[-previous_count:]
        if previous_count
        else ()
    )
    assert tuple(group.line_id for group in window.next_band._rendered_groups) == tuple(
        f"following-{index}" for index in range(following_count)
    )

    window.resize(760, 720)
    _settle_layout(window, qt_app)
    assert window.previous_band.visible_group_count == 4
    assert window.next_band.visible_group_count == 4
    assert window._lyric_column.adaptive_fit_scale == pytest.approx(1.0)
    window.close()


def test_active_layers_outlive_context_before_typography_fits(
    qt_app: QApplication,
) -> None:
    _, original, reading, translation = MULTILINGUAL_CASES[0]
    window = MainWindow()
    window.set_appearance_profile(
        replace(window.appearance_profile, lyric_scale_percent=200)
    )
    window.render_state(_state(original, reading, translation))
    window.resize(420, 420)
    window.show()
    _settle_layout(window, qt_app)

    assert window.previous_band.visible_group_count == 0
    assert window.next_band.visible_group_count == 0
    assert 0.35 < window._lyric_column.adaptive_fit_scale < 1.0
    assert len(_semantic_active_labels(window)) == 3
    assert window._lyric_column.verticalScrollBar().maximum() == 0
    window.close()


def test_multiple_active_lines_keep_every_layer_measured(
    qt_app: QApplication,
) -> None:
    active = tuple(
        DesktopLyricGroup(f"active-{index}", original, reading, translation)
        for index, (_, original, reading, translation) in enumerate(
            MULTILINGUAL_CASES[:2]
        )
    )
    window = MainWindow()
    window.set_appearance_profile(
        replace(window.appearance_profile, lyric_scale_percent=150)
    )
    window.render_state(_state("unused", None, None, active=active))
    window.resize(520, 620)
    window.show()
    _settle_layout(window, qt_app)

    assert window.active_band.visible_group_count == 2
    labels = _semantic_active_labels(window)
    rectangles = tuple(
        _mapped_rect(label, window._lyric_column._content) for label in labels
    )
    assert len(labels) == 6
    assert all(
        label.height() >= label.heightForWidth(label.width()) for label in labels
    )
    assert all(
        not left.intersects(right)
        for index, left in enumerate(rectangles)
        for right in rectangles[index + 1 :]
    )
    window.close()
