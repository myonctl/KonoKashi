"""Measured wrapped lyric geometry and FLIP motion regressions."""

from __future__ import annotations

import os
from dataclasses import replace
from itertools import pairwise

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from konokashi.application.desktop_state import DesktopLyricGroup, DesktopViewState
from konokashi.domain.synchronization import PlaybackState
from konokashi.presentation.desktop.main_window import MainWindow
from tests.test_desktop_widgets import _state

CYRILLIC = (
    "Я слышу твой очень длинный голос через ночные улицы и далёкие города, "
    "когда музыка снова возвращается к нам. "
)
LATIN = (
    "Ya slyshu tvoy ochen dlinnyy golos cherez nochnye ulitsy i dalekie "
    "goroda, kogda muzyka snova vozvrashchaetsya k nam. "
)
TRANSLATION = (
    "I hear your very long voice across the night streets and distant cities "
    "when the music returns to us again. "
)


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    application = QApplication.instance() or QApplication(["lyric-geometry-test"])
    assert isinstance(application, QApplication)
    return application


def _stress_state(
    *,
    layers: tuple[bool, bool, bool] = (True, True, True),
    active_count: int = 1,
) -> DesktopViewState:
    original, romanized, translated = layers

    def group(line_id: str, repeat: int = 1) -> DesktopLyricGroup:
        return DesktopLyricGroup(
            line_id,
            CYRILLIC * repeat if original else None,
            LATIN * repeat if romanized else None,
            TRANSLATION * repeat if translated else None,
        )

    return replace(
        _state(),
        previous=(group("previous"),),
        active=tuple(group(f"active-{index}", 2) for index in range(active_count)),
        next=(group("next"),),
    )


def _layer_labels(window: MainWindow) -> tuple[QLabel, ...]:
    return tuple(
        label
        for band in (window.previous_band, window.active_band, window.next_band)
        for group in band._group_widgets
        if not group.isHidden()
        for label in (
            group.original,
            group.reading_caption,
            group.romanized,
            group.translation_caption,
            group.translation,
        )
        if not label.isHidden()
    )


def _content_rect(label: QLabel, window: MainWindow) -> QRect:
    top_left = label.mapTo(window._lyric_column._content, label.rect().topLeft())
    return QRect(top_left, label.size())


@pytest.mark.parametrize("size", ((520, 620), (760, 720), (1_440, 900)))
@pytest.mark.parametrize("lyric_scale", (50, 100, 200))
def test_wrapped_layers_have_disjoint_measured_geometry_at_supported_scales(
    qt_app: QApplication,
    size: tuple[int, int],
    lyric_scale: int,
) -> None:
    window = MainWindow()
    window.set_appearance_profile(
        replace(window.appearance_profile, lyric_scale_percent=lyric_scale)
    )
    window.resize(*size)
    window.render_state(_stress_state())
    window.show()
    qt_app.processEvents()
    QTest.qWait(120)
    window._lyric_column.relayout(center_active=True)

    labels = _layer_labels(window)
    rectangles = tuple(_content_rect(label, window) for label in labels)
    assert labels
    assert all(
        label.height() >= label.heightForWidth(label.width()) for label in labels
    )
    assert all(
        not left.intersects(right)
        for index, left in enumerate(rectangles)
        for right in rectangles[index + 1 :]
    )
    assert (
        max(rect.bottom() for rect in rectangles) < window._lyric_column._scene.height()
    )
    assert window._lyric_column._scene.height() >= window._lyric_column.document_height

    viewport = window._lyric_column.viewport().rect()
    active_rectangles = tuple(
        QRect(
            label.mapTo(window._lyric_column.viewport(), label.rect().topLeft()),
            label.size(),
        )
        for group in window.active_band._group_widgets
        if not group.isHidden()
        for label in (
            group.original,
            group.reading_caption,
            group.romanized,
            group.translation_caption,
            group.translation,
        )
        if not label.isHidden()
    )
    if any(not viewport.contains(rect) for rect in active_rectangles):
        assert window._lyric_column.verticalScrollBar().maximum() > 0
        assert window._lyric_column.verticalScrollBar().isVisible()
    window.close()


