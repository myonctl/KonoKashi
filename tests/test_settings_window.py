"""Stage 12 schema-driven desktop settings widget regressions."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QSpinBox

from konokashi.application.settings import (
    SETTINGS_BY_KEY,
    SettingCategory,
    SettingOrigin,
    SettingsDiagnostic,
    SettingType,
    validate_settings_values,
)
from konokashi.presentation.desktop.settings_window import (
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
    ] == [category.value for category in SettingCategory]
    for key, row in window.rows.items():
        definition = SETTINGS_BY_KEY[key]
        assert row.definition is definition
        assert definition.title
        assert definition.description
        if definition.value_type is SettingType.BOOLEAN:
            assert isinstance(row.editor, QCheckBox)
        elif definition.value_type is SettingType.INTEGER:
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
    QTest.mouseClick(window.reset_appearance_button, Qt.MouseButton.LeftButton)

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
