"""Stage 12 schema-driven desktop settings widget regressions."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QPalette, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDial,
    QDoubleSpinBox,
    QFileDialog,
    QMessageBox,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from konokashi.application.settings import (
    SETTINGS_BY_KEY,
    SettingOrigin,
    SettingsDiagnostic,
    SettingType,
    validate_settings_values,
)
from konokashi.infrastructure.desktop_portal import DesktopPortalError
from konokashi.presentation.desktop.settings_input import SettingsWheelGuard
from konokashi.presentation.desktop.settings_window import (
    AnimationSpeedEditor,
    FontWeightEditor,
    LyricTransitionEditor,
    OrderedStringListEditor,
    SemanticStringEditor,
    SettingsWindow,
)


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    application = QApplication.instance() or QApplication(["settings-window-test"])
    assert isinstance(application, QApplication)
    return application


def _window(qt_app: QApplication, path: Path) -> SettingsWindow:
    window = SettingsWindow(path)
    window.set_snapshot(validate_settings_values({}))
    window.show()
    qt_app.processEvents()
    return window


def test_every_canonical_setting_appears_once_in_actual_category_and_type(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")

    assert set(window.rows) == set(SETTINGS_BY_KEY)
    assert [
        window.categories.item(index).text()
        for index in range(window.categories.count())
    ] == [
        "Players / MPRIS",
        "Lyrics",
        "Desktop",
        "Library",
        "Presets & Defaults",
        "Typography",
        "Colors",
        "Progress",
        "Visibility",
        "Spacing",
        "Motion",
    ]
    for key, row in window.rows.items():
        definition = SETTINGS_BY_KEY[key]
        assert row.definition is definition
        assert definition.title
        assert definition.description
        if definition.value_type is SettingType.BOOLEAN:
            expected = (
                LyricTransitionEditor
                if definition.key == "appearance.motion.smooth_scrolling"
                else QCheckBox
            )
            assert isinstance(row.editor, expected)
        elif definition.value_type is SettingType.INTEGER:
            if definition.key == "appearance.motion.transition_ms":
                assert isinstance(row.editor, AnimationSpeedEditor)
            elif definition.key.endswith(".weight"):
                assert isinstance(row.editor, FontWeightEditor)
            else:
                assert isinstance(row.editor, QSpinBox)
                assert row.editor.minimum() == definition.minimum
                assert row.editor.maximum() == definition.maximum
        elif definition.value_type is SettingType.STRING:
            assert isinstance(row.editor, SemanticStringEditor)
        else:
            assert isinstance(row.editor, OrderedStringListEditor)
        assert row.origin_label.text() == "Default"
        assert not row.reset_button.isEnabled()
    assert (
        window.rows["library.metadata_workers"].reload_label.text()
        == "Applies to the next operation · global"
    )
    window.close()


def test_snapshot_sets_current_values_origins_defaults_and_reset_state(
    qt_app: QApplication, tmp_path: Path
) -> None:
    roots = (str(tmp_path),)
    values = {
        "players.preferred": ("strawberry", "vlc"),
        "lyrics.display.translated": True,
        "library.roots": roots,
        "library.metadata_workers": 7,
    }
    snapshot = validate_settings_values(
        values,
        explicit_keys=frozenset(values),
    )
    window = _window(qt_app, tmp_path / "config.toml")
    window.set_snapshot(snapshot)

    preferred = window.rows["players.preferred"]
    assert isinstance(preferred.editor, OrderedStringListEditor)
    assert preferred.editor.value() == ("strawberry", "vlc")
    translated = window.rows["lyrics.display.translated"]
    assert isinstance(translated.editor, QCheckBox)
    assert translated.editor.isChecked()
    workers = window.rows["library.metadata_workers"]
    assert isinstance(workers.editor, QSpinBox)
    assert workers.editor.value() == 7
    for key in values:
        assert window.rows[key].reset_button.isEnabled()
        assert "config-file" in window.rows[key].origin_label.text()
    assert snapshot.resolved("players.ignored").origin is SettingOrigin.DEFAULT
    window.close()


def test_appearance_string_controls_emit_canonical_keys_and_global_reset(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    changes: list[tuple[str, object]] = []
    resets: list[str] = []
    window.change_requested.connect(lambda key, value: changes.append((key, value)))
    window.reset_appearance_requested.connect(lambda: resets.append("appearance"))

    row = window.rows["appearance.colors.active_lyric"]
    assert isinstance(row.editor, SemanticStringEditor)
    assert row.editor.input is not None
    row.editor.input.setText("#AABBCC")
    row.editor.input.editingFinished.emit()

    def confirm() -> None:
        dialog = qt_app.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        button = next(b for b in dialog.buttons() if b.text() == "Reset Appearance")
        button.click()

    QTimer.singleShot(0, confirm)
    window.reset_appearance_button.click()

    assert changes == [("appearance.colors.active_lyric", "#AABBCC")]
    assert resets == ["appearance"]
    window.close()


def test_typed_controls_emit_only_canonical_key_and_typed_value(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    changes: list[tuple[str, object]] = []
    resets: list[str] = []
    window.change_requested.connect(lambda key, value: changes.append((key, value)))
    window.reset_requested.connect(resets.append)

    translated = window.rows["lyrics.display.translated"]
    assert isinstance(translated.editor, QCheckBox)
    translated.editor.click()

    workers = window.rows["library.metadata_workers"]
    assert isinstance(workers.editor, QSpinBox)
    workers.editor.setValue(6)
    workers.editor.editingFinished.emit()

    preferred = window.rows["players.preferred"]
    assert isinstance(preferred.editor, OrderedStringListEditor)
    preferred.editor.input.setText("strawberry")
    preferred.editor.add_button.click()
    preferred.reset_button.setEnabled(True)
    preferred.reset_button.click()

    assert changes == [
        ("lyrics.display.translated", True),
        ("library.metadata_workers", 6),
        ("players.preferred", ("strawberry",)),
    ]
    assert resets == ["players.preferred"]
    window.close()


@pytest.mark.parametrize(
    ("query", "visible_key"),
    [
        ("metadata workers", "library.metadata_workers"),
        ("policy-approved", "library.automatic_downloads"),
        ("desktop.lyrics.selectable", "desktop.lyrics.selectable"),
    ],
)
def test_search_uses_title_description_and_canonical_key(
    qt_app: QApplication,
    tmp_path: Path,
    query: str,
    visible_key: str,
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    window.search.setText(query)
    qt_app.processEvents()

    assert window.rows[visible_key].isVisible()
    assert sum(row.isVisible() for row in window.rows.values()) == 1
    assert not window.no_results.isVisible()
    window.search.setText("no setting can match this phrase")
    qt_app.processEvents()
    assert window.no_results.isVisible()
    window.close()


def test_external_diagnostics_are_actionable_without_replacing_controls(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    row = window.rows["lyrics.display.translated"]
    assert isinstance(row.editor, QCheckBox)
    assert not row.editor.isChecked()

    diagnostic = SettingsDiagnostic(
        "Expected boolean, got str 'invalid'.",
        key="lyrics.display.translated",
        path=tmp_path / "config.toml",
    )
    window.set_diagnostics(
        (diagnostic,),
        context="The configuration file was rejected; current values remain active.",
    )

    assert window.error_banner.isVisible()
    assert "current values remain active" in window.error_banner.text()
    assert "lyrics.display.translated" in window.error_banner.text()
    assert not row.editor.isChecked()
    window.set_diagnostics(())
    assert not window.error_banner.isVisible()
    window.close()


def test_keyboard_starts_with_search_then_category_navigation(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    window.search.setFocus()
    qt_app.processEvents()
    assert qt_app.focusWidget() is window.search
    QTest.keyClick(window.search, Qt.Key.Key_Tab)
    assert qt_app.focusWidget() is window.categories
    QTest.keyClick(window, Qt.Key.Key_Escape)
    qt_app.processEvents()
    assert not window.isVisible()


def test_config_path_is_copyable_and_folder_open_uses_qt_helper(
    qt_app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.toml"
    window = _window(qt_app, path)
    opened: list[QUrl] = []
    monkeypatch.setattr(
        "konokashi.presentation.desktop.settings_window.QDesktopServices.openUrl",
        lambda url: opened.append(url) or True,
    )

    window.copy_path_button.click()
    assert QApplication.clipboard().text() == str(path)
    window.open_location_button.click()
    assert opened == [QUrl.fromLocalFile(str(tmp_path))]
    window.close()


@pytest.mark.parametrize("dark", (False, True))
def test_settings_window_uses_system_palette_without_color_only_state(
    qt_app: QApplication, tmp_path: Path, dark: bool
) -> None:
    original = qt_app.palette()
    palette = QPalette()
    background = QColor("#202124") if dark else QColor("#f8f9fa")
    foreground = QColor("#f1f3f4") if dark else QColor("#202124")
    palette.setColor(QPalette.ColorRole.Window, background)
    palette.setColor(QPalette.ColorRole.WindowText, foreground)
    qt_app.setPalette(palette)
    try:
        window = _window(qt_app, tmp_path / "config.toml")
        assert window.styleSheet() == ""
        assert window.palette().color(QPalette.ColorRole.Window) == background
        assert window.error_banner.property("error") is False
        window.close()
    finally:
        qt_app.setPalette(original)


def test_save_retains_middle_page_focus_scroll_and_widget_identity(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    window.set_diagnostics(())
    key = "appearance.colors.metadata_secondary"
    row = window.rows[key]
    for index in range(window.categories.count()):
        if window.categories.item(index).text() == row.definition.category.value:
            window.categories.setCurrentRow(index)
    toggle = window._advanced_toggles.get(row.definition.category)
    if toggle is not None and row in window._advanced_rows[row.definition.category]:
        toggle.setChecked(True)
    qt_app.processEvents()
    page = window.pages.currentWidget()
    page.ensureWidgetVisible(row)
    row.editor.input.setFocus()
    qt_app.processEvents()
    focus = qt_app.focusWidget()
    position = page.verticalScrollBar().value()
    category = window.categories.currentRow()
    assert 0 < position < page.verticalScrollBar().maximum()
    assert window.mark_pending(key)
    qt_app.processEvents()
    assert qt_app.focusWidget() is focus
    assert page.verticalScrollBar().value() == position
    window.set_snapshot(validate_settings_values({key: "#ABCDEF"}))
    window.set_diagnostics(())
    qt_app.processEvents()
    assert window.rows[key] is row
    assert qt_app.focusWidget() is focus
    assert page.verticalScrollBar().value() == position
    assert window.categories.currentRow() == category
    window.close()


def test_external_projection_preserves_unsubmitted_text_and_search(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    window.search.setText("progress")
    editor = window.rows["appearance.colors.progress"].editor
    assert isinstance(editor, SemanticStringEditor)
    assert editor.input is not None
    editor.input.setText("#AB")
    editor.input.setCursorPosition(2)
    window.set_snapshot(validate_settings_values({"lyrics.display.translated": True}))
    assert window.search.text() == "progress"
    assert editor.input.text() == "#AB"
    assert editor.input.cursorPosition() == 2
    window.close()


def test_find_shortcut_and_escape_search_navigation(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    window.categories.setFocus()
    QTest.keyClick(window, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    assert window.search.hasFocus()
    assert window.search.placeholderText() == "Search settings…"
    window.search.setText("progress")
    QTest.keyClick(window.search, Qt.Key.Key_Escape)
    assert not window.search.text()
    assert window.isVisible()
    QTest.keyClick(window.search, Qt.Key.Key_Escape)
    assert window.categories.hasFocus()
    window.close()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("appearance.visibility.progress", False),
        ("appearance.typography.original.size", 30),
        ("appearance.alignment.lyrics", "left"),
        ("appearance.typography.original.family", "DejaVu Sans"),
        ("appearance.colors.progress", "#ABCDEF"),
        ("appearance.preset", "compact"),
    ],
)
def test_save_and_reset_keep_logical_focus(
    qt_app: QApplication, tmp_path: Path, key: str, value: object
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    window.set_diagnostics(())
    row = window.rows[key]
    for index in range(window.categories.count()):
        if window.categories.item(index).text() == row.definition.category.value:
            window.categories.setCurrentRow(index)
    toggle = window._advanced_toggles.get(row.definition.category)
    if toggle is not None and row in window._advanced_rows[row.definition.category]:
        toggle.setChecked(True)
    qt_app.processEvents()
    editor = row.editor
    target = (
        editor.focus_widgets()[0]
        if isinstance(editor, SemanticStringEditor)
        else editor
    )
    target.setFocus()
    qt_app.processEvents()
    assert window.mark_pending(key)
    window.set_snapshot(
        validate_settings_values({key: value}, explicit_keys=frozenset({key}))
    )
    qt_app.processEvents()
    assert qt_app.focusWidget() is target
    row.reset_button.setFocus()
    qt_app.processEvents()
    assert window.mark_pending(key)
    window.set_snapshot(validate_settings_values({}))
    qt_app.processEvents()
    assert row.reset_button.hasFocus()
    window.close()


def test_minimum_layout_has_no_horizontal_category_scroll(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    window.resize(window.minimumSize())
    for index in range(window.categories.count()):
        window.categories.setCurrentRow(index)
        qt_app.processEvents()
        assert window.pages.currentWidget().horizontalScrollBar().maximum() == 0
        assert window.search.isVisible()
    window.close()


@pytest.mark.parametrize("cancel_key", [Qt.Key.Key_Escape, Qt.Key.Key_Return])
def test_reset_appearance_cancel_is_default_and_never_writes(
    qt_app: QApplication, tmp_path: Path, cancel_key: Qt.Key
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    resets: list[bool] = []
    window.reset_appearance_requested.connect(lambda: resets.append(True))

    def cancel() -> None:
        dialog = qt_app.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        assert dialog.defaultButton().text() == "Cancel"
        QTest.keyClick(dialog, cancel_key)

    QTimer.singleShot(0, cancel)
    window.reset_appearance_button.click()
    assert not resets
    window.close()


def test_confirmed_appearance_reset_projection_preserves_viewport_and_focus(
    qt_app: QApplication, tmp_path: Path
) -> None:
    values = {
        "appearance.colors.metadata_secondary": "#123456",
        "appearance.opacity.inactive_line": 35,
    }
    window = _window(qt_app, tmp_path / "config.toml")
    window.set_snapshot(
        validate_settings_values(values, explicit_keys=frozenset(values))
    )
    row = window.rows["appearance.colors.metadata_secondary"]
    window.categories.setCurrentItem(window._category_items[row.definition.category])
    window._advanced_toggles[row.definition.category].setChecked(True)
    qt_app.processEvents()
    page = window.pages.currentWidget()
    page.ensureWidgetVisible(row)
    assert isinstance(row.editor, SemanticStringEditor)
    target = row.editor.focus_widgets()[0]
    target.setFocus()
    qt_app.processEvents()
    position = page.verticalScrollBar().value()
    assert position > 0

    assert window.mark_pending("appearance")
    window.set_snapshot(validate_settings_values({}))
    qt_app.processEvents()

    assert page.verticalScrollBar().value() == position
    assert qt_app.focusWidget() is target
    window.close()


@pytest.mark.parametrize(
    "key",
    ["appearance.typography.original.size", "appearance.typography.original.family"],
)
def test_wheel_over_focused_value_editor_scrolls_page_without_editing(
    qt_app: QApplication, tmp_path: Path, key: str
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    row = window.rows[key]
    for index in range(window.categories.count()):
        if window.categories.item(index).text() == row.definition.category.value:
            window.categories.setCurrentRow(index)
    toggle = window._advanced_toggles.get(row.definition.category)
    if toggle is not None and row in window._advanced_rows[row.definition.category]:
        toggle.setChecked(True)
    qt_app.processEvents()
    editor = row.editor
    target = (
        editor.focus_widgets()[0]
        if isinstance(editor, SemanticStringEditor)
        else editor
    )
    target.setFocus()
    qt_app.processEvents()
    page = window.pages.currentWidget()
    page.verticalScrollBar().setValue(0)
    before = editor.value()
    writes: list[object] = []
    window.change_requested.connect(lambda *args: writes.append(args))
    event = QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(target, event)
    qt_app.processEvents()
    assert editor.value() == before
    assert not writes
    assert page.verticalScrollBar().value() > 0
    window.close()


def test_shared_wheel_guard_covers_all_qt_value_editor_families(
    qt_app: QApplication,
) -> None:
    scroll = QScrollArea()
    scroll.resize(320, 220)
    content = QWidget()
    content.setMinimumHeight(900)
    layout = QVBoxLayout(content)
    integer = QSpinBox()
    decimal = QDoubleSpinBox()
    choice = QComboBox()
    choice.addItems(("First", "Second", "Third"))
    slider = QSlider(Qt.Orientation.Horizontal)
    dial = QDial()
    controls = (integer, decimal, choice, slider, dial)
    integer.setValue(5)
    decimal.setValue(5.5)
    choice.setCurrentIndex(1)
    slider.setValue(50)
    dial.setValue(50)
    for control in controls:
        layout.addWidget(control)
    layout.addStretch(1)
    scroll.setWidget(content)
    scroll.setWidgetResizable(True)
    guard = SettingsWheelGuard(scroll)
    for control in controls:
        control.installEventFilter(guard)
    scroll.show()
    qt_app.processEvents()

    values = (
        integer.value,
        decimal.value,
        choice.currentIndex,
        slider.value,
        dial.value,
    )
    for control, value in zip(controls, values, strict=True):
        scroll.verticalScrollBar().setValue(0)
        before = value()
        event = QWheelEvent(
            QPointF(5, 5),
            QPointF(5, 5),
            QPoint(),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(control, event)
        qt_app.processEvents()
        assert value() == before
        assert scroll.verticalScrollBar().value() > 0
    scroll.close()


@pytest.mark.parametrize(
    "key", ["players.preferred", "players.ignored", "library.roots"]
)
def test_collection_add_cancel_duplicate_and_remove(
    qt_app: QApplication, tmp_path: Path, key: str
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    editor = window.rows[key].editor
    assert isinstance(editor, OrderedStringListEditor)
    changes: list[object] = []
    window.change_requested.connect(lambda *_args: changes.append(_args))
    editor.input.setText("未保存")
    editor.cancel_button.click()
    assert not changes
    assert editor.value() == ()
    editor.input.setText("   ")
    editor.add_button.click()
    assert not changes
    value = "/音楽" if key == "library.roots" else "音楽"
    editor.input.setText(value)
    editor.add_button.click()
    assert editor.value() == (value,)
    assert len(changes) == 1
    editor.input.setText(value)
    editor.add_button.click()
    assert len(changes) == 1
    assert "already" in editor.validation_label.text()
    editor.remove_button.click()
    assert editor.value() == ()
    assert len(changes) == 2
    window.close()


def test_collection_edit_ordering_discovered_players_and_overlap_are_explicit(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    preferred = window.rows["players.preferred"].editor
    ignored = window.rows["players.ignored"].editor
    roots = window.rows["library.roots"].editor
    assert isinstance(preferred, OrderedStringListEditor)
    assert isinstance(ignored, OrderedStringListEditor)
    assert isinstance(roots, OrderedStringListEditor)
    window.set_player_suggestions(("firefox", "plasma-browser-integration"))
    assert [
        preferred.suggestions.itemText(index)
        for index in range(preferred.suggestions.count())
    ] == ["firefox", "plasma-browser-integration"]

    changes: list[tuple[str, object]] = []
    window.change_requested.connect(lambda key, value: changes.append((key, value)))
    preferred.suggestions.activated.emit(0)
    assert preferred.input.text() == "firefox"
    preferred.cancel_button.click()
    assert not changes
    preferred.input.setText("Firefox")
    preferred.add_button.click()
    preferred.edit_button.click()
    preferred.input.setText("firefox")
    preferred.cancel_button.click()
    assert preferred.value() == ("Firefox",)
    assert len(changes) == 1
    preferred.edit_button.click()
    preferred.input.setText("Strawberry")
    preferred.add_button.click()
    assert preferred.value() == ("Strawberry",)
    assert len(changes) == 2
    assert ignored.up_button.isHidden()
    assert roots.down_button.isHidden()

    window.set_snapshot(
        validate_settings_values(
            {
                "players.preferred": ["firefox"],
                "players.ignored": ["Firefox"],
            },
            explicit_keys=frozenset({"players.preferred", "players.ignored"}),
        )
    )
    assert "ignored-player rules take precedence" in preferred.validation_label.text()
    assert "ignored-player rules take precedence" in ignored.validation_label.text()
    window.close()


def test_hierarchical_navigation_expands_collapses_and_selects_children(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    tree = window.categories
    assert [
        tree.topLevelItem(index).text() for index in range(tree.topLevelItemCount())
    ] == ["Functionality", "Appearance", "Workspace", "Advanced"]
    functionality = tree.topLevelItem(0)
    appearance = tree.topLevelItem(1)
    assert functionality.isExpanded()
    assert appearance.isExpanded()
    assert functionality.childCount() == 4
    assert appearance.childCount() == 7
    tree.setCurrentItem(functionality)
    tree.setFocus()
    QTest.keyClick(tree, Qt.Key.Key_Left)
    assert not functionality.isExpanded()
    QTest.keyClick(tree, Qt.Key.Key_Right)
    assert functionality.isExpanded()
    QTest.keyClick(tree, Qt.Key.Key_Right)
    assert tree.currentItem() is functionality.child(0)
    assert tree.currentItem().text() == "Players / MPRIS"
    window.close()


def test_global_search_ignores_expansion_and_restores_it_after_clear(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    tree = window.categories
    functionality = tree.topLevelItem(0)
    appearance = tree.topLevelItem(1)
    functionality.setExpanded(False)
    appearance.setExpanded(False)
    window.search.setText("progress track color")
    qt_app.processEvents()
    assert appearance.isExpanded()
    assert window.rows["appearance.progress.track_color"].isVisible()
    assert tree.currentItem().text() == "Progress"
    assert tree.topLevelItem(2).isHidden()
    assert tree.topLevelItem(3).isHidden()
    window.search.clear()
    qt_app.processEvents()
    assert not window.rows["appearance.progress.track_color"].isVisible()
    assert not functionality.isExpanded()
    assert not appearance.isExpanded()
    assert not tree.topLevelItem(2).isHidden()
    assert not tree.topLevelItem(3).isHidden()
    window.close()


def test_progressive_disclosure_and_appearance_quick_navigation(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    advanced = window.rows["appearance.typography.translation.weight"]
    common = window.rows["appearance.typography.original.weight"]
    window.categories.setCurrentItem(
        window._category_items[advanced.definition.category]
    )
    qt_app.processEvents()
    assert common.isVisible()
    assert not advanced.isVisible()
    toggle = window._advanced_toggles[advanced.definition.category]
    assert toggle.text() == "Advanced"
    assert toggle.arrowType() is Qt.ArrowType.RightArrow
    toggle.click()
    assert advanced.isVisible()
    assert toggle.arrowType() is Qt.ArrowType.DownArrow
    window.categories.setCurrentItem(
        window._category_items[
            next(
                category
                for category in window._category_items
                if category.value == "Presets & Defaults"
            )
        ]
    )
    window.quick_navigation[
        next(
            category
            for category in window.quick_navigation
            if category.value == "Progress"
        )
    ].click()
    assert window.categories.currentItem().text() == "Progress"
    window.close()


def test_font_preview_and_human_weight_update_before_commit(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    family = window.rows["appearance.typography.original.family"].editor
    size = window.rows["appearance.typography.original.size"].editor
    weight = window.rows["appearance.typography.original.weight"].editor
    italic = window.rows["appearance.typography.original.italic"].editor
    assert isinstance(family, SemanticStringEditor)
    assert isinstance(size, QSpinBox)
    assert isinstance(weight, FontWeightEditor)
    assert isinstance(italic, QCheckBox)
    assert [weight.combo.itemText(index) for index in range(weight.combo.count())] == [
        "Thin",
        "Extra Light",
        "Light",
        "Normal",
        "Medium",
        "Semi Bold",
        "Bold",
        "Extra Bold",
        "Black",
    ]
    changes: list[tuple[str, object]] = []
    window.change_requested.connect(lambda key, value: changes.append((key, value)))
    assert family.combo is not None
    family.combo.setEditText("Missing Test Font Ω")
    size.setValue(31)
    weight.combo.setCurrentIndex(weight.combo.findData(700))
    italic.setChecked(True)
    qt_app.processEvents()
    assert window.font_preview is not None
    assert window.font_preview.font().weight() == 700
    assert window.font_preview.font().italic()
    assert window.font_preview.font().pointSizeF() == pytest.approx(23.25)
    assert window.font_preview_status is not None
    assert "Requested: Missing Test Font Ω" in window.font_preview_status.text()
    assert "Rendered:" in window.font_preview_status.text()
    assert "Bold" in window.font_preview_status.text()
    # Font browsing and numeric drafts preview without saving; the checkbox is
    # a deliberate immediate toggle and continues to use canonical writes.
    assert changes == [("appearance.typography.original.italic", True)]
    line_edit = family.combo.lineEdit()
    assert line_edit is not None
    line_edit.editingFinished.emit()
    assert changes == [("appearance.typography.original.italic", True)]
    line_edit.returnPressed.emit()
    weight.combo.activated.emit(weight.combo.currentIndex())
    assert changes[-2:] == [
        ("appearance.typography.original.family", "Missing Test Font Ω"),
        ("appearance.typography.original.weight", 700),
    ]
    window.close()


def test_font_family_filters_by_unicode_casefold_without_writing_until_commit(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    family = window.rows["appearance.typography.original.family"].editor
    assert isinstance(family, SemanticStringEditor)
    assert family.combo is not None
    assert family._font_source is not None
    family._font_source.setStringList(
        ["Noto Sans", "Noto Serif", "Fira Sans", "Straße Ω"]
    )
    changes: list[tuple[str, object]] = []
    window.change_requested.connect(lambda key, value: changes.append((key, value)))
    line_edit = family.combo.lineEdit()
    assert line_edit is not None

    line_edit.setFocus()
    line_edit.setText("noto sa")
    line_edit.textEdited.emit("noto sa")
    assert family.filtered_values() == ("Noto Sans",)
    assert not changes
    line_edit.editingFinished.emit()
    assert not changes

    family._font_query_changed("STRASSE")
    assert family.filtered_values() == ("Straße Ω",)
    assert not changes
    family.combo.activated.emit(0)
    assert changes == [("appearance.typography.original.family", "Straße Ω")]
    window.close()


def test_library_folder_chooser_cancel_unicode_duplicate_and_overlap_feedback(
    qt_app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    editor = window.rows["library.roots"].editor
    assert isinstance(editor, OrderedStringListEditor)
    changes: list[tuple[str, object]] = []
    window.change_requested.connect(lambda key, value: changes.append((key, value)))
    selections = iter(
        (
            "",
            "/home/test/音楽",
            "/home/test/音楽",
            "/home/test/音楽/Live",
            "/home/test",
            "/mnt/Музыка",
        )
    )
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: next(selections),
    )

    assert editor.folder_button.text() == "Add folder…"
    assert not editor.folder_button.isHidden()
    assert editor.input_widget.isHidden()
    editor.folder_button.click()
    assert not changes
    editor.folder_button.click()
    assert editor.value() == ("/home/test/音楽",)
    editor.folder_button.click()
    assert "already in the library" in editor.validation_label.text()
    editor.folder_button.click()
    assert "already covered" in editor.validation_label.text()
    editor.folder_button.click()
    assert "already inside" in editor.validation_label.text()
    editor.folder_button.click()
    assert editor.value() == ("/home/test/音楽", "/mnt/Музыка")
    assert len(changes) == 2

    editor.manual_toggle.setChecked(True)
    assert not editor.input_widget.isHidden()
    editor.input.setText("relative/path")
    editor.add_button.click()
    assert "absolute folder path" in editor.validation_label.text()
    assert len(changes) == 2
    window.close()


def test_flatpak_folder_chooser_is_async_and_surfaces_portal_failures(
    qt_app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[object] = []

    class FakePortalRequest(QObject):
        finished = Signal(object, object)

        def __init__(self, parent: QObject) -> None:
            super().__init__(parent)
            self.started = False
            requests.append(self)

        def start(self, *, parent_window: str = "") -> None:
            self.started = True
            self.parent_window = parent_window

    monkeypatch.setattr(
        "konokashi.presentation.desktop.settings_window.is_flatpak_session",
        lambda: True,
    )
    monkeypatch.setattr(
        "konokashi.presentation.desktop.settings_window.PortalDirectoryRequest",
        FakePortalRequest,
    )
    monkeypatch.setattr(
        "konokashi.presentation.desktop.settings_window.portal_parent_identifier",
        lambda _window: "wayland:settings-test-parent",
    )
    window = _window(qt_app, tmp_path / "config.toml")
    editor = window.rows["library.roots"].editor
    assert isinstance(editor, OrderedStringListEditor)
    changes: list[tuple[str, object]] = []
    window.change_requested.connect(lambda key, value: changes.append((key, value)))

    editor.folder_button.click()
    first = requests[-1]
    assert isinstance(first, FakePortalRequest)
    assert first.started
    assert first.parent_window == "wayland:settings-test-parent"
    assert not editor.folder_button.isEnabled()
    assert editor.value() == ()
    first.finished.emit(None, None)
    assert editor.folder_button.isEnabled()
    assert changes == []

    editor.folder_button.click()
    second = requests[-1]
    assert isinstance(second, FakePortalRequest)
    second.finished.emit(None, DesktopPortalError("portal unavailable"))
    assert editor.validation_label.text() == "portal unavailable"
    assert editor.folder_button.isEnabled()
    assert changes == []

    editor.folder_button.click()
    third = requests[-1]
    assert isinstance(third, FakePortalRequest)
    editor.set_editing_enabled(False)
    third.finished.emit("/run/user/1000/doc/example/音楽", None)
    assert editor.value() == ("/run/user/1000/doc/example/音楽",)
    assert not editor.folder_button.isEnabled()
    assert changes == [
        ("library.roots", ("/run/user/1000/doc/example/音楽",)),
    ]
    window.close()


def test_missing_and_custom_font_weight_values_remain_projectable(
    qt_app: QApplication, tmp_path: Path
) -> None:
    values = {
        "appearance.typography.original.family": "不存在の書体",
        "appearance.typography.original.weight": 550,
    }
    # The canonical schema currently bounds exact advanced values, so preserve
    # a supported non-preset value through direct row projection.
    snapshot = validate_settings_values(values, explicit_keys=frozenset(values))
    window = _window(qt_app, tmp_path / "config.toml")
    window.set_snapshot(snapshot)
    family = window.rows["appearance.typography.original.family"].editor
    weight = window.rows["appearance.typography.original.weight"].editor
    assert isinstance(family, SemanticStringEditor)
    assert isinstance(weight, FontWeightEditor)
    assert family.value() == "不存在の書体"
    assert weight.value() == 550
    assert weight.combo.currentText() == "Custom (550)"
    assert window.font_preview_status is not None
    assert "Requested: 不存在の書体" in window.font_preview_status.text()
    window.close()


def test_motion_controls_explain_instant_smooth_and_speed_without_raw_ms(
    qt_app: QApplication, tmp_path: Path
) -> None:
    window = _window(qt_app, tmp_path / "config.toml")
    transition = window.rows["appearance.motion.smooth_scrolling"].editor
    speed = window.rows["appearance.motion.transition_ms"].editor
    assert isinstance(transition, LyricTransitionEditor)
    assert isinstance(speed, AnimationSpeedEditor)
    assert [transition.combo.itemText(index) for index in range(2)] == [
        "Instant",
        "Smooth",
    ]
    assert [speed.combo.itemText(index) for index in range(4)] == [
        "Slow",
        "Normal",
        "Fast",
        "Custom",
    ]
    assert speed.value() == 180
    speed.set_value(275)
    assert speed.combo.currentText() == "Custom"
    assert not speed.custom.isHidden()
    assert speed.value() == 275
    window.close()
