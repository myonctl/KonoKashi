"""Schema-driven PySide6 frontend for canonical KonoKashi settings."""

from __future__ import annotations

from collections.abc import Iterable
from itertools import pairwise
from pathlib import Path
from typing import cast

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QFontInfo,
    QKeyEvent,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from konokashi.application.appearance import default_appearance_profile
from konokashi.application.settings import (
    SETTINGS_SCHEMA,
    ReloadBehavior,
    ResolvedSetting,
    SettingCategory,
    SettingDefinition,
    SettingOrigin,
    SettingsDiagnostic,
    SettingsSnapshot,
    SettingStringFormat,
    SettingType,
)
from konokashi.presentation.desktop.settings_input import SettingsWheelGuard


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


_ADVANCED_SETTING_KEYS = frozenset(
    {
        "library.metadata_workers",
        "appearance.typography.original.size",
        "appearance.typography.romanization.family",
        "appearance.typography.romanization.size",
        "appearance.typography.romanization.weight",
        "appearance.typography.romanization.italic",
        "appearance.typography.translation.family",
        "appearance.typography.translation.size",
        "appearance.typography.translation.weight",
        "appearance.typography.translation.italic",
        "appearance.typography.metadata.family",
        "appearance.typography.metadata.size",
        "appearance.typography.metadata.weight",
        "appearance.typography.status.family",
        "appearance.typography.status.size",
        "appearance.typography.status.weight",
        "appearance.typography.active_size_percent",
        "appearance.typography.inactive_size_percent",
        "appearance.colors.inactive_lyric",
        "appearance.colors.romanization",
        "appearance.colors.translation",
        "appearance.colors.metadata_primary",
        "appearance.colors.metadata_secondary",
        "appearance.colors.foreground",
        "appearance.colors.status",
        "appearance.colors.muted",
        "appearance.colors.selection",
        "appearance.opacity.content",
        "appearance.opacity.inactive_line",
        "appearance.opacity.metadata",
        "appearance.opacity.secondary_representation",
        "appearance.progress.track_color",
        "appearance.progress.opacity",
        "appearance.progress.corner_radius",
        "appearance.spacing.progress",
        "appearance.spacing.lyric_padding",
        "appearance.spacing.representation",
        "appearance.spacing.metadata",
        "appearance.motion.emphasis_transition_ms",
    }
)


class NavigationItem(QTreeWidgetItem):
    """Tree item whose primary text remains convenient to inspect."""

    def text(self, column: int = 0) -> str:
        return super().text(column)


