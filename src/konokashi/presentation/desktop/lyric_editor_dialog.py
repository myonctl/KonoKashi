"""Self-contained local lyric correction editor for the Qt desktop."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from konokashi.application.lyric_corrections import validate_editor_edits
from konokashi.application.lyric_exchange import (
    LyricExchangeFormat,
    serialize_lyric_edits,
)
from konokashi.domain.lyric_corrections import LyricEditorSnapshot, LyricLineEdit


def _plain_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class LyricEditorDialog(QDialog):
    """Small line editor with local preview, stamping, undo, and text exchange."""

    def __init__(
        self,
        snapshot: LyricEditorSnapshot,
        position_ms: Callable[[], int | None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._snapshot = snapshot
        self._position_ms = position_ms
        self._lines = tuple(
            LyricLineEdit(line.line_id, line.effective_text, line.effective_start_ms)
            for line in snapshot.lines
        )
        self._history: list[tuple[LyricLineEdit, ...]] = []
        self._import_text: str | None = None
        self.setWindowTitle("Edit local lyric corrections")
        self.resize(720, 620)
        root = QVBoxLayout(self)
        root.addWidget(
            _plain_label(
                f"Source: {snapshot.source_name}. Edits are a local overlay; the "
                "source document remains immutable. Save replaces only this "
                "document's correction layer."
            )
        )
        if snapshot.diagnostics:
            root.addWidget(_plain_label(" · ".join(snapshot.diagnostics)))

        selector = QHBoxLayout()
        self.line_selector = QComboBox()
        self.line_selector.setAccessibleName("Lyric line to edit")
        for index, line in enumerate(snapshot.lines, start=1):
            flags = []
            if line.text_corrected:
                flags.append("text")
            if line.timing_corrected:
                flags.append("time")
            suffix = "" if not flags else f" [{'+'.join(flags)}]"
            self.line_selector.addItem(
                f"{index}. {line.effective_text or '<blank>'}{suffix}", index - 1
            )
        selector.addWidget(self.line_selector, 1)
        self.previous_button = QPushButton("Previous")
        self.next_button = QPushButton("Next")
        selector.addWidget(self.previous_button)
        selector.addWidget(self.next_button)
        root.addLayout(selector)

        source_group = QGroupBox("Immutable source evidence")
        source_layout = QFormLayout(source_group)
        self.source_text = _plain_label("")
        self.source_timestamp = _plain_label("")
        source_layout.addRow("Text", self.source_text)
        source_layout.addRow("Timestamp", self.source_timestamp)
        root.addWidget(source_group)

        edit_group = QGroupBox("Effective local line")
        edit_layout = QFormLayout(edit_group)
        self.text_edit = QLineEdit()
        self.text_edit.setAccessibleName("Corrected original lyric text")
        self.timestamp_ms = QSpinBox()
        self.timestamp_ms.setRange(-1, 604_800_000)
        self.timestamp_ms.setSpecialValueText("Untimed")
        self.timestamp_ms.setSuffix(" ms")
        self.timestamp_ms.setAccessibleName("Corrected line timestamp")
        edit_layout.addRow("Text", self.text_edit)
        edit_layout.addRow("Start", self.timestamp_ms)
        edit_buttons = QHBoxLayout()
        self.apply_line_button = QPushButton("Apply to preview")
        self.reset_line_button = QPushButton("Reset line")
        self.stamp_button = QPushButton("Stamp current position && next")
        self.stamp_button.setShortcut("Ctrl+Space")
        self.stamp_button.setToolTip(
            "Use the current MPRIS playback position and advance to the next line "
            "(Ctrl+Space)."
        )
        edit_buttons.addWidget(self.apply_line_button)
        edit_buttons.addWidget(self.reset_line_button)
        edit_buttons.addWidget(self.stamp_button)
        edit_layout.addRow(edit_buttons)
        root.addWidget(edit_group)

        history_buttons = QHBoxLayout()
        self.undo_button = QPushButton("Undo editor change")
        self.reset_all_button = QPushButton("Reset all in preview")
        self.copy_plain_button = QPushButton("Copy plain")
        self.copy_lrc_button = QPushButton("Copy LRC")
        self.import_button = QPushButton("Import pasted LRC/plain")
        for button in (
            self.undo_button,
            self.reset_all_button,
            self.copy_plain_button,
            self.copy_lrc_button,
            self.import_button,
        ):
            history_buttons.addWidget(button)
        root.addLayout(history_buttons)

        self.preview_now = _plain_label("")
        self.preview_now.setAccessibleName("Live corrected lyric preview")
        root.addWidget(self.preview_now)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("Portable corrected lyrics preview")
        root.addWidget(self.preview, 1)
        self.validation = _plain_label("")
        self.validation.setAccessibleName("Lyric editor validation status")
        root.addWidget(self.validation)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.line_selector.currentIndexChanged.connect(self._load_line)
        self.previous_button.clicked.connect(lambda: self._move(-1))
        self.next_button.clicked.connect(lambda: self._move(1))
        self.apply_line_button.clicked.connect(self._apply_current)
        self.reset_line_button.clicked.connect(self._reset_current)
        self.stamp_button.clicked.connect(self._stamp_current)
        self.undo_button.clicked.connect(self._undo)
        self.reset_all_button.clicked.connect(self._reset_all)
        self.copy_plain_button.clicked.connect(
            lambda: self._copy(LyricExchangeFormat.PLAIN)
        )
        self.copy_lrc_button.clicked.connect(
            lambda: self._copy(LyricExchangeFormat.LRC)
        )
        self.import_button.clicked.connect(self._import_clipboard)
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(100)
        self._preview_timer.timeout.connect(self._update_live_preview)
        self._preview_timer.start()
        self._load_line()
        self._update_preview()

    def edits(self) -> tuple[LyricLineEdit, ...]:
        """Return the complete accepted editor state in source order."""

        return self._lines

    def imported_text(self) -> str | None:
        """Return explicitly pasted portable text for application-layer parsing."""

        return self._import_text

    def _index(self) -> int:
        return max(0, self.line_selector.currentIndex())

    def _load_line(self) -> None:
        if not self._lines:
            return
        index = self._index()
        source = self._snapshot.lines[index]
        effective = self._lines[index]
        self.source_text.setText(source.source_text or "<blank>")
        self.source_timestamp.setText(_timestamp_text(source.source_start_ms))
        self.text_edit.setText(effective.text)
        self.timestamp_ms.setValue(
            -1 if effective.start_ms is None else effective.start_ms
        )
        self.previous_button.setEnabled(index > 0)
        self.next_button.setEnabled(index + 1 < len(self._lines))
        self.reset_line_button.setEnabled(
            effective.text != source.source_text
            or effective.start_ms != source.source_start_ms
        )

    def _move(self, delta: int) -> None:
        self._apply_current()
        self.line_selector.setCurrentIndex(
            max(0, min(len(self._lines) - 1, self._index() + delta))
        )

    def _replace_current(self, value: LyricLineEdit, *, advance: bool = False) -> None:
        index = self._index()
        if value != self._lines[index]:
            self._history.append(self._lines)
            lines = list(self._lines)
            lines[index] = value
            self._lines = tuple(lines)
        self._refresh_selector()
        self._update_preview()
        if advance and index + 1 < len(self._lines):
            self.line_selector.setCurrentIndex(index + 1)
        else:
            self._load_line()

    def _apply_current(self) -> None:
        if not self._lines:
            return
        current = self._lines[self._index()]
        timestamp = self.timestamp_ms.value()
        self._replace_current(
            LyricLineEdit(
                current.line_id,
                self.text_edit.text(),
                None if timestamp < 0 else timestamp,
            )
        )

    def _reset_current(self) -> None:
        source = self._snapshot.lines[self._index()]
        self._replace_current(
            LyricLineEdit(source.line_id, source.source_text, source.source_start_ms)
        )

    def _stamp_current(self) -> None:
        position = self._position_ms()
        if position is None or position < 0:
            self.validation.setText(
                "Current playback position is unavailable; no timestamp was changed."
            )
            return
        current = self._lines[self._index()]
        self._replace_current(
            LyricLineEdit(current.line_id, self.text_edit.text(), position),
            advance=True,
        )

    def _undo(self) -> None:
        if not self._history:
            return
        self._lines = self._history.pop()
        self._refresh_selector()
        self._update_preview()
        self._load_line()

    def _reset_all(self) -> None:
        reset = tuple(
            LyricLineEdit(line.line_id, line.source_text, line.source_start_ms)
            for line in self._snapshot.lines
        )
        if reset != self._lines:
            self._history.append(self._lines)
            self._lines = reset
        self._refresh_selector()
        self._update_preview()
        self._load_line()

    def _refresh_selector(self) -> None:
        current = self._index()
        self.line_selector.blockSignals(True)
        for index, (source, effective) in enumerate(
            zip(self._snapshot.lines, self._lines, strict=True)
        ):
            flags = []
            if effective.text != source.source_text:
                flags.append("text")
            if effective.start_ms != source.source_start_ms:
                flags.append("time")
            suffix = "" if not flags else f" [{'+'.join(flags)}]"
            self.line_selector.setItemText(
                index, f"{index + 1}. {effective.text or '<blank>'}{suffix}"
            )
        self.line_selector.blockSignals(False)
        self.line_selector.setCurrentIndex(current)
        self.undo_button.setEnabled(bool(self._history))

    def _copy(self, format: LyricExchangeFormat) -> None:
        self._apply_current()
        try:
            validate_editor_edits(self._snapshot, self._lines)
            content = serialize_lyric_edits(self._lines, format)
        except ValueError as error:
            self.validation.setText(str(error))
            return
        QApplication.clipboard().setText(content)
        self.validation.setText(
            f"Copied {format.value.upper()} text with {len(self._lines)} line(s)."
        )

    def _import_clipboard(self) -> None:
        text = QApplication.clipboard().text()
        if not text.strip():
            self.validation.setText("Clipboard contains no lyric text to import.")
            return
        self._import_text = text
        self.accept()

    def _save(self) -> None:
        self._apply_current()
        try:
            validate_editor_edits(self._snapshot, self._lines)
        except ValueError as error:
            self.validation.setText(str(error))
            return
        self.accept()

    def _update_preview(self) -> None:
        validation_error: ValueError | None = None
        try:
            validate_editor_edits(self._snapshot, self._lines)
        except ValueError as error:
            validation_error = error
        try:
            content = serialize_lyric_edits(self._lines, LyricExchangeFormat.LRC)
            valid_message = (
                "Preview is valid line-synchronized LRC. Equal timestamps are allowed."
            )
        except ValueError:
            content = serialize_lyric_edits(self._lines, LyricExchangeFormat.PLAIN)
            timed = sum(item.start_ms is not None for item in self._lines)
            valid_message = (
                f"Plain preview: {timed}/{len(self._lines)} lines are stamped; "
                "all lines must be stamped before a timed save/export."
            )
        self.validation.setText(
            str(validation_error) if validation_error is not None else valid_message
        )
        self.preview.setPlainText(content)
        self.undo_button.setEnabled(bool(self._history))
        self._update_live_preview()

    def _update_live_preview(self) -> None:
        position = self._position_ms()
        self.stamp_button.setEnabled(position is not None and position >= 0)
        if position is None or position < 0:
            self.preview_now.setText("Live preview: playback position unavailable")
            return
        timed = tuple(
            (line.start_ms, index, line)
            for index, line in enumerate(self._lines)
            if line.start_ms is not None and line.start_ms <= position
        )
        if not timed:
            text = "before the first stamped line"
        else:
            _start, index, line = max(timed, key=lambda item: (item[0], item[1]))
            text = f"line {index + 1}: {line.text or '<blank>'}"
        self.preview_now.setText(
            f"Live preview at {_timestamp_text(position)} — {text}"
        )


def _timestamp_text(value_ms: int | None) -> str:
    if value_ms is None:
        return "untimed"
    minutes, remainder = divmod(value_ms, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