@pytest.mark.parametrize(
    "layers",
    (
        (True, False, False),
        (True, True, False),
        (True, False, True),
        (True, True, True),
        (False, True, False),
        (False, False, True),
        (False, True, True),
    ),
)
def test_empty_and_partial_layer_combinations_keep_active_geometry_disjoint(
    qt_app: QApplication,
    layers: tuple[bool, bool, bool],
) -> None:
    window = MainWindow()
    window.resize(520, 620)
    window.render_state(_stress_state(layers=layers, active_count=2))
    window.show()
    qt_app.processEvents()
    QTest.qWait(120)

    labels = _layer_labels(window)
    rectangles = tuple(_content_rect(label, window) for label in labels)
    assert len(window.active_band._group_widgets) == 2
    assert all(
        not left.intersects(right)
        for index, left in enumerate(rectangles)
        for right in rectangles[index + 1 :]
    )
    assert window.active_band.height() >= window.active_band.heightForWidth(
        window.active_band.width()
    )
    window.close()


def test_fully_empty_layer_groups_fall_back_to_the_status_page(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    window.resize(520, 620)
    window.render_state(_stress_state(layers=(False, False, False)))
    window.show()
    qt_app.processEvents()

    assert not _layer_labels(window)
    assert window.content_stack.currentWidget() is window._state_page
    assert window._lyric_column.document_height == 0
    window.close()


def _adjacent_states(
    old_active: str, incoming: str
) -> tuple[DesktopViewState, DesktopViewState]:
    first = replace(
        _state(original=old_active, romanized=None),
        active=(DesktopLyricGroup("old", old_active),),
        next=(DesktopLyricGroup("incoming", incoming),),
    )
    second = replace(
        first,
        previous=first.active,
        active=(DesktopLyricGroup("incoming", incoming),),
        next=(DesktopLyricGroup("later", "later lyric"),),
        position_us=(first.position_us or 0) + 1_000_000,
    )
    return first, second


@pytest.mark.parametrize(
    ("old_active", "incoming"),
    (
        ("short", CYRILLIC * 3),
        (CYRILLIC * 3, "short"),
        ("equal first", "equal second"),
    ),
)
def test_measured_flip_motion_converges_monotonically_to_exact_final_geometry(
    qt_app: QApplication,
    old_active: str,
    incoming: str,
) -> None:
    window = MainWindow()
    window.resize(520, 620)
    first, second = _adjacent_states(old_active, incoming)
    window.render_state(first)
    window.show()
    qt_app.processEvents()
    QTest.qWait(120)

    old_incoming_center = window._lyric_column._visual_center(("incoming",))
    assert old_incoming_center is not None
    window.render_state(second)
    new_incoming_center = window._lyric_column._visual_center(("incoming",))
    assert new_incoming_center == pytest.approx(old_incoming_center, abs=1.5)
    assert window._lyric_column.animation_running
    samples = [abs(window._lyric_column.lyric_offset())]
    for _index in range(5):
        QTest.qWait(max(10, window.appearance_profile.motion.transition_ms // 6))
        samples.append(abs(window._lyric_column.lyric_offset()))
    QTest.qWait(window.appearance_profile.motion.transition_ms + 40)
    samples.append(abs(window._lyric_column.lyric_offset()))

    assert samples[0] > 0
    assert all(later <= earlier for earlier, later in pairwise(samples))
    assert samples[-1] == 0
    assert not window._lyric_column.animation_running
    animated_final = window.active_band._group_widgets[0].geometry()

    window.render_state(second)
    qt_app.processEvents()
    assert window._lyric_column.lyric_offset() == 0
    assert window.active_band._group_widgets[0].geometry() == animated_final
    window.close()


def test_coalesced_transition_starts_at_current_visual_line_position(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    first, second = _adjacent_states("first", "second")
    third = replace(
        second,
        previous=second.active,
        active=second.next,
        next=(DesktopLyricGroup("fourth", "fourth"),),
        position_us=(second.position_us or 0) + 1_000_000,
    )
    window.render_state(first)
    window.show()
    qt_app.processEvents()
    window.render_state(second)
    QTest.qWait(window.appearance_profile.motion.transition_ms // 3)

    old_visual_center = window._lyric_column._visual_center(("later",))
    assert old_visual_center is not None
    window.render_state(third)
    new_visual_center = window._lyric_column._visual_center(("later",))
    assert new_visual_center == pytest.approx(old_visual_center, abs=1.5)
    assert window._lyric_column.animation_running
    QTest.qWait(window.appearance_profile.motion.transition_ms + 40)
    assert window._lyric_column.lyric_offset() == 0
    window.close()


def test_two_original_lines_share_one_measured_transition_anchor(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    window.resize(520, 620)
    incoming = (
        DesktopLyricGroup("incoming-1", CYRILLIC * 2),
        DesktopLyricGroup("incoming-2", CYRILLIC),
    )
    first = replace(
        _state(original="departing", romanized=None),
        active=(DesktopLyricGroup("departing", "departing"),),
        next=incoming,
    )
    second = replace(
        first,
        previous=first.active,
        active=incoming,
        next=(DesktopLyricGroup("later", "later"),),
        position_us=(first.position_us or 0) + 1_000_000,
    )
    window.render_state(first)
    window.show()
    qt_app.processEvents()
    QTest.qWait(120)

    incoming_ids = tuple(group.line_id for group in incoming)
    old_center = window._lyric_column._visual_center(incoming_ids)
    assert old_center is not None
    window.render_state(second)
    assert window._lyric_column._visual_center(incoming_ids) == pytest.approx(
        old_center, abs=1.5
    )
    assert window._lyric_column.animation_running

    active_rectangles = tuple(
        _content_rect(label, window)
        for group in window.active_band._group_widgets
        for label in (
            group.original,
            group.reading_caption,
            group.romanized,
            group.translation_caption,
            group.translation,
        )
        if not label.isHidden()
    )
    assert all(
        not left.intersects(right)
        for index, left in enumerate(active_rectangles)
        for right in active_rectangles[index + 1 :]
    )
    QTest.qWait(window.appearance_profile.motion.transition_ms + 40)
    assert window._lyric_column.lyric_offset() == 0
    assert not window._lyric_column.animation_running
    window.close()


@pytest.mark.parametrize(
    ("smooth", "reduced"),
    ((False, False), (True, True)),
)
def test_instant_and_reduced_motion_never_move_or_fade(
    qt_app: QApplication,
    smooth: bool,
    reduced: bool,
) -> None:
    window = MainWindow()
    window.set_appearance_profile(
        replace(
            window.appearance_profile,
            motion=replace(
                window.appearance_profile.motion,
                smooth_scrolling=smooth,
                reduced_motion=reduced,
            ),
        )
    )
    first, second = _adjacent_states("first", "second")
    window.render_state(first)
    window.show()
    qt_app.processEvents()
    window.render_state(second)

    assert not window._lyric_column.animation_running
    assert window._lyric_column.lyric_offset() == 0
    assert window._lyric_column.active_opacity == pytest.approx(1.0)
    window.close()


def test_resize_appearance_pause_and_close_settle_inflight_motion(
    qt_app: QApplication,
) -> None:
    window = MainWindow()
    window.resize(520, 620)
    first, second = _adjacent_states("first", CYRILLIC * 2)
    window.render_state(first)
    window.show()
    qt_app.processEvents()

    window.render_state(second)
    assert window._lyric_column.animation_running
    window.resize(760, 720)
    qt_app.processEvents()
    assert not window._lyric_column.animation_running
    assert window._lyric_column.lyric_offset() == 0

    first, second = _adjacent_states("first", "second")
    window.render_state(first)
    window.render_state(second)
    assert window._lyric_column.animation_running
    window.set_appearance_profile(
        replace(window.appearance_profile, lyric_scale_percent=150)
    )
    assert not window._lyric_column.animation_running
    assert window._lyric_column.lyric_offset() == 0

    window.render_state(first)
    window._lyric_column.settle()
    window.render_state(second)
    assert window._lyric_column.animation_running
    window.render_state(replace(second, playback_state=PlaybackState.PAUSED))
    assert not window._lyric_column.animation_running
    assert window._lyric_column.active_opacity == pytest.approx(1.0)

    window.render_state(first)
    window._lyric_column.settle()
    window.render_state(second)
    assert window._lyric_column.animation_running
    window.close()
    qt_app.processEvents()
    assert not window._lyric_column.animation_running
    assert window._lyric_column.lyric_offset() == 0