class SettingsNavigation(QTreeWidget):
    """Native expandable navigation with leaf helpers for focused tests/tools."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setItemsExpandable(True)
        self.setExpandsOnDoubleClick(True)
        self.setIndentation(18)
        self._category_items: list[QTreeWidgetItem] = []

    def register_category(self, item: QTreeWidgetItem) -> None:
        self._category_items.append(item)

    def count(self) -> int:
        return len(self._category_items)

    def item(self, index: int) -> QTreeWidgetItem:
        return self._category_items[index]

    def setCurrentRow(self, index: int) -> None:
        self.setCurrentItem(self.item(index))

    def currentRow(self) -> int:
        current = self.currentItem()
        return self.row(current) if current is not None else -1

    def row(self, item: QTreeWidgetItem) -> int:
        try:
            return self._category_items.index(item)
        except ValueError:
            return -1


class OrderedStringListEditor(QWidget):
    """Keyboard-friendly editor for one canonical string collection."""

    value_changed = Signal(object)

    def __init__(
        self,
        accessible_name: str,
        *,
        ordered: bool,
        player_identities: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ordered = ordered
        self._editing_row: int | None = None
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

        self.suggestions = QComboBox()
        self.suggestions.setAccessibleName(
            f"Discovered stable players for {accessible_name}"
        )
        self.suggestions.setPlaceholderText("Add a discovered player…")
        self.suggestions.activated.connect(self._suggestion_selected)
        self.suggestions.setVisible(player_identities)
        layout.addWidget(self.suggestions)

        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setAccessibleName(f"New item for {accessible_name}")
        self.input.setPlaceholderText(
            "Enter a stable player identity"
            if player_identities
            else "Enter an absolute folder path"
        )
        self.add_button = QPushButton("Add")
        self.add_button.setAccessibleName(f"Add item to {accessible_name}")
        self.cancel_button = QPushButton("Cancel entry")
        self.cancel_button.clicked.connect(self._cancel_entry)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.add_button)
        input_row.addWidget(self.cancel_button)
        layout.addLayout(input_row)

        actions = QHBoxLayout()
        self.remove_button = QPushButton("Remove")
        self.edit_button = QPushButton("Edit")
        self.up_button = QPushButton("Move up")
        self.down_button = QPushButton("Move down")
        actions.addWidget(self.remove_button)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.up_button)
        actions.addWidget(self.down_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.validation_label = _plain_label()
        self.validation_label.setWordWrap(True)
        layout.addWidget(self.validation_label)
        self.input.textChanged.connect(lambda: self.validation_label.clear())

        self.add_button.clicked.connect(self._add)
        self.input.returnPressed.connect(self._add)
        self.remove_button.clicked.connect(self._remove)
        self.edit_button.clicked.connect(self._edit)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.items.currentRowChanged.connect(lambda _row: self._update_actions())
        self._update_actions()

    def set_suggestions(self, values: Iterable[str]) -> None:
        current = self.suggestions.currentText()
        with QSignalBlocker(self.suggestions):
            self.suggestions.clear()
            self.suggestions.setPlaceholderText("Add a discovered player…")
            self.suggestions.addItems(tuple(values))
            if current:
                self.suggestions.setCurrentText(current)

    def value(self) -> tuple[str, ...]:
        return tuple(
            self.items.item(index).text() for index in range(self.items.count())
        )

    def set_value(self, values: Iterable[str]) -> None:
        current_item = self.items.currentItem()
        current_value = None if current_item is None else current_item.text()
        self.items.clear()
        self.items.addItems(list(values))
        if self.items.count():
            matching = self.items.findItems(
                current_value or "", Qt.MatchFlag.MatchExactly
            )
            self.items.setCurrentItem(matching[0] if matching else self.items.item(0))
        self.input.clear()
        self._editing_row = None
        self.add_button.setText("Add")
        self._update_actions()

    def set_editing_enabled(self, enabled: bool) -> None:
        self.items.setEnabled(enabled)
        self.input.setEnabled(enabled)
        self.add_button.setEnabled(enabled)
        self.cancel_button.setEnabled(enabled)
        self.suggestions.setEnabled(enabled)
        self._update_actions(enabled)

    def _add(self) -> None:
        value = self.input.text().strip()
        if not value:
            self.validation_label.setText("Enter a value before adding it.")
            return
        duplicate_rows = {
            index
            for index, existing in enumerate(self.value())
            if existing.casefold() == value.casefold()
        }
        if duplicate_rows - (
            {self._editing_row} if self._editing_row is not None else set()
        ):
            self.validation_label.setText("This entry is already in the list.")
            return
        if self._editing_row is None:
            self.items.addItem(value)
            self.items.setCurrentRow(self.items.count() - 1)
        else:
            item = self.items.item(self._editing_row)
            item.setText(value)
            self.items.setCurrentRow(self._editing_row)
        self._cancel_entry()
        self.value_changed.emit(self.value())

    def _edit(self) -> None:
        row = self.items.currentRow()
        if row < 0:
            return
        self._editing_row = row
        self.input.setText(self.items.item(row).text())
        self.input.selectAll()
        self.input.setFocus()
        self.add_button.setText("Save")

    def _cancel_entry(self) -> None:
        self.input.clear()
        self._editing_row = None
        self.add_button.setText("Add")

    def _suggestion_selected(self, index: int) -> None:
        value = self.suggestions.itemText(index)
        if value:
            self.input.setText(value)
            self.input.setFocus()

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
        self.edit_button.setEnabled(editing_enabled and row >= 0)
        self.up_button.setVisible(self._ordered)
        self.down_button.setVisible(self._ordered)
        self.up_button.setEnabled(editing_enabled and self._ordered and row > 0)
        self.down_button.setEnabled(
            editing_enabled and self._ordered and 0 <= row < count - 1
        )


_FONT_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("Thin", 100),
    ("Extra Light", 200),
    ("Light", 300),
    ("Normal", 400),
    ("Medium", 500),
    ("Semi Bold", 600),
    ("Bold", 700),
    ("Extra Bold", 800),
    ("Black", 900),
)


class FontWeightEditor(QWidget):
    """Human-readable font weights projected to the canonical integer value."""

    value_changed = Signal(int)
    preview_changed = Signal(int)

    def __init__(self, definition: SettingDefinition, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.setAccessibleName(definition.title)
        self.combo.setAccessibleDescription(definition.description)
        for label, value in _FONT_WEIGHTS:
            self.combo.addItem(label, value)
        self.combo.currentIndexChanged.connect(
            lambda: self.preview_changed.emit(self.value())
        )
        self.combo.activated.connect(lambda: self.value_changed.emit(self.value()))
        layout.addWidget(self.combo, 1)

    def value(self) -> int:
        return int(self.combo.currentData())

    def set_value(self, value: int) -> None:
        with QSignalBlocker(self.combo):
            index = self.combo.findData(value)
            if index < 0:
                self.combo.addItem(f"Custom ({value})", value)
                index = self.combo.count() - 1
            self.combo.setCurrentIndex(index)

    def set_editing_enabled(self, enabled: bool) -> None:
        self.combo.setEnabled(enabled)

    def focus_widgets(self) -> tuple[QWidget, ...]:
        return (self.combo,)


class LyricTransitionEditor(QWidget):
    """Present the canonical motion boolean as an experiential choice."""

    value_changed = Signal(bool)

    def __init__(self, definition: SettingDefinition, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.setAccessibleName(definition.title)
        self.combo.setAccessibleDescription(definition.description)
        self.combo.addItem("Instant", False)
        self.combo.addItem("Smooth", True)
        self.combo.activated.connect(lambda: self.value_changed.emit(self.value()))
        layout.addWidget(self.combo, 1)

    def value(self) -> bool:
        return bool(self.combo.currentData())

    def set_value(self, value: bool) -> None:
        with QSignalBlocker(self.combo):
            self.combo.setCurrentIndex(self.combo.findData(value))

    def set_editing_enabled(self, enabled: bool) -> None:
        self.combo.setEnabled(enabled)

    def focus_widgets(self) -> tuple[QWidget, ...]:
        return (self.combo,)


class AnimationSpeedEditor(QWidget):
    """Map approachable speed choices onto the canonical duration integer."""

    value_changed = Signal(int)
    _PRESETS = (("Slow", 320), ("Normal", 180), ("Fast", 120))

    def __init__(self, definition: SettingDefinition, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.setAccessibleName(definition.title)
        self.combo.setAccessibleDescription(definition.description)
        for label, duration in self._PRESETS:
            self.combo.addItem(label, duration)
        self.combo.addItem("Custom", None)
        self.custom = QSpinBox()
        self.custom.setAccessibleName("Custom lyric transition duration")
        self.custom.setMinimum(definition.minimum or 0)
        self.custom.setMaximum(definition.maximum or 1000)
        self.custom.setSuffix(" ms")
        self.custom.hide()
        self.combo.currentIndexChanged.connect(self._choice_changed)
        self.combo.activated.connect(self._commit_choice)
        self.custom.editingFinished.connect(
            lambda: self.value_changed.emit(self.custom.value())
        )
        layout.addWidget(self.combo, 1)
        layout.addWidget(self.custom, 1)

    def value(self) -> int:
        selected = self.combo.currentData()
        return self.custom.value() if selected is None else int(selected)

    def set_value(self, value: int) -> None:
        with QSignalBlocker(self.combo), QSignalBlocker(self.custom):
            index = self.combo.findData(value)
            if index < 0:
                index = self.combo.count() - 1
                self.custom.setValue(value)
            self.combo.setCurrentIndex(index)
            self.custom.setVisible(self.combo.currentData() is None)

    def set_editing_enabled(self, enabled: bool) -> None:
        self.combo.setEnabled(enabled)
        self.custom.setEnabled(enabled)

    def focus_widgets(self) -> tuple[QWidget, ...]:
        return (self.combo, self.custom)

    def _choice_changed(self) -> None:
        self.custom.setVisible(self.combo.currentData() is None)

    def _commit_choice(self) -> None:
        if self.combo.currentData() is not None:
            self.value_changed.emit(self.value())


class SemanticStringEditor(QWidget):
    """Choice, font-family, and validated text editor over one string value."""

    value_changed = Signal(str)
    preview_changed = Signal(str)

    def __init__(
        self, definition: SettingDefinition, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.definition = definition
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo: QComboBox | None = None
        self.input: QLineEdit | None = None
        self.color_button: QPushButton | None = None
        if (
            definition.choices
            or definition.string_format is SettingStringFormat.FONT_FAMILY
        ):
            combo = QComboBox()
            combo.setAccessibleName(definition.title)
            combo.setAccessibleDescription(definition.description)
            if definition.choices:
                for value in definition.choices:
                    combo.addItem(value.replace("-", " ").title(), value)
            else:
                combo.addItems(tuple(QFontDatabase.families()))
            combo.setEditable(not definition.choices)
            line_edit = combo.lineEdit()
            if line_edit is not None:
                line_edit.setPlaceholderText("System default")
                line_edit.editingFinished.connect(self._emit_combo)
            else:
                combo.currentIndexChanged.connect(
                    lambda: self.value_changed.emit(self.value())
                )
            self.combo = combo
            if definition.string_format is SettingStringFormat.FONT_FAMILY:
                combo.currentTextChanged.connect(self.preview_changed)
                combo.activated.connect(self._emit_combo)
            layout.addWidget(combo, 1)
        else:
            line = QLineEdit()
            line.setAccessibleName(definition.title)
            line.setAccessibleDescription(definition.description)
            line.editingFinished.connect(lambda: self.value_changed.emit(line.text()))
            self.input = line
            layout.addWidget(line, 1)
            if definition.string_format is SettingStringFormat.COLOR:
                button = QPushButton("Choose…")
                button.setAccessibleName(f"Choose {definition.title}")
                button.clicked.connect(self._choose_color)
                self.color_button = button
                layout.addWidget(button)

    def value(self) -> str:
        if self.combo is not None:
            if self.definition.choices:
                return str(self.combo.currentData())
            return self.combo.currentText()
        assert self.input is not None
        return self.input.text()

    def set_value(self, value: str) -> None:
        if self.combo is not None:
            with QSignalBlocker(self.combo):
                index = (
                    self.combo.findData(value)
                    if self.definition.choices
                    else self.combo.findText(value)
                )
                if index >= 0:
                    self.combo.setCurrentIndex(index)
                elif self.combo.isEditable():
                    self.combo.setEditText(value)
            return
        assert self.input is not None
        with QSignalBlocker(self.input):
            self.input.setText(value)

    def set_editing_enabled(self, enabled: bool) -> None:
        if self.combo is not None:
            self.combo.setEnabled(enabled)
        if self.input is not None:
            self.input.setEnabled(enabled)
        if self.color_button is not None:
            self.color_button.setEnabled(enabled)

    def focus_widgets(self) -> tuple[QWidget, ...]:
        widgets: list[QWidget] = []
        if self.combo is not None:
            widgets.append(self.combo)
        if self.input is not None:
            widgets.append(self.input)
        if self.color_button is not None:
            widgets.append(self.color_button)
        return tuple(widgets)

    def _emit_combo(self) -> None:
        self.value_changed.emit(self.value())

    def _choose_color(self) -> None:
        assert self.input is not None
        initial = QColor(self.input.text())
        fallback = QColor(default_appearance_profile().colors.accent)
        dialog = QColorDialog(initial if initial.isValid() else fallback, self)
        dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, True)
        if dialog.exec() != QColorDialog.DialogCode.Accepted:
            return
        color = dialog.selectedColor()
        value = color.name(QColor.NameFormat.HexArgb)
        # QColor emits #AARRGGBB while canonical TOML uses #RRGGBBAA.
        canonical = f"#{value[3:]}{value[1:3]}".upper()
        self.input.setText(canonical)
        self.value_changed.emit(canonical)


class SettingRow(QFrame):
    """One schema entry, typed editor, metadata, and individual reset."""

    change_requested = Signal(str, object)
    reset_requested = Signal(str)

    def __init__(
        self, definition: SettingDefinition, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.definition = definition
        self.setFrameShape(QFrame.Shape.NoFrame)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title = _plain_label(definition.title)
        title.setWordWrap(True)
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

        self.editor: (
            QCheckBox
            | QSpinBox
            | OrderedStringListEditor
            | SemanticStringEditor
            | FontWeightEditor
            | LyricTransitionEditor
            | AnimationSpeedEditor
        )
        if definition.value_type is SettingType.BOOLEAN:
            if definition.key == "appearance.motion.smooth_scrolling":
                transition = LyricTransitionEditor(definition)
                transition.value_changed.connect(
                    lambda value: self.change_requested.emit(self.definition.key, value)
                )
                self.editor = transition
            else:
                checkbox = QCheckBox("Enabled")
                checkbox.setAccessibleName(definition.title)
                checkbox.setAccessibleDescription(definition.description)
                checkbox.toggled.connect(
                    lambda checked: self.change_requested.emit(
                        self.definition.key, checked
                    )
                )
                self.editor = checkbox
        elif definition.value_type is SettingType.INTEGER:
            is_font_weight = definition.key.startswith(
                "appearance.typography."
            ) and definition.key.endswith(".weight")
            if definition.key == "appearance.motion.transition_ms":
                speed = AnimationSpeedEditor(definition)
                speed.value_changed.connect(
                    lambda value: self.change_requested.emit(self.definition.key, value)
                )
                self.editor = speed
            elif is_font_weight:
                weight = FontWeightEditor(definition)
                weight.value_changed.connect(
                    lambda value: self.change_requested.emit(self.definition.key, value)
                )
                self.editor = weight
            else:
                spin = QSpinBox()
                spin.setAccessibleName(definition.title)
                spin.setAccessibleDescription(definition.description)
                if definition.key == "appearance.typography.lyric_scale_percent":
                    spin.setSuffix("%")
                    spin.setSingleStep(5)
                spin.setMinimum(
                    definition.minimum if definition.minimum is not None else -1
                )
                spin.setMaximum(
                    definition.maximum if definition.maximum is not None else 99
                )
                spin.editingFinished.connect(
                    lambda: self.change_requested.emit(
                        self.definition.key, spin.value()
                    )
                )
                self.editor = spin
        elif definition.value_type is SettingType.STRING:
            string_editor = SemanticStringEditor(definition)
            string_editor.value_changed.connect(
                lambda value: self.change_requested.emit(self.definition.key, value)
            )
            self.editor = string_editor
        else:
            list_editor = OrderedStringListEditor(
                definition.title,
                ordered=definition.key == "players.preferred",
                player_identities=definition.key.startswith("players."),
            )
            list_editor.setAccessibleDescription(definition.description)
            list_editor.value_changed.connect(
                lambda value: self.change_requested.emit(self.definition.key, value)
            )
            self.editor = list_editor
        if isinstance(self.editor, OrderedStringListEditor):
            # Collections need the full page width for the list and actions.
            # They cannot share the compact scalar editor column.
            layout.addWidget(self.editor)
        else:
            self.editor.setMinimumWidth(180)
            self.editor.setMaximumWidth(360)
            title_row.insertWidget(1, self.editor, 1)

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
        key_label.hide()
        self.setToolTip(
            f"{definition.key}\n{_reload_text(definition.reload)} · "
            f"{definition.scope.value}"
        )
        layout.addLayout(metadata_row)

        self.reload_label = _plain_label(
            f"{_reload_text(definition.reload)} · {definition.scope.value}"
        )
        self.reload_label.setAccessibleName(
            f"Application timing for {definition.title}"
        )
        layout.addWidget(self.reload_label)
        self.reload_label.hide()
        self._resolved: ResolvedSetting | None = None

    def apply(self, resolved: ResolvedSetting, *, force: bool = False) -> None:
        unchanged = (
            self._resolved is not None and self._resolved.value == resolved.value
        )
        self._resolved = resolved
        if unchanged and not force:
            pass  # Preserve drafts, selection, and list position on unrelated updates.
        elif isinstance(self.editor, QCheckBox):
            with QSignalBlocker(self.editor):
                self.editor.setChecked(bool(resolved.value))
        elif isinstance(self.editor, QSpinBox):
            with QSignalBlocker(self.editor):
                self.editor.setValue(cast(int, resolved.value))
        elif isinstance(self.editor, FontWeightEditor):
            self.editor.set_value(cast(int, resolved.value))
        elif isinstance(self.editor, LyricTransitionEditor):
            self.editor.set_value(cast(bool, resolved.value))
        elif isinstance(self.editor, AnimationSpeedEditor):
            self.editor.set_value(cast(int, resolved.value))
        elif isinstance(self.editor, SemanticStringEditor):
            self.editor.set_value(cast(str, resolved.value))
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
        self.set_editing_enabled(True)

    def set_editing_enabled(self, enabled: bool) -> None:
        if isinstance(
            self.editor,
            (
                OrderedStringListEditor,
                SemanticStringEditor,
                FontWeightEditor,
                LyricTransitionEditor,
                AnimationSpeedEditor,
            ),
        ):
            self.editor.set_editing_enabled(enabled)
        else:
            self.editor.setEnabled(enabled)
        self.reset_button.setEnabled(
            enabled
            and self._resolved is not None
            and (
                self._resolved.origin is not SettingOrigin.DEFAULT
                or self.reset_button.hasFocus()
            )
        )

    def matches(self, query: str) -> bool:
        haystack = " ".join(
            (
                self.definition.title,
                self.definition.description,
                self.definition.key,
                self.definition.category.value,
                self.definition.section,
            )
        ).casefold()
        return query.casefold() in haystack


class SettingsWindow(QDialog):
    """Reusable non-modal settings window driven by canonical snapshots."""

    change_requested = Signal(str, object)
    reset_requested = Signal(str)
    reset_appearance_requested = Signal()

    def __init__(
        self,
        config_path: Path,
        parent: QWidget | None = None,
        *,
        definitions: tuple[SettingDefinition, ...] = SETTINGS_SCHEMA,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("KonoKashi Settings")
        self.setPalette(QApplication.palette())
        self.setAutoFillBackground(True)
        self.setModal(False)
        self.setMinimumSize(760, 520)
        self.resize(1040, 760)
        self.setSizeGripEnabled(True)
        self._config_path = config_path
        self._pending_key: str | None = None
        self.rows: dict[str, SettingRow] = {}
        self._category_items: dict[SettingCategory, QTreeWidgetItem] = {}
        self._navigation_parents: dict[str, QTreeWidgetItem] = {}
        self._expansion_before_search: dict[str, bool] | None = None
        self._category_rows: dict[SettingCategory, list[SettingRow]] = {}
        self._advanced_rows: dict[SettingCategory, list[SettingRow]] = {}
        self._advanced_toggles: dict[SettingCategory, QToolButton] = {}
        self._sections: list[tuple[QWidget, list[SettingRow]]] = []
        self.font_preview: QLabel | None = None
        self.font_preview_status: QLabel | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)
        heading_row = QHBoxLayout()
        heading = _plain_label("Settings")
        heading_font = heading.font()
        heading_font.setPointSizeF(max(14.0, heading_font.pointSizeF() * 1.35))
        heading_font.setBold(True)
        heading.setFont(heading_font)
        heading_row.addWidget(heading, 1)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search settings…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search settings")
        self.search.textChanged.connect(self._filter)
        self.search.setMinimumWidth(300)
        heading_row.addWidget(self.search, 2)
        root.addLayout(heading_row)
        self.find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.find_shortcut.activated.connect(self.search.setFocus)

        self.error_banner = _plain_label()
        self.error_banner.setWordWrap(True)
        self.error_banner.setAccessibleName("Configuration status")
        self.error_banner.setVisible(False)
        banner_policy = self.error_banner.sizePolicy()
        banner_policy.setRetainSizeWhenHidden(True)
        self.error_banner.setSizePolicy(banner_policy)
        self.error_banner.setMinimumHeight(self.fontMetrics().lineSpacing() * 2)
        root.addWidget(self.error_banner)

        content = QHBoxLayout()
        self.categories = SettingsNavigation()
        self.categories.setAccessibleName("Settings categories")
        self.categories.setMinimumWidth(140)
        self.categories.setMaximumWidth(180)
        self.categories.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        self.pages = QStackedWidget()
        self.pages.setAccessibleName("Settings category contents")
        content.addWidget(self.categories)
        content.addWidget(self.pages, 1)
        root.addLayout(content, 1)

        navigation_groups: tuple[tuple[str, tuple[SettingCategory, ...]], ...] = (
            (
                "Functionality",
                (
                    SettingCategory.PLAYERS,
                    SettingCategory.LYRICS,
                    SettingCategory.DESKTOP,
                    SettingCategory.LIBRARY,
                ),
            ),
            (
                "Appearance",
                (
                    SettingCategory.APPEARANCE,
                    SettingCategory.TYPOGRAPHY,
                    SettingCategory.COLORS,
                    SettingCategory.PROGRESS,
                    SettingCategory.VISIBILITY,
                    SettingCategory.LAYOUT,
                    SettingCategory.MOTION,
                ),
            ),
        )
        ordered_categories = tuple(
            category
            for _group, categories in navigation_groups
            for category in categories
        )
        for group_name, _categories in navigation_groups:
            parent_item = NavigationItem((group_name,))
            parent_font = parent_item.font(0)
            parent_font.setBold(True)
            parent_item.setFont(0, parent_font)
            self.categories.addTopLevelItem(parent_item)
            self._navigation_parents[group_name] = parent_item

        for category in ordered_categories:
            category_definitions = tuple(
                item for item in definitions if item.category is category
            )
            if not category_definitions:
                continue
            parent_name = next(
                name for name, categories in navigation_groups if category in categories
            )
            item = NavigationItem((category.value,))
            item.setData(0, Qt.ItemDataRole.UserRole, category)
            self._navigation_parents[parent_name].addChild(item)
            self.categories.register_category(item)
            self._category_items[category] = item
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(16, 4, 16, 16)
            page_layout.setSpacing(8)
            page_heading = _plain_label(category.value)
            page_font = page_heading.font()
            page_font.setBold(True)
            page_heading.setFont(page_font)
            page_layout.addWidget(page_heading)
            if category is SettingCategory.TYPOGRAPHY:
                preview = QFrame()
                preview.setFrameShape(QFrame.Shape.StyledPanel)
                preview_layout = QVBoxLayout(preview)
                preview_title = _plain_label("Original lyric preview")
                preview_title_font = preview_title.font()
                preview_title_font.setBold(True)
                preview_title.setFont(preview_title_font)
                preview_layout.addWidget(preview_title)
                self.font_preview = _plain_label(
                    "KonoKashi — Lyrics\nこの歌詞 · 歌詞 · 가사"
                )
                self.font_preview.setWordWrap(True)
                self.font_preview.setAccessibleName("Selected lyric font preview")
                preview_layout.addWidget(self.font_preview)
                self.font_preview_status = _plain_label()
                self.font_preview_status.setWordWrap(True)
                preview_layout.addWidget(self.font_preview_status)
                page_layout.addWidget(preview)
            if any(
                definition.key in _ADVANCED_SETTING_KEYS
                for definition in category_definitions
            ):
                common_heading = _plain_label("Common")
                common_font = common_heading.font()
                common_font.setBold(True)
                common_heading.setFont(common_font)
                page_layout.addWidget(common_heading)
            if category is SettingCategory.APPEARANCE:
                introduction = _plain_label(
                    "Start with a preset, lyric scale, or one of the focused "
                    "appearance sections."
                )
                introduction.setWordWrap(True)
                page_layout.addWidget(introduction)
                quick_label = _plain_label("Quick appearance")
                quick_font = quick_label.font()
                quick_font.setBold(True)
                quick_label.setFont(quick_font)
                page_layout.addWidget(quick_label)
                quick_layout = QGridLayout()
                self.quick_navigation: dict[SettingCategory, QPushButton] = {}
                for position, destination in enumerate(
                    (
                        SettingCategory.TYPOGRAPHY,
                        SettingCategory.COLORS,
                        SettingCategory.PROGRESS,
                        SettingCategory.VISIBILITY,
                        SettingCategory.LAYOUT,
                        SettingCategory.MOTION,
                    )
                ):
                    button = QPushButton(destination.value)
                    button.clicked.connect(
                        lambda _checked=False, destination=destination: (
                            self.categories.setCurrentItem(
                                self._category_items[destination]
                            )
                        )
                    )
                    quick_layout.addWidget(button, position // 3, position % 3)
                    self.quick_navigation[destination] = button
                page_layout.addLayout(quick_layout)
            rows: list[SettingRow] = []
            section_rows: dict[str, list[SettingRow]] = {}
            section_order = tuple(
                dict.fromkeys(d.section for d in category_definitions)
            )
            ordered_definitions = sorted(
                category_definitions,
                key=lambda d: (
                    d.key in _ADVANCED_SETTING_KEYS,
                    section_order.index(d.section),
                ),
            )
            advanced_rows: list[SettingRow] = []
            advanced_toggle: QToolButton | None = None
            for definition in ordered_definitions:
                is_advanced = definition.key in _ADVANCED_SETTING_KEYS
                if is_advanced and advanced_toggle is None:
                    advanced_toggle = QToolButton()
                    advanced_toggle.setText("Advanced")
                    advanced_toggle.setCheckable(True)
                    advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
                    advanced_toggle.setToolButtonStyle(
                        Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                    )
                    advanced_toggle.setAccessibleName(
                        f"Show advanced {category.value} settings"
                    )
                    advanced_toggle.toggled.connect(
                        lambda checked, category=category: self._set_advanced_visible(
                            category, checked
                        )
                    )
                    page_layout.addWidget(advanced_toggle)
                    self._advanced_toggles[category] = advanced_toggle
                if definition.section not in section_rows:
                    section_rows[definition.section] = []
                    section = QWidget()
                    section_layout = QVBoxLayout(section)
                    section_layout.setContentsMargins(0, 16, 0, 0)
                    label = _plain_label(definition.section)
                    font = label.font()
                    font.setBold(True)
                    label.setFont(font)
                    section_layout.addWidget(label)
                    separator = QFrame()
                    separator.setFrameShape(QFrame.Shape.HLine)
                    section_layout.addWidget(separator)
                    page_layout.addWidget(section)
                    self._sections.append((section, section_rows[definition.section]))
                row = SettingRow(definition)
                row.change_requested.connect(self.change_requested)
                row.reset_requested.connect(self.reset_requested)
                page_layout.addWidget(row)
                self.rows[definition.key] = row
                rows.append(row)
                section_rows[definition.section].append(row)
                if is_advanced:
                    row.hide()
                    advanced_rows.append(row)
            page_layout.addStretch(1)
            self._category_rows[category] = rows
            self._advanced_rows[category] = advanced_rows
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            page_index = self.pages.addWidget(scroll)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, page_index)

        self._add_information_branch(
            "Workspace",
            "Panels & Workspaces",
            "The default workspace contains Lyrics, Metadata, Progress and Status "
            "panels. Moving, docking and saved workspaces are planned; no movable "
            "panel controls are exposed yet.",
        )
        self._add_information_branch(
            "Advanced",
            "Configuration & Diagnostics",
            "The canonical configuration path and diagnostic actions remain available "
            "below and from the application Help menu.",
        )

        self.categories.currentItemChanged.connect(self._navigation_changed)
        self._navigation_parents["Functionality"].setExpanded(True)
        self._navigation_parents["Appearance"].setExpanded(True)
        if self.categories.count():
            self.categories.setCurrentRow(0)

        self.no_results = _plain_label("No settings match this search.")
        self.no_results.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_results.setAccessibleName("No matching settings")
        self.no_results.setVisible(False)
        root.addWidget(self.no_results)

        footer = QHBoxLayout()
        self.reset_appearance_button = QPushButton("Reset Appearance…")
        self.reset_appearance_button.setAccessibleName(
            "Reset all appearance settings to defaults"
        )
        self.reset_appearance_button.setToolTip(
            "Remove the preset and every appearance override, including lyric layers"
        )
        self.reset_appearance_button.clicked.connect(self._confirm_reset_appearance)
        appearance_item = self._category_items.get(SettingCategory.APPEARANCE)
        if appearance_item is not None:
            page = cast(
                QScrollArea, self.pages.widget(self.categories.row(appearance_item))
            )
            page_widget = page.widget()
            assert page_widget is not None
            page_layout = cast(QVBoxLayout, page_widget.layout())
            page_layout.insertWidget(
                page_layout.count() - 1, self.reset_appearance_button
            )
        else:
            self.reset_appearance_button.hide()
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
        for row in self.rows.values():
            if isinstance(row.editor, OrderedStringListEditor):
                focus_order.extend(
                    (
                        row.editor.items,
                        row.editor.suggestions,
                        row.editor.input,
                        row.editor.add_button,
                        row.editor.cancel_button,
                        row.editor.remove_button,
                        row.editor.edit_button,
                        row.editor.up_button,
                        row.editor.down_button,
                    )
                )
            elif isinstance(
                row.editor,
                (
                    SemanticStringEditor,
                    FontWeightEditor,
                    LyricTransitionEditor,
                    AnimationSpeedEditor,
                ),
            ):
                focus_order.extend(row.editor.focus_widgets())
            else:
                focus_order.append(row.editor)
            focus_order.append(row.reset_button)
        focus_order.extend(
            (
                self.reset_appearance_button,
                self.path_display,
                self.copy_path_button,
                self.open_location_button,
                buttons.button(QDialogButtonBox.StandardButton.Close),
            )
        )
        for current, following in pairwise(focus_order):
            QWidget.setTabOrder(current, following)

        # A save must not disable successive focused controls: Qt advances focus
        # and QScrollArea follows it all the way to the final row. Briefly gate
        # editor input instead, retaining widget identity and keyboard focus.
        self._wheel_guard = SettingsWheelGuard(self)
        for row in self.rows.values():
            for widget in (row, *row.findChildren(QWidget)):
                widget.installEventFilter(self)
                widget.installEventFilter(self._wheel_guard)
        self.reset_appearance_button.installEventFilter(self)

        self._connect_font_preview()

        self.set_loading()

    def _connect_font_preview(self) -> None:
        keys = {
            "appearance.typography.original.family",
            "appearance.typography.original.size",
            "appearance.typography.original.weight",
            "appearance.typography.original.italic",
        }
        if not keys <= self.rows.keys():
            return
        family = self.rows["appearance.typography.original.family"].editor
        size = self.rows["appearance.typography.original.size"].editor
        weight = self.rows["appearance.typography.original.weight"].editor
        italic = self.rows["appearance.typography.original.italic"].editor
        assert isinstance(family, SemanticStringEditor)
        assert isinstance(size, QSpinBox)
        assert isinstance(weight, FontWeightEditor)
        assert isinstance(italic, QCheckBox)
        family.preview_changed.connect(lambda _value: self._refresh_font_preview())
        size.valueChanged.connect(lambda _value: self._refresh_font_preview())
        weight.preview_changed.connect(lambda _value: self._refresh_font_preview())
        italic.toggled.connect(lambda _value: self._refresh_font_preview())

    def _refresh_font_preview(self) -> None:
        if self.font_preview is None or self.font_preview_status is None:
            return
        family_editor = self.rows["appearance.typography.original.family"].editor
        size_editor = self.rows["appearance.typography.original.size"].editor
        weight_editor = self.rows["appearance.typography.original.weight"].editor
        italic_editor = self.rows["appearance.typography.original.italic"].editor
        assert isinstance(family_editor, SemanticStringEditor)
        assert isinstance(size_editor, QSpinBox)
        assert isinstance(weight_editor, FontWeightEditor)
        assert isinstance(italic_editor, QCheckBox)
        family = family_editor.value()
        size = size_editor.value()
        weight = weight_editor.value()
        italic = italic_editor.isChecked()
        font = QFont(self.font_preview.font())
        if family:
            font.setFamily(family)
        font.setPointSizeF(min(36.0, max(9.0, size * 0.75)))
        font.setWeight(QFont.Weight(weight))
        font.setItalic(italic)
        self.font_preview.setFont(font)
        rendered = QFontInfo(font).family()
        requested = family or "System default"
        weight_name = next(
            (label for label, value in _FONT_WEIGHTS if value == weight),
            f"Custom {weight}",
        )
        style = "italic" if italic else "upright"
        self.font_preview_status.setText(
            f"Requested: {requested} · Rendered: {rendered} · "
            f"{size} pt · {weight_name} · {style}"
        )

    def _add_information_branch(
        self, parent_name: str, child_name: str, text: str
    ) -> None:
        parent = NavigationItem((parent_name,))
        font = parent.font(0)
        font.setBold(True)
        parent.setFont(0, font)
        child = NavigationItem((child_name,))
        parent.addChild(child)
        self.categories.addTopLevelItem(parent)
        self._navigation_parents[parent_name] = parent
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = _plain_label(child_name)
        heading_font = heading.font()
        heading_font.setBold(True)
        heading.setFont(heading_font)
        layout.addWidget(heading)
        description = _plain_label(text)
        description.setWordWrap(True)
        layout.addWidget(description)
        layout.addStretch(1)
        page_index = self.pages.addWidget(page)
        child.setData(0, Qt.ItemDataRole.UserRole + 1, page_index)
        parent.setData(0, Qt.ItemDataRole.UserRole + 1, page_index)

    def _navigation_changed(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        if current is None:
            return
        page_index = current.data(0, Qt.ItemDataRole.UserRole + 1)
        if isinstance(page_index, int):
            self.pages.setCurrentIndex(page_index)
            return
        if current.childCount():
            first = current.child(0)
            assert first is not None
            child_page = first.data(0, Qt.ItemDataRole.UserRole + 1)
            if isinstance(child_page, int):
                self.pages.setCurrentIndex(child_page)

    def _set_advanced_visible(self, category: SettingCategory, visible: bool) -> None:
        toggle = self._advanced_toggles.get(category)
        if toggle is not None:
            toggle.setArrowType(
                Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow
            )
        if self.search.text():
            return
        advanced = set(self._advanced_rows.get(category, ()))
        for row in self._category_rows.get(category, ()):
            row.setVisible(row not in advanced or visible)
        self._refresh_section_visibility("")

    def _refresh_section_visibility(self, query: str) -> None:
        for section, rows in self._sections:
            section.setVisible(
                any(
                    row.isVisible() and (not query or row.matches(query))
                    for row in rows
                )
            )

    def set_loading(self) -> None:
        self._set_banner("Loading canonical settings…", error=False)
        self._set_all_editing_enabled(False)

    def set_snapshot(self, snapshot: SettingsSnapshot) -> None:
        for resolved in snapshot.values:
            row = self.rows.get(resolved.definition.key)
            if row is not None:
                row.apply(
                    resolved,
                    force=self._pending_key in {resolved.definition.key, "appearance"},
                )
        self._refresh_font_preview()
        self._refresh_player_overlap()
        self._pending_key = None
        self._set_all_editing_enabled(True)

    def set_player_suggestions(self, values: Iterable[str]) -> None:
        """Offer currently discovered stable identities without requiring one."""

        for key in ("players.preferred", "players.ignored"):
            row = self.rows.get(key)
            if row is not None and isinstance(row.editor, OrderedStringListEditor):
                row.editor.set_suggestions(values)

    def _refresh_player_overlap(self) -> None:
        preferred = self.rows.get("players.preferred")
        ignored = self.rows.get("players.ignored")
        if preferred is None or ignored is None:
            return
        assert isinstance(preferred.editor, OrderedStringListEditor)
        assert isinstance(ignored.editor, OrderedStringListEditor)
        preferred_keys = {
            value.strip().casefold() for value in preferred.editor.value()
        }
        ignored_keys = {value.strip().casefold() for value in ignored.editor.value()}
        overlap = preferred_keys & ignored_keys
        for editor in (preferred.editor, ignored.editor):
            if overlap:
                editor.validation_label.setText(
                    "Also listed as ignored; ignored-player rules take precedence."
                )
            elif editor.validation_label.text().startswith("Also listed as ignored"):
                editor.validation_label.clear()

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
        self._set_banner(f"Saving {key}…", error=False)
        return True

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if getattr(self, "_pending_key", None) is not None and event.type() in {
            QEvent.Type.KeyPress,
            QEvent.Type.KeyRelease,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.Wheel,
            QEvent.Type.InputMethod,
            QEvent.Type.ContextMenu,
        }:
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self.search.text():
            self.search.clear()
            self.search.setFocus()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self.search.hasFocus():
            self.categories.setFocus()
            event.accept()
            return
        super().keyPressEvent(event)

    def _set_banner(self, message: str, *, error: bool) -> None:
        self.error_banner.setText(message)
        self.error_banner.setProperty("error", error)
        self.error_banner.setVisible(True)

    def _set_all_editing_enabled(self, enabled: bool) -> None:
        for row in self.rows.values():
            row.set_editing_enabled(enabled)
        self.reset_appearance_button.setEnabled(enabled)

    def _filter(self, query: str) -> None:
        searching = bool(query)
        if searching and self._expansion_before_search is None:
            self._expansion_before_search = {
                name: item.isExpanded()
                for name, item in self._navigation_parents.items()
            }
        elif not searching and self._expansion_before_search is not None:
            for name, expanded in self._expansion_before_search.items():
                self._navigation_parents[name].setExpanded(expanded)
            self._expansion_before_search = None

        visible_categories: list[int] = []
        for index in range(self.categories.count()):
            item = self.categories.item(index)
            category = cast(SettingCategory, item.data(0, Qt.ItemDataRole.UserRole))
            rows = self._category_rows[category]
            advanced = set(self._advanced_rows.get(category, ()))
            show_advanced = self._advanced_toggles.get(category)
            advanced_expanded = show_advanced is not None and show_advanced.isChecked()
            visible = False
            for row in rows:
                matches = row.matches(query) and (
                    searching or row not in advanced or advanced_expanded
                )
                row.setVisible(matches)
                visible = visible or matches
            item.setHidden(not visible)
            if visible:
                visible_categories.append(index)
        for name, parent in self._navigation_parents.items():
            children = [parent.child(index) for index in range(parent.childCount())]
            has_visible_child = any(
                child is not None and not child.isHidden() for child in children
            )
            if searching and name in {"Workspace", "Advanced"}:
                has_visible_child = False
            parent.setHidden(not has_visible_child)
            if searching and has_visible_child:
                parent.setExpanded(True)
        self.no_results.setVisible(not visible_categories)
        self.pages.setVisible(bool(visible_categories))
        for toggle in self._advanced_toggles.values():
            toggle.setVisible(not searching)
        self._refresh_section_visibility(query)
        current = self.categories.currentItem()
        visible_items = {self.categories.item(index) for index in visible_categories}
        if visible_categories and current not in visible_items:
            self.categories.setCurrentRow(visible_categories[0])

    def _copy_path(self) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(str(self._config_path))

    def _confirm_reset_appearance(self) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Reset appearance?")
        dialog.setText("Reset appearance?")
        dialog.setInformativeText(
            "This restores KonoKashi's appearance settings to their defaults.\n"
            "Lyrics, library data and unrelated functionality settings are unaffected."
        )
        cancel = dialog.addButton(QMessageBox.StandardButton.Cancel)
        reset = dialog.addButton(
            "Reset Appearance", QMessageBox.ButtonRole.DestructiveRole
        )
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        if dialog.clickedButton() is reset:
            self.reset_appearance_requested.emit()

    def _open_location(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._config_path.parent)))
