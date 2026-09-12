"""Review and local-correction dialogs over frontend-neutral values."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from konokashi.application.lyric_corrections import validate_editor_edits
from konokashi.application.lyric_exchange import (
    LyricExchangeFormat,
    serialize_lyric_edits,
)
from konokashi.application.review_corrections import (
    ReviewCorrectionSnapshot,
    TranslationReviewLine,
)
from konokashi.domain.lyric_corrections import (
    LyricEditorSnapshot,
    LyricLineEdit,
)
from konokashi.domain.lyrics import (
    ContentProvenance,
    LyricsAlternative,
    LyricsMatchConfidence,
    LyricsMatchDecision,
    LyricTimingLevel,
)


class CorrectionActionKind(Enum):
    """One deliberate mutation requested from the passive Qt dialog."""

    PUT_TRACK_OVERRIDE = "put-track-override"
    RESET_TRACK_OVERRIDE = "reset-track-override"
    APPROVE_CURRENT = "approve-current"
    REJECT_CURRENT = "reject-current"
    CHOOSE_ALTERNATIVE = "choose-alternative"
    REJECT_ALTERNATIVE = "reject-alternative"
    SEARCH_MATCHES = "search-matches"
    REFRESH_MATCHES = "refresh-matches"
    ENRICH_YOUTUBE = "enrich-youtube"
    RESET_MATCH = "reset-match"
    SET_DELAY = "set-delay"
    RESET_DELAY = "reset-delay"
    SET_LANGUAGE_ZH = "set-language-zh"
    SET_LANGUAGE_JA = "set-language-ja"
    RESET_LANGUAGE = "reset-language"
    PUT_TRANSLATION = "put-translation"
    RESET_TRANSLATION = "reset-translation"
    APPLY_LYRIC_EDITS = "apply-lyric-edits"
    RESET_LYRIC_EDITS = "reset-lyric-edits"
    IMPORT_LYRIC_TEXT = "import-lyric-text"


@dataclass(frozen=True, slots=True)
class CorrectionActionRequest:
    """Plain values emitted to the coordinator for worker-owned application work."""

    kind: CorrectionActionKind
    title: str | None = None
    artists: tuple[str, ...] = ()
    album: str | None = None
    alternative: LyricsAlternative | None = None
    delay_us: int | None = None
    source_line_id: str | None = None
    text: str | None = None
    line_edits: tuple[LyricLineEdit, ...] = ()
    import_text: str | None = None


def _plain_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _artists(values: tuple[str, ...] | None) -> str:
    if values is None:
        return "<unavailable>"
    return " · ".join(values) if values else "<empty>"


def _provenance_text(value: ContentProvenance | None) -> str:
    return {
        ContentProvenance.LOCAL: "Local lyrics",
        ContentProvenance.IMPORTED: "Imported lyrics",
        ContentProvenance.PROVIDER: "Provider lyrics",
        ContentProvenance.USER: "Your corrected lyrics",
        ContentProvenance.GENERATED: "Generated lyrics",
        None: "Origin unavailable",
    }.get(value, "Origin unavailable")


def _timing_text(value: LyricTimingLevel | None) -> str:
    return {
        LyricTimingLevel.UNSYNCHRONIZED: "Not synchronized",
        LyricTimingLevel.LINE: "Line synchronized",
        LyricTimingLevel.WORD: "Word synchronized",
        LyricTimingLevel.ELEMENT: "Fine synchronized",
        None: "Timing unavailable",
    }[value]


def _current_review_copy(snapshot: ReviewCorrectionSnapshot) -> tuple[str, str]:
    """Translate retained matching evidence into a calm primary explanation."""

    if snapshot.current_document_id is None:
        if snapshot.current_match_decision is LyricsMatchDecision.REJECTED:
            return (
                "Lyrics need attention",
                "The previous lyrics were rejected for this recording. Find a "
                "different result or reset the saved choice.",
            )
        return (
            "No lyrics are active",
            "KonoKashi has not found a usable lyric document for this recording.",
        )
    if snapshot.current_match_decision is LyricsMatchDecision.APPROVED:
        return (
            "Lyrics are ready",
            "These are your saved lyrics for this exact recording.",
        )
    if snapshot.current_lyrics_provenance is ContentProvenance.LOCAL:
        return (
            "Lyrics are ready",
            "A local lyric source attached to this recording takes priority over "
            "provider search.",
        )
    if snapshot.current_lyrics_provenance is ContentProvenance.USER:
        return (
            "Lyrics are ready",
            "Your local corrections take priority while the source lyrics remain "
            "unchanged.",
        )
    if snapshot.current_match_confidence in {
        LyricsMatchConfidence.APPROVED,
        LyricsMatchConfidence.HIGH,
    }:
        return (
            "Lyrics are ready",
            "The recording identity and provider result form a strong match.",
        )
    if snapshot.current_match_confidence is LyricsMatchConfidence.MEDIUM:
        return (
            "Review suggested",
            "The result is plausible, but some recording evidence is incomplete or "
            "different.",
        )
    return (
        "Lyrics need attention",
        "The current result has weak matching evidence. It was not silently approved.",
    )


def _alternative_strength(value: LyricsMatchConfidence) -> str:
    return {
        LyricsMatchConfidence.APPROVED: "Saved match",
        LyricsMatchConfidence.HIGH: "Strong match",
        LyricsMatchConfidence.MEDIUM: "Possible match",
        LyricsMatchConfidence.LOW: "Weak match",
    }[value]


def _duration_text(value_ms: int | None) -> str:
    if value_ms is None:
        return "unknown length"
    total_seconds = max(0, value_ms // 1_000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _alternative_text(value: LyricsAlternative) -> str:
    candidate = value.candidate
    duration = _duration_text(candidate.duration_ms)
    rejected = " · previously rejected" if value.rejected else ""
    return (
        f"{_alternative_strength(value.confidence)} — {candidate.artist_name} — "
        f"{candidate.track_name} · {candidate.provider} · {duration}{rejected}"
    )


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


class ReviewCorrectionDialog(QDialog):
    """Review raw evidence and request one explicit correction at a time."""

    def __init__(
        self,
        snapshot: ReviewCorrectionSnapshot,
        parent: QWidget | None = None,
        *,
        position_ms: Callable[[], int | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._snapshot = snapshot
        self._position_ms = position_ms or (lambda: None)
        self._action: CorrectionActionRequest | None = None
        self._editor_dialog: LyricEditorDialog | None = None
        self.setWindowTitle("Review lyrics")
        self.resize(820, 700 if snapshot.alternatives_searched else 430)
        outer = QVBoxLayout(self)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_content = QWidget()
        root = QVBoxLayout(scroll_content)
        self.scroll_area.setWidget(scroll_content)
        outer.addWidget(self.scroll_area, 1)

        current_group = QGroupBox("Current lyrics")
        current_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        current_layout = QVBoxLayout(current_group)
        current_heading, current_reason = _current_review_copy(snapshot)
        self.current_status = _plain_label(current_heading)
        status_font = self.current_status.font()
        status_font.setBold(True)
        status_font.setPointSizeF(max(12.0, status_font.pointSizeF() + 2.0))
        self.current_status.setFont(status_font)
        self.current_status.setAccessibleName("Current lyrics status")
        current_layout.addWidget(self.current_status)
        summary_parts = [
            snapshot.current_lyrics_source or "No lyric source",
            _provenance_text(snapshot.current_lyrics_provenance),
            _timing_text(snapshot.current_timing_level),
            (
                "Confidence unavailable"
                if snapshot.current_match_confidence is None
                else (
                    "User approved"
                    if snapshot.current_match_confidence
                    is LyricsMatchConfidence.APPROVED
                    else f"{snapshot.current_match_confidence.value} confidence"
                )
            ),
        ]
        if snapshot.current_match_decision is LyricsMatchDecision.APPROVED:
            summary_parts.append("Your saved choice")
        elif snapshot.current_match_decision is LyricsMatchDecision.REJECTED:
            summary_parts.append("Rejected")
        self.current_summary = _plain_label(" · ".join(summary_parts))
        self.current_summary.setAccessibleName("Current lyric source summary")
        current_layout.addWidget(self.current_summary)
        self.current_reason = _plain_label(current_reason)
        self.current_reason.setAccessibleName("Why these lyrics are active")
        current_layout.addWidget(self.current_reason)

        primary_actions = QHBoxLayout()
        self.edit_lyrics_button = QPushButton("Edit lyrics…")
        self.adjust_timing_button = QPushButton("Adjust timing…")
        self.find_different_button = QPushButton("Find different lyrics…")
        primary_actions.addWidget(self.edit_lyrics_button)
        primary_actions.addWidget(self.adjust_timing_button)
        primary_actions.addWidget(self.find_different_button)
        primary_actions.addStretch(1)
        current_layout.addLayout(primary_actions)
        secondary_actions = QHBoxLayout()
        self.change_metadata_button = QPushButton("Change recording metadata…")
        self.other_corrections_button = QPushButton("Language and translation…")
        self.why_match_button = QPushButton("Details / Why this match?")
        for button in (
            self.change_metadata_button,
            self.other_corrections_button,
            self.why_match_button,
        ):
            button.setFlat(True)
            secondary_actions.addWidget(button)
        secondary_actions.addStretch(1)
        current_layout.addLayout(secondary_actions)
        current_layout.addWidget(
            _plain_label(
                "Any correction is stored only for this recording or lyric document; "
                "the original metadata and provider content stay unchanged."
            )
        )
        root.addWidget(current_group)

        metadata_group = QGroupBox("Recording metadata correction")
        metadata_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        self.metadata_group = metadata_group
        metadata_layout = QFormLayout(metadata_group)
        self.title_edit = QLineEdit(snapshot.track.effective_title or "")
        self.title_edit.setAccessibleName("Corrected track title")
        self.artists_edit = QLineEdit("; ".join(snapshot.track.effective_artists))
        self.artists_edit.setAccessibleName("Corrected track artists")
        self.artists_edit.setPlaceholderText(
            "Separate multiple artists with semicolons"
        )
        self.album_edit = QLineEdit(snapshot.track.effective_album or "")
        self.album_edit.setAccessibleName("Corrected track album")
        metadata_layout.addRow("Title", self.title_edit)
        metadata_layout.addRow("Artist(s)", self.artists_edit)
        metadata_layout.addRow("Album", self.album_edit)
        metadata_buttons = QHBoxLayout()
        self.save_track_button = QPushButton("Save correction")
        self.reset_track_button = QPushButton("Reset correction")
        self.save_track_button.setEnabled(snapshot.durable)
        self.reset_track_button.setEnabled(
            snapshot.durable and snapshot.has_track_override
        )
        self.save_track_button.clicked.connect(self._save_track)
        self.reset_track_button.clicked.connect(
            lambda: self._finish(CorrectionActionKind.RESET_TRACK_OVERRIDE)
        )
        metadata_buttons.addWidget(self.save_track_button)
        metadata_buttons.addWidget(self.reset_track_button)
        metadata_layout.addRow(metadata_buttons)
        root.addWidget(metadata_group)
        metadata_group.setVisible(False)

        lyrics_group = QGroupBox("Find different lyrics")
        lyrics_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        self.lyrics_group = lyrics_group
        lyrics_layout = QVBoxLayout(lyrics_group)
        lyrics_layout.addWidget(
            _plain_label(
                "Search deliberately when the current lyrics are wrong or missing. "
                "A search never replaces the current result until you choose one."
            )
        )
        search_layout = QFormLayout()
        self.search_title_edit = QLineEdit(
            snapshot.search_title or snapshot.track.effective_title or ""
        )
        self.search_title_edit.setAccessibleName("Lyrics search title")
        self.search_artists_edit = QLineEdit(
            "; ".join(snapshot.search_artists or snapshot.track.effective_artists)
        )
        self.search_artists_edit.setAccessibleName("Lyrics search artists")
        self.search_artists_edit.setPlaceholderText(
            "Separate multiple artists with semicolons"
        )
        search_layout.addRow("Search title", self.search_title_edit)
        search_layout.addRow("Search artist(s)", self.search_artists_edit)
        lyrics_layout.addLayout(search_layout)
        search_buttons = QHBoxLayout()
        self.search_button = QPushButton("Search")
        self.refresh_button = QPushButton("Refresh")
        self.enrich_button = QPushButton("Use YouTube metadata")
        self.search_button.clicked.connect(
            lambda: self._search(CorrectionActionKind.SEARCH_MATCHES)
        )
        self.refresh_button.clicked.connect(
            lambda: self._search(CorrectionActionKind.REFRESH_MATCHES)
        )
        self.enrich_button.setEnabled(snapshot.youtube_enrichment_available)
        self.enrich_button.setToolTip(
            "Contact YouTube for this public video's title, description credits, "
            "uploader, and duration. Audio, video, cookies, and lyric text are "
            "never sent or downloaded."
        )
        self.enrich_button.clicked.connect(
            lambda: self._finish(CorrectionActionKind.ENRICH_YOUTUBE)
        )
        search_buttons.addWidget(self.search_button)
        search_buttons.addWidget(self.refresh_button)
        search_buttons.addWidget(self.enrich_button)
        search_buttons.addStretch(1)
        lyrics_layout.addLayout(search_buttons)
        visible_alternatives = tuple(
            item
            for item in snapshot.alternatives
            if not item.current
            and item.confidence
            in {
                LyricsMatchConfidence.APPROVED,
                LyricsMatchConfidence.HIGH,
                LyricsMatchConfidence.MEDIUM,
            }
        )
        weak_alternatives = tuple(
            item
            for item in snapshot.alternatives
            if not item.current and item.confidence is LyricsMatchConfidence.LOW
        )
        self._visible_alternatives = visible_alternatives
        self._weak_alternatives = weak_alternatives
        if snapshot.alternatives_searched:
            result_text = (
                "No strong or possible alternatives were found."
                if not visible_alternatives
                else (
                    f"{len(visible_alternatives)} useful alternative"
                    + ("" if len(visible_alternatives) == 1 else "s")
                    + " found."
                )
            )
        else:
            result_text = "Alternative search has not been run."
        self.results_status = _plain_label(result_text)
        self.results_status.setAccessibleName("Alternative lyric search status")
        lyrics_layout.addWidget(self.results_status)
        self.alternatives = QComboBox()
        self.alternatives.setAccessibleName("Alternative lyric results")
        self._populate_alternatives(include_weak=False)
        lyrics_layout.addWidget(self.alternatives)
        self.show_weak_results_button = QPushButton(
            f"Show weak results ({len(weak_alternatives)})"
        )
        self.show_weak_results_button.setCheckable(True)
        self.show_weak_results_button.setVisible(bool(weak_alternatives))
        self.show_weak_results_button.toggled.connect(self._toggle_weak_results)
        lyrics_layout.addWidget(self.show_weak_results_button)
        self.alternative_details = _plain_label("")
        self.alternative_details.setAccessibleName("Selected lyric match evidence")
        lyrics_layout.addWidget(self.alternative_details)
        self.alternatives.currentIndexChanged.connect(self._update_alternative_details)
        self._update_alternative_details()
        match_buttons = QHBoxLayout()
        self.choose_button = QPushButton("Choose alternative")
        self.reject_alternative_button = QPushButton("Reject alternative")
        self.approve_button = QPushButton("Approve current")
        self.reject_button = QPushButton("Reject current")
        self.reset_match_button = QPushButton("Reset match choices")
        has_document = snapshot.current_document_id is not None
        self.choose_button.setEnabled(False)
        self.reject_alternative_button.setEnabled(False)
        self.approve_button.setEnabled(snapshot.durable and has_document)
        self.reject_button.setEnabled(snapshot.durable and has_document)
        self.reset_match_button.setEnabled(
            snapshot.durable and snapshot.current_match_decision is not None
        )
        self.choose_button.clicked.connect(self._choose_alternative)
        self.reject_alternative_button.clicked.connect(self._reject_alternative)
        self.approve_button.clicked.connect(
            lambda: self._finish(CorrectionActionKind.APPROVE_CURRENT)
        )
        self.reject_button.clicked.connect(
            lambda: self._finish(CorrectionActionKind.REJECT_CURRENT)
        )
        self.reset_match_button.clicked.connect(
            lambda: self._finish(CorrectionActionKind.RESET_MATCH)
        )
        for button in (
            self.choose_button,
            self.reject_alternative_button,
        ):
            match_buttons.addWidget(button)
        match_buttons.addStretch(1)
        lyrics_layout.addLayout(match_buttons)
        root.addWidget(lyrics_group)
        lyrics_group.setVisible(snapshot.alternatives_searched)

        language_group = QGroupBox("Han-language routing")
        language_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        self.language_group = language_group
        language_layout = QVBoxLayout(language_group)
        routing_text = (
            "No lyric document is loaded."
            if snapshot.routing_status is None
            else (
                f"State: {snapshot.routing_status.value} · "
                f"language: {snapshot.routing_language or 'undetermined'}\n"
                f"{snapshot.routing_diagnostic or ''}"
            )
        )
        language_layout.addWidget(_plain_label(routing_text))
        language_buttons = QHBoxLayout()
        self.chinese_button = QPushButton("Chinese (zh)")
        self.japanese_button = QPushButton("Japanese (ja)")
        self.automatic_language_button = QPushButton("Reset to automatic")
        self.chinese_button.setEnabled(has_document)
        self.japanese_button.setEnabled(has_document)
        self.automatic_language_button.setEnabled(
            has_document and snapshot.language_override is not None
        )
        self.chinese_button.clicked.connect(
            lambda: self._finish(CorrectionActionKind.SET_LANGUAGE_ZH)
        )
        self.japanese_button.clicked.connect(
            lambda: self._finish(CorrectionActionKind.SET_LANGUAGE_JA)
        )
        self.automatic_language_button.clicked.connect(
            lambda: self._finish(CorrectionActionKind.RESET_LANGUAGE)
        )
        language_buttons.addWidget(self.chinese_button)
        language_buttons.addWidget(self.japanese_button)
        language_buttons.addWidget(self.automatic_language_button)
        language_layout.addLayout(language_buttons)
        root.addWidget(language_group)
        language_group.setVisible(False)

        translation_group = QGroupBox("Local aligned translation")
        translation_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        self.translation_group = translation_group
        translation_layout = QVBoxLayout(translation_group)
        translation_layout.addWidget(
            _plain_label(
                "KonoKashi has no automatic translation backend. Edit one exact "
                "original line locally; saving approves that aligned translation "
                "without replacing the original lyrics."
            )
        )
        self.translation_lines = QComboBox()
        self.translation_lines.setAccessibleName("Original lyric line to translate")
        for line in snapshot.translation_lines:
            self.translation_lines.addItem(
                f"{line.source_line_id} · {line.original_text}",
                line,
            )
        self.translation_lines.setEnabled(bool(snapshot.translation_lines))
        translation_layout.addWidget(self.translation_lines)
        self.translation_original = _plain_label("")
        self.translation_original.setAccessibleName("Selected original lyric text")
        translation_layout.addWidget(self.translation_original)
        self.translation_edit = QLineEdit()
        self.translation_edit.setAccessibleName("Local aligned translation text")
        translation_layout.addWidget(self.translation_edit)
        translation_buttons = QHBoxLayout()
        self.save_translation_button = QPushButton("Save and approve translation")
        self.reset_translation_button = QPushButton("Reset translation")
        self.save_translation_button.clicked.connect(self._save_translation)
        self.reset_translation_button.clicked.connect(self._reset_translation)
        translation_buttons.addWidget(self.save_translation_button)
        translation_buttons.addWidget(self.reset_translation_button)
        translation_layout.addLayout(translation_buttons)
        self.translation_lines.currentIndexChanged.connect(
            self._update_translation_line
        )
        self._update_translation_line()
        root.addWidget(translation_group)
        translation_group.setVisible(False)

        editor_group = QGroupBox("Original lyric text and line timing")
        editor_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        self.editor_group = editor_group
        editor_layout = QVBoxLayout(editor_group)
        editor = snapshot.lyric_editor
        if editor is None:
            editor_summary = "No editable original lyric document is loaded."
        else:
            editor_summary = (
                f"{len(editor.lines)} source line(s) · "
                f"{editor.corrected_lines} corrected · "
                f"{editor.stale_corrections} stale. Provider/import source rows "
                "remain immutable."
            )
        editor_layout.addWidget(_plain_label(editor_summary))
        editor_buttons = QHBoxLayout()
        self.open_editor_button = self.edit_lyrics_button
        self.reset_lyrics_button = QPushButton("Revert all line edits")
        can_edit = snapshot.durable and editor is not None and bool(editor.lines)
        self.open_editor_button.setEnabled(can_edit)
        self.reset_lyrics_button.setEnabled(
            can_edit
            and bool(editor and (editor.corrected_lines or editor.stale_corrections))
        )
        self.open_editor_button.clicked.connect(self._open_editor)
        self.reset_lyrics_button.clicked.connect(
            lambda: self._finish(CorrectionActionKind.RESET_LYRIC_EDITS)
        )
        editor_buttons.addWidget(self.reset_lyrics_button)
        editor_buttons.addStretch(1)
        editor_layout.addLayout(editor_buttons)
        root.addWidget(editor_group)
        editor_group.setVisible(False)

        delay_group = QGroupBox("Recording lyric timing")
        delay_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        self.delay_group = delay_group
        delay_layout = QHBoxLayout(delay_group)
        self.delay_ms = QSpinBox()
        self.delay_ms.setRange(-60_000, 60_000)
        self.delay_ms.setSuffix(" ms")
        self.delay_ms.setValue(round(snapshot.display_delay_us / 1000))
        self.delay_ms.setAccessibleName("Exact lyric document display delay")
        self.save_delay_button = QPushButton("Apply delay")
        self.reset_delay_button = QPushButton("Reset delay")
        self.save_delay_button.setEnabled(snapshot.durable and has_document)
        self.reset_delay_button.setEnabled(
            snapshot.durable and has_document and snapshot.display_delay_us != 0
        )
        self.save_delay_button.clicked.connect(
            lambda: self._finish(
                CorrectionActionKind.SET_DELAY,
                delay_us=self.delay_ms.value() * 1000,
            )
        )
        self.reset_delay_button.clicked.connect(
            lambda: self._finish(CorrectionActionKind.RESET_DELAY)
        )
        delay_layout.addWidget(self.delay_ms)
        delay_layout.addWidget(self.save_delay_button)
        delay_layout.addWidget(self.reset_delay_button)
        root.addWidget(delay_group)
        delay_group.setVisible(False)

        details_group = QGroupBox("Details / Why this match?")
        details_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        self.details_group = details_group
        details_layout = QVBoxLayout(details_group)
        details_layout.addWidget(
            _plain_label(
                "Technical evidence is retained here for diagnosis. It does not "
                "change the current lyrics or select a provider result."
            )
        )
        current_match_buttons = QHBoxLayout()
        for button in (
            self.approve_button,
            self.reject_button,
            self.reset_match_button,
        ):
            current_match_buttons.addWidget(button)
        current_match_buttons.addStretch(1)
        details_layout.addLayout(current_match_buttons)
        audit = QPlainTextEdit()
        audit.setReadOnly(True)
        audit.setMinimumHeight(240)
        audit.setAccessibleName("Raw metadata and interpretation evidence")
        audit.setPlainText(self._audit_text(snapshot))
        details_layout.addWidget(audit, 1)
        root.addWidget(details_group, 1)
        details_group.setVisible(False)
        root.addStretch(1)

        for button in (
            self.adjust_timing_button,
            self.find_different_button,
            self.change_metadata_button,
            self.other_corrections_button,
            self.why_match_button,
        ):
            button.setCheckable(True)
        self.adjust_timing_button.toggled.connect(
            lambda visible: self._set_panel_visible(delay_group, visible)
        )
        self.find_different_button.toggled.connect(
            lambda visible: self._set_panel_visible(lyrics_group, visible)
        )
        self.change_metadata_button.toggled.connect(
            lambda visible: self._set_panel_visible(metadata_group, visible)
        )
        self.other_corrections_button.toggled.connect(
            lambda visible: self._set_correction_panels_visible(visible)
        )
        self.why_match_button.toggled.connect(
            lambda visible: self._set_panel_visible(details_group, visible)
        )
        self.find_different_button.setChecked(snapshot.alternatives_searched)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def action(self) -> CorrectionActionRequest | None:
        """Return the single deliberate action selected before the dialog closed."""

        return self._action

    def _set_correction_panels_visible(self, visible: bool) -> None:
        for panel in (
            self.language_group,
            self.translation_group,
            self.editor_group,
        ):
            panel.setVisible(visible)
        if visible:
            QTimer.singleShot(
                0, lambda: self.scroll_area.ensureWidgetVisible(self.language_group)
            )

    def _set_panel_visible(self, panel: QWidget, visible: bool) -> None:
        panel.setVisible(visible)
        if visible:
            QTimer.singleShot(0, lambda: self.scroll_area.ensureWidgetVisible(panel))

    def _populate_alternatives(self, *, include_weak: bool) -> None:
        self.alternatives.blockSignals(True)
        self.alternatives.clear()
        values = self._visible_alternatives + (
            self._weak_alternatives if include_weak else ()
        )
        for item in values:
            self.alternatives.addItem(_alternative_text(item), item)
        self.alternatives.setEnabled(bool(values))
        self.alternatives.setCurrentIndex(-1)
        self.alternatives.blockSignals(False)
        if hasattr(self, "choose_button"):
            self.choose_button.setEnabled(False)
            self.reject_alternative_button.setEnabled(False)
        if hasattr(self, "alternative_details"):
            self._update_alternative_details()

    def _toggle_weak_results(self, visible: bool) -> None:
        self.show_weak_results_button.setText(
            "Hide weak results"
            if visible
            else f"Show weak results ({len(self._weak_alternatives)})"
        )
        self._populate_alternatives(include_weak=visible)

    def _save_track(self) -> None:
        artists = tuple(
            item.strip() for item in self.artists_edit.text().split(";") if item.strip()
        )
        self._finish(
            CorrectionActionKind.PUT_TRACK_OVERRIDE,
            title=self.title_edit.text().strip(),
            artists=artists,
            album=self.album_edit.text().strip() or None,
        )

    def _choose_alternative(self) -> None:
        value = self.alternatives.currentData()
        if isinstance(value, LyricsAlternative):
            self._finish(
                CorrectionActionKind.CHOOSE_ALTERNATIVE,
                alternative=value,
            )

    def _reject_alternative(self) -> None:
        value = self.alternatives.currentData()
        if isinstance(value, LyricsAlternative):
            self._finish(
                CorrectionActionKind.REJECT_ALTERNATIVE,
                alternative=value,
            )

    def _search(self, kind: CorrectionActionKind) -> None:
        artists = tuple(
            item.strip()
            for item in self.search_artists_edit.text().split(";")
            if item.strip()
        )
        self._finish(
            kind,
            title=self.search_title_edit.text().strip(),
            artists=artists,
        )

    def _update_alternative_details(self) -> None:
        value = self.alternatives.currentData()
        selection_available = isinstance(value, LyricsAlternative)
        if hasattr(self, "choose_button"):
            enabled = self._snapshot.durable and selection_available
            self.choose_button.setEnabled(enabled)
            self.reject_alternative_button.setEnabled(enabled)
        if not isinstance(value, LyricsAlternative):
            if self.alternatives.count():
                text = (
                    "Select an alternative to inspect it. Nothing changes until you "
                    "choose one."
                )
            elif not self._snapshot.alternatives_searched:
                text = "Search has not been run for this review."
            elif self._weak_alternatives:
                text = (
                    "No strong or possible alternatives are available. Weak results "
                    "remain hidden unless you deliberately show them."
                )
            else:
                text = (
                    "No alternative result was useful enough to show. Try corrected "
                    "title or artist text if the recording metadata is wrong."
                )
            self.alternative_details.setText(text)
            return
        candidate = value.candidate
        duration = _duration_text(candidate.duration_ms)
        explanation = {
            LyricsMatchConfidence.APPROVED: "You previously saved this match.",
            LyricsMatchConfidence.HIGH: (
                "Its recording metadata forms a strong alternative match."
            ),
            LyricsMatchConfidence.MEDIUM: (
                "Some recording details differ or are missing, so review it before "
                "choosing."
            ),
            LyricsMatchConfidence.LOW: (
                "This result has weak evidence and is shown only because you asked "
                "to see weak results."
            ),
        }[value.confidence]
        rejected = " You previously rejected this result." if value.rejected else ""
        self.alternative_details.setText(
            f"{candidate.artist_name} — {candidate.track_name}\n"
            f"{candidate.provider} · {duration}\n{explanation}{rejected}"
        )

    def _update_translation_line(self) -> None:
        value = self.translation_lines.currentData()
        if not isinstance(value, TranslationReviewLine):
            self.translation_original.setText("No original lyric line is available.")
            self.translation_edit.clear()
            self.save_translation_button.setEnabled(False)
            self.reset_translation_button.setEnabled(False)
            return
        self.translation_original.setText(
            f"Original ({value.source_line_id}): {value.original_text}"
        )
        self.translation_edit.setText(value.translated_text or "")
        self.save_translation_button.setEnabled(True)
        self.reset_translation_button.setEnabled(
            value.translated_text is not None or value.approval_state is not None
        )

    def _save_translation(self) -> None:
        value = self.translation_lines.currentData()
        if isinstance(value, TranslationReviewLine):
            self._finish(
                CorrectionActionKind.PUT_TRANSLATION,
                source_line_id=value.source_line_id,
                text=self.translation_edit.text().strip(),
            )

    def _reset_translation(self) -> None:
        value = self.translation_lines.currentData()
        if isinstance(value, TranslationReviewLine):
            self._finish(
                CorrectionActionKind.RESET_TRANSLATION,
                source_line_id=value.source_line_id,
            )

    def _open_editor(self) -> None:
        editor = self._snapshot.lyric_editor
        if editor is None:
            return
        dialog = LyricEditorDialog(editor, self._position_ms, self)
        self._editor_dialog = dialog
        dialog.accepted.connect(lambda: self._editor_accepted(dialog))
        dialog.finished.connect(lambda _result: self._editor_finished(dialog))
        dialog.open()

    def _editor_accepted(self, dialog: LyricEditorDialog) -> None:
        imported = dialog.imported_text()
        if imported is not None:
            self._finish(CorrectionActionKind.IMPORT_LYRIC_TEXT, import_text=imported)
        else:
            self._finish(
                CorrectionActionKind.APPLY_LYRIC_EDITS,
                line_edits=dialog.edits(),
            )

    def _editor_finished(self, dialog: LyricEditorDialog) -> None:
        if self._editor_dialog is dialog:
            self._editor_dialog = None

    def _finish(
        self,
        kind: CorrectionActionKind,
        *,
        title: str | None = None,
        artists: tuple[str, ...] = (),
        album: str | None = None,
        alternative: LyricsAlternative | None = None,
        delay_us: int | None = None,
        source_line_id: str | None = None,
        text: str | None = None,
        line_edits: tuple[LyricLineEdit, ...] = (),
        import_text: str | None = None,
    ) -> None:
        self._action = CorrectionActionRequest(
            kind,
            title=title,
            artists=artists,
            album=album,
            alternative=alternative,
            delay_us=delay_us,
            source_line_id=source_line_id,
            text=text,
            line_edits=line_edits,
            import_text=import_text,
        )
        self.accept()

    @staticmethod
    def _audit_text(snapshot: ReviewCorrectionSnapshot) -> str:
        track = snapshot.track
        lines = [
            "Raw player metadata (unchanged)",
            f"title: {track.raw_title or '<unavailable>'}",
            f"artist/uploader: {_artists(track.raw_artists)}",
            f"album: {track.raw_album or '<unavailable>'}",
            f"URL: {track.raw_url or '<unavailable>'}",
            f"duration: {track.raw_duration_us or '<unavailable>'} us",
            "",
            "Automatic interpretation",
            f"title: {track.automatic_title or '<unavailable>'}",
            f"artist: {_artists(track.automatic_artists)}",
            f"album: {track.automatic_album or '<unavailable>'}",
            f"confidence: {track.automatic_confidence}",
            "",
            "Effective interpretation",
            f"title: {track.effective_title or '<unavailable>'}",
            f"artist: {_artists(track.effective_artists)}",
            f"album: {track.effective_album or '<unavailable>'}",
            f"confidence: {track.effective_confidence}",
            "",
            "Current lyric provider values (unchanged)",
            f"title: {snapshot.current_provider_title or '<unavailable>'}",
            f"artist: {snapshot.current_provider_artist or '<unavailable>'}",
            f"album: {snapshot.current_provider_album or '<unavailable>'}",
            "duration: "
            + (
                "<unavailable>"
                if snapshot.current_provider_duration_ms is None
                else f"{snapshot.current_provider_duration_ms} ms"
            ),
            "",
            "Automatic transformations:",
            *(f"- {item}" for item in track.transformations or ("none",)),
            "Evidence:",
            *(f"- {item}" for item in track.evidence or ("none",)),
            "Warnings:",
            *(f"- {item}" for item in track.warnings or ("none",)),
            "Lyric match evidence:",
            *(f"- {item}" for item in snapshot.current_match_evidence or ("none",)),
            "Alternative diagnostics:",
            *(
                (
                    f"- {item.candidate.provider} #{item.candidate.record_id}: "
                    f"overall {item.confidence.value}; "
                    f"text {item.text_confidence.value}; "
                    f"timing {item.timing_confidence.value}; "
                    f"strategy {item.strategy}; current {item.current}; "
                    f"rejected {item.rejected}; evidence "
                    f"{'; '.join(item.evidence) or 'none'}"
                )
                for item in snapshot.alternatives
            ),
            "",
            "Representation routing:",
            "state: "
            + (
                "unavailable"
                if snapshot.routing_status is None
                else snapshot.routing_status.value
            ),
            f"language: {snapshot.routing_language or '<undetermined>'}",
            f"evidence: {snapshot.routing_diagnostic or 'none'}",
            "",
            "Representation layers:",
            *(
                (
                    f"- {item.kind.value}: {item.availability.value}; "
                    f"eligible {item.eligible}/{item.original_lines}; "
                    f"persisted {item.candidate_persisted}; "
                    f"selected {item.candidate_selected}; "
                    f"rendered {item.candidate_rendered}; "
                    f"origins {', '.join(item.origins) or 'none'}"
                )
                for item in snapshot.layer_statuses
            ),
        ]
        if snapshot.diagnostics:
            lines.extend(
                ("Diagnostics:", *(f"- {item}" for item in snapshot.diagnostics))
            )
        return "\n".join(lines)
