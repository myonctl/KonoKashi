"""Schema-driven PySide6 frontend for canonical LyriFlux settings."""

from __future__ import annotations

from collections.abc import Iterable
from itertools import pairwise
from pathlib import Path
from typing import cast

from PySide6.QtCore import QSignalBlocker, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lyriflux.application.settings import (
    SETTINGS_SCHEMA,
    ReloadBehavior,
    ResolvedSetting,
    SettingCategory,
    SettingDefinition,
    SettingOrigin,
    SettingsDiagnostic,
    SettingsSnapshot,
    SettingType,
)


def _plain_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def _reload_text(behavior: ReloadBehavior) -> str:
    if behavior is ReloadBehavior.LIVE:
        return "Applies immediately"
    if behavior is ReloadBehavior.NEXT_OPERATION:
        return "Applies to the next operation"
    return "Requires restart"


class OrderedStringListEditor(QWidget):
    """Small keyboard-friendly editor for one ordered tuple of strings."""

    value_changed = Signal(object)

    def __init__(self, accessible_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.items = QListWidget()
        self.items.setAccessibleName(accessible_name)
        self.items.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        list_height = max(84, self.fontMetrics().lineSpacing() * 4 + 16)
        self.items.setMaximumHeight(list_height)
        self.items.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        layout.addWidget(self.items)

        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setAccessibleName(f"New item for {accessible_name}")
        self.input.setPlaceholderText("Add one value")
        self.add_button = QPushButton("Add")
        self.add_button.setAccessibleName(f"Add item to {accessible_name}")
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.add_button)
        layout.addLayout(input_row)

        actions = QHBoxLayout()
        self.remove_button = QPushButton("Remove")
        self.up_button = QPushButton("Move up")
        self.down_button = QPushButton("Move down")
        actions.addWidget(self.remove_button)
        actions.addWidget(self.up_button)
        actions.addWidget(self.down_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.add_button.clicked.connect(self._add)
        self.input.returnPressed.connect(self._add)
        self.remove_button.clicked.connect(self._remove)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.items.currentRowChanged.connect(self._update_actions)
        self._update_actions()

    def value(self) -> tuple[str, ...]:
        return tuple(
            self.items.item(index).text() for index in range(self.items.count())
        )

    def set_value(self, values: Iterable[str]) -> None:
        self.items.clear()
        self.items.addItems(list(values))
        if self.items.count():
            self.items.setCurrentRow(0)
        self.input.clear()
        self._update_actions()

    def set_editing_enabled(self, enabled: bool) -> None:
        self.items.setEnabled(enabled)
        self.input.setEnabled(enabled)
        self.add_button.setEnabled(enabled)
        self._update_actions(enabled)

    def _add(self) -> None:
        value = self.input.text().strip()
        if not value:
            return
        self.items.addItem(value)
        self.items.setCurrentRow(self.items.count() - 1)
        self.input.clear()
        self.value_changed.emit(self.value())

    def _remove(self) -> None:
        row = self.items.currentRow()
        if row < 0:
            return
        self.items.takeItem(row)
        if self.items.count():
            self.items.setCurrentRow(min(row, self.items.count() - 1))
        self.value_changed.emit(self.value())

    def _move(self, offset: int) -> None:
        row = self.items.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= self.items.count():
            return
        item = self.items.takeItem(row)
        self.items.insertItem(target, item)
        self.items.setCurrentRow(target)
        self.value_changed.emit(self.value())

    def _update_actions(self, editing_enabled: bool | None = None) -> None:
        if editing_enabled is None:
            editing_enabled = self.items.isEnabled()
        row = self.items.currentRow()
        count = self.items.count()
        self.remove_button.setEnabled(editing_enabled and row >= 0)
        self.up_button.setEnabled(editing_enabled and row > 0)
        self.down_button.setEnabled(editing_enabled and 0 <= row < count - 1)


class SettingRow(QFrame):
    """One schema entry, typed editor, metadata, and individual reset."""

    change_requested = Signal(str, object)
    reset_requested = Signal(str)

    def __init__(
        self, definition: SettingDefinition, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.definition = definition
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)

        title_row = QHBoxLayout()
        title = _plain_label(definition.title)
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        title_row.addWidget(title, 1)
        self.reset_button = QPushButton("Reset")
        self.reset_button.setAccessibleName(f"Reset {definition.title} to default")
        self.reset_button.clicked.connect(
            lambda: self.reset_requested.emit(self.definition.key)
        )
        title_row.addWidget(self.reset_button)
        layout.addLayout(title_row)

        description = _plain_label(definition.description)
        description.setWordWrap(True)
        description.setAccessibleName(f"Description for {definition.title}")
        layout.addWidget(description)

        self.editor: QCheckBox | QSpinBox | OrderedStringListEditor
        if definition.value_type is SettingType.BOOLEAN:
            checkbox = QCheckBox("Enabled")
            checkbox.setAccessibleName(definition.title)
            checkbox.setAccessibleDescription(definition.description)
            checkbox.toggled.connect(
                lambda checked: self.change_requested.emit(self.definition.key, checked)
            )
            self.editor = checkbox
        elif definition.value_type is SettingType.INTEGER:
            spin = QSpinBox()
            spin.setAccessibleName(definition.title)
            spin.setAccessibleDescription(definition.description)
            spin.setMinimum(
                definition.minimum if definition.minimum is not None else -1
            )
            spin.setMaximum(
                definition.maximum if definition.maximum is not None else 99
            )
            spin.editingFinished.connect(
                lambda: self.change_requested.emit(self.definition.key, spin.value())
            )
            self.editor = spin
        else:
            list_editor = OrderedStringListEditor(definition.title)
            list_editor.setAccessibleDescription(definition.description)
            list_editor.value_changed.connect(
                lambda value: self.change_requested.emit(self.definition.key, value)
            )
            self.editor = list_editor
        layout.addWidget(self.editor)

        metadata_row = QHBoxLayout()
        self.origin_label = _plain_label("Loading…")
        self.origin_label.setAccessibleName(f"Value status for {definition.title}")
        metadata_row.addWidget(self.origin_label)
        metadata_row.addStretch(1)
        key_label = _plain_label(definition.key)
        key_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        key_label.setToolTip(f"Canonical config and CLI key: {definition.key}")
        key_label.setAccessibleName(f"Canonical key {definition.key}")
        metadata_row.addWidget(key_label)
        layout.addLayout(metadata_row)

        self.reload_label = _plain_label(
            f"{_reload_text(definition.reload)} · {definition.scope.value}"
        )
        self.reload_label.setAccessibleName(
            f"Application timing for {definition.title}"
        )
        layout.addWidget(self.reload_label)
        self._resolved: ResolvedSetting | None = None

    def apply(self, resolved: ResolvedSetting) -> None:
        self._resolved = resolved
        if isinstance(self.editor, QCheckBox):
            with QSignalBlocker(self.editor):
                self.editor.setChecked(bool(resolved.value))
        elif isinstance(self.editor, QSpinBox):
            with QSignalBlocker(self.editor):
                self.editor.setValue(cast(int, resolved.value))
        else:
            self.editor.set_value(cast(tuple[str, ...], resolved.value))
        is_default_value = resolved.value == resolved.definition.default
        if resolved.origin is SettingOrigin.DEFAULT:
            status = "Default"
        elif is_default_value:
            status = f"Default value · {resolved.origin.value}"
        else:
            status = f"Customized · {resolved.origin.value}"
        self.origin_label.setText(status)
        self.origin_label.setToolTip(
            f"Default: {resolved.definition.default!r}; origin: {resolved.origin.value}"
        )
        self.reset_button.setEnabled(resolved.origin is not SettingOrigin.DEFAULT)
        self.set_editing_enabled(True)

    def set_editing_enabled(self, enabled: bool) -> None:
        if isinstance(self.editor, OrderedStringListEditor):
            self.editor.set_editing_enabled(enabled)
        else:
            self.editor.setEnabled(enabled)
        self.reset_button.setEnabled(
            enabled
            and self._resolved is not None
            and self._resolved.origin is not SettingOrigin.DEFAULT
        )

    def matches(self, query: str) -> bool:
        haystack = " ".join(
            (self.definition.title, self.definition.description, self.definition.key)
        ).casefold()
        return query.casefold() in haystack


class SettingsWindow(QDialog):
    """Reusable non-modal settings window driven by canonical snapshots."""

    change_requested = Signal(str, object)
    reset_requested = Signal(str)

    def __init__(
        self,
        config_path: Path,
        parent: QWidget | None = None,
        *,
        definitions: tuple[SettingDefinition, ...] = SETTINGS_SCHEMA,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("LyriFlux Settings")
        self.setModal(False)
        self.setMinimumSize(680, 480)
        self.resize(880, 640)
        self.setSizeGripEnabled(True)
        self._config_path = config_path
        self._pending_key: str | None = None
        self.rows: dict[str, SettingRow] = {}
        self._category_items: dict[SettingCategory, QListWidgetItem] = {}
        self._category_rows: dict[SettingCategory, list[SettingRow]] = {}

        root = QVBoxLayout(self)
        heading = _plain_label("Settings")
        heading_font = heading.font()
        heading_font.setPointSizeF(max(14.0, heading_font.pointSizeF() * 1.35))
        heading_font.setBold(True)
        heading.setFont(heading_font)
        root.addWidget(heading)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search settings")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search settings")
        self.search.textChanged.connect(self._filter)
        root.addWidget(self.search)

        self.error_banner = _plain_label()
        self.error_banner.setWordWrap(True)
        self.error_banner.setAccessibleName("Configuration status")
        self.error_banner.setVisible(False)
        root.addWidget(self.error_banner)

        content = QHBoxLayout()
        self.categories = QListWidget()
        self.categories.setAccessibleName("Settings categories")
        self.categories.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        self.pages = QStackedWidget()
        self.pages.setAccessibleName("Settings category contents")
        content.addWidget(self.categories)
        content.addWidget(self.pages, 1)
        root.addLayout(content, 1)

        for category in SettingCategory:
            category_definitions = tuple(
                item for item in definitions if item.category is category
            )
            if not category_definitions:
                continue
            item = QListWidgetItem(category.value)
            item.setData(Qt.ItemDataRole.UserRole, category)
            self.categories.addItem(item)
            self._category_items[category] = item
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_heading = _plain_label(category.value)
            page_font = page_heading.font()
            page_font.setBold(True)
            page_heading.setFont(page_font)
            page_layout.addWidget(page_heading)
            rows: list[SettingRow] = []
            for definition in category_definitions:
                row = SettingRow(definition)
                row.change_requested.connect(self.change_requested)
                row.reset_requested.connect(self.reset_requested)
                page_layout.addWidget(row)
                self.rows[definition.key] = row
                rows.append(row)
            page_layout.addStretch(1)
            self._category_rows[category] = rows
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self.pages.addWidget(scroll)

        self.categories.currentRowChanged.connect(self.pages.setCurrentIndex)
        if self.categories.count():
            self.categories.setCurrentRow(0)

        self.no_results = _plain_label("No settings match this search.")
        self.no_results.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_results.setAccessibleName("No matching settings")
        self.no_results.setVisible(False)
        root.addWidget(self.no_results)

        footer = QHBoxLayout()
        path_label = _plain_label("Configuration:")
        footer.addWidget(path_label)
        self.path_display = QLineEdit(str(config_path))
        self.path_display.setReadOnly(True)
        self.path_display.setAccessibleName("Canonical configuration path")
        footer.addWidget(self.path_display, 1)
        self.copy_path_button = QPushButton("Copy path")
        self.copy_path_button.clicked.connect(self._copy_path)
        self.open_location_button = QPushButton("Open folder")
        self.open_location_button.clicked.connect(self._open_location)
        footer.addWidget(self.copy_path_button)
        footer.addWidget(self.open_location_button)
        root.addLayout(footer)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        root.addWidget(buttons)

        focus_order: list[QWidget] = [self.search, self.categories]
        for definition in definitions:
            row = self.rows[definition.key]
            if isinstance(row.editor, OrderedStringListEditor):
                focus_order.extend(
                    (
                        row.editor.items,
                        row.editor.input,
                        row.editor.add_button,
                        row.editor.remove_button,
                        row.editor.up_button,
                        row.editor.down_button,
                    )
                )
            else:
                focus_order.append(row.editor)
            focus_order.append(row.reset_button)
        focus_order.extend(
            (
                self.path_display,
                self.copy_path_button,
                self.open_location_button,
                buttons.button(QDialogButtonBox.StandardButton.Close),
            )
        )
        for current, following in pairwise(focus_order):
            QWidget.setTabOrder(current, following)

        self.set_loading()

    def set_loading(self) -> None:
        self._set_banner("Loading canonical settings…", error=False)
        self._set_all_editing_enabled(False)

    def set_snapshot(self, snapshot: SettingsSnapshot) -> None:
        for resolved in snapshot.values:
            row = self.rows.get(resolved.definition.key)
            if row is not None:
                row.apply(resolved)
        self._pending_key = None
        self._set_all_editing_enabled(True)

    def set_diagnostics(
        self, diagnostics: tuple[SettingsDiagnostic, ...], *, context: str = ""
    ) -> None:
        if diagnostics:
            message = "\n".join(item.render() for item in diagnostics)
            if context:
                message = f"{context}\n{message}"
            self._set_banner(message, error=True)
        else:
            self.error_banner.clear()
            self.error_banner.setVisible(False)

    def set_operation_error(self, message: str) -> None:
        self._pending_key = None
        self._set_all_editing_enabled(True)
        self._set_banner(message, error=True)

    def mark_pending(self, key: str) -> bool:
        if self._pending_key is not None:
            return False
        self._pending_key = key
        self._set_all_editing_enabled(False)
        self._set_banner(f"Saving {key}…", error=False)
        return True

    def _set_banner(self, message: str, *, error: bool) -> None:
        self.error_banner.setText(message)
        self.error_banner.setProperty("error", error)
        self.error_banner.setVisible(True)

    def _set_all_editing_enabled(self, enabled: bool) -> None:
        for row in self.rows.values():
            row.set_editing_enabled(enabled)

    def _filter(self, query: str) -> None:
        visible_categories: list[int] = []
        for index in range(self.categories.count()):
            item = self.categories.item(index)
            category = cast(SettingCategory, item.data(Qt.ItemDataRole.UserRole))
            rows = self._category_rows[category]
            visible = False
            for row in rows:
                matches = row.matches(query)
                row.setVisible(matches)
                visible = visible or matches
            item.setHidden(not visible)
            if visible:
                visible_categories.append(index)
        self.no_results.setVisible(not visible_categories)
        current = self.categories.currentItem()
        if visible_categories and (current is None or current.isHidden()):
            self.categories.setCurrentRow(visible_categories[0])

    def _copy_path(self) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(str(self._config_path))

    def _open_location(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._config_path.parent)))
