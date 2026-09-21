"""Deliberate recovery search and candidate-card selection surface."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from konokashi.application.review_corrections import ReviewCorrectionSnapshot
from konokashi.domain.lyrics import LyricsAlternative, LyricsMatchConfidence


def _plain_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


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
        f"{candidate.artist_name} — {candidate.track_name} · {candidate.provider} · "
        f"{duration} · {_alternative_strength(value.confidence)}{rejected}"
    )


class _AlternativeCard(QFrame):
    """Readable result summary with actions attached to that exact result."""

    use_requested = Signal(object)
    why_requested = Signal(object)

    def __init__(self, value: LyricsAlternative, *, durable: bool) -> None:
        super().__init__()
        self.value = value
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAccessibleName(_alternative_text(value))
        layout = QVBoxLayout(self)

        candidate = value.candidate
        heading = _plain_label(f"{candidate.artist_name} — {candidate.track_name}")
        heading_font = heading.font()
        heading_font.setBold(True)
        heading.setFont(heading_font)
        layout.addWidget(heading)

        summary = _plain_label(
            f"{candidate.provider} · {_duration_text(candidate.duration_ms)} · "
            f"{_alternative_strength(value.confidence)}"
            + (" · previously rejected" if value.rejected else "")
        )
        layout.addWidget(summary)

        buttons = QHBoxLayout()
        self.use_button = QPushButton("Use this")
        self.use_button.setEnabled(durable)
        self.why_button = QPushButton("Why this result?")
        self.why_button.setFlat(True)
        self.use_button.clicked.connect(lambda: self.use_requested.emit(self.value))
        self.why_button.clicked.connect(lambda: self.why_requested.emit(self.value))
        buttons.addWidget(self.use_button)
        buttons.addWidget(self.why_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)


class ReviewAlternativePanel(QGroupBox):
    """Own recovery search, usefulness filtering, and explicit result selection."""

    search_requested = Signal(str, object)
    refresh_requested = Signal(str, object)
    choose_requested = Signal(object)
    reject_requested = Signal(object)

    def __init__(self, snapshot: ReviewCorrectionSnapshot) -> None:
        super().__init__("Find different lyrics")
        self._snapshot = snapshot
        self._cards: list[_AlternativeCard] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _plain_label(
                "Use this only when the automatic result is missing or wrong. "
                "Nothing changes until you choose a result."
            )
        )

        self.search_controls_button = QPushButton("Search with corrected terms…")
        self.search_controls_button.setCheckable(True)
        layout.addWidget(self.search_controls_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.search_controls = QWidget()
        search_controls_layout = QVBoxLayout(self.search_controls)
        search_controls_layout.setContentsMargins(0, 0, 0, 0)
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
        search_controls_layout.addLayout(search_layout)

        search_buttons = QHBoxLayout()
        self.search_button = QPushButton("Search")
        self.refresh_button = QPushButton("Refresh")
        self.search_button.clicked.connect(self._request_search)
        self.refresh_button.clicked.connect(self._request_refresh)
        search_buttons.addWidget(self.search_button)
        search_buttons.addWidget(self.refresh_button)
        search_buttons.addStretch(1)
        search_controls_layout.addLayout(search_buttons)
        self.search_controls.setVisible(False)
        self.search_controls_button.toggled.connect(self.search_controls.setVisible)
        layout.addWidget(self.search_controls)

        self._visible_alternatives = tuple(
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
        self._weak_alternatives = tuple(
            item
            for item in snapshot.alternatives
            if not item.current and item.confidence is LyricsMatchConfidence.LOW
        )
        if snapshot.alternatives_searched:
            result_text = (
                "No strong or possible alternatives were found."
                if not self._visible_alternatives
                else (
                    f"{len(self._visible_alternatives)} useful alternative"
                    + ("" if len(self._visible_alternatives) == 1 else "s")
                    + " found."
                )
            )
        else:
            result_text = "Alternative search has not been run."
        self.results_status = _plain_label(result_text)
        self.results_status.setAccessibleName("Alternative lyric search status")
        layout.addWidget(self.results_status)

        self.alternatives = QListWidget()
        self.alternatives.setAccessibleName("Alternative lyric results")
        self.alternatives.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.alternatives.currentRowChanged.connect(self._selection_changed)
        layout.addWidget(self.alternatives)

        self.show_weak_results_button = QPushButton(
            f"Show weak results ({len(self._weak_alternatives)})"
        )
        self.show_weak_results_button.setCheckable(True)
        self.show_weak_results_button.setVisible(bool(self._weak_alternatives))
        self.show_weak_results_button.toggled.connect(self._toggle_weak_results)
        layout.addWidget(self.show_weak_results_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.alternative_details = _plain_label("")
        self.alternative_details.setAccessibleName("Selected lyric match evidence")
        self.alternative_details.setVisible(False)
        layout.addWidget(self.alternative_details)

        self.reject_alternative_button = QPushButton("Reject this result")
        self.reject_alternative_button.setFlat(True)
        self.reject_alternative_button.setEnabled(False)
        self.reject_alternative_button.clicked.connect(self._reject_alternative)
        layout.addWidget(self.reject_alternative_button, 0, Qt.AlignmentFlag.AlignLeft)

        self._populate_alternatives(include_weak=False)

    @property
    def cards(self) -> tuple[_AlternativeCard, ...]:
        """Return visible result cards for UI automation and accessibility tests."""

        return tuple(self._cards)

    def _search_values(self) -> tuple[str, tuple[str, ...]]:
        artists = tuple(
            item.strip()
            for item in self.search_artists_edit.text().split(";")
            if item.strip()
        )
        return self.search_title_edit.text().strip(), artists

    def _request_search(self) -> None:
        title, artists = self._search_values()
        self.search_requested.emit(title, artists)

    def _request_refresh(self) -> None:
        title, artists = self._search_values()
        self.refresh_requested.emit(title, artists)

    def _reject_alternative(self) -> None:
        value = self._selected_alternative()
        if value is not None:
            self.reject_requested.emit(value)

    def _selected_alternative(self) -> LyricsAlternative | None:
        item = self.alternatives.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, LyricsAlternative) else None

    def _populate_alternatives(self, *, include_weak: bool) -> None:
        self.alternatives.blockSignals(True)
        self.alternatives.clear()
        self._cards.clear()
        values = self._visible_alternatives + (
            self._weak_alternatives if include_weak else ()
        )
        for value in values:
            item = QListWidgetItem(_alternative_text(value))
            item.setData(Qt.ItemDataRole.UserRole, value)
            card = _AlternativeCard(value, durable=self._snapshot.durable)
            card.use_requested.connect(self.choose_requested)
            card.why_requested.connect(self._show_details)
            item.setSizeHint(card.sizeHint())
            self.alternatives.addItem(item)
            self.alternatives.setItemWidget(item, card)
            self._cards.append(card)
        self.alternatives.setVisible(bool(values))
        self.alternatives.setCurrentRow(-1)
        self.alternatives.blockSignals(False)
        self.reject_alternative_button.setEnabled(False)
        self.alternative_details.setVisible(False)

    def _toggle_weak_results(self, visible: bool) -> None:
        self.show_weak_results_button.setText(
            "Hide weak results"
            if visible
            else f"Show weak results ({len(self._weak_alternatives)})"
        )
        self._populate_alternatives(include_weak=visible)

    def _selection_changed(self, _row: int) -> None:
        self.reject_alternative_button.setEnabled(
            self._snapshot.durable and self._selected_alternative() is not None
        )

    def _show_details(self, value: LyricsAlternative) -> None:
        for row in range(self.alternatives.count()):
            item = self.alternatives.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == value:
                self.alternatives.setCurrentRow(row)
                break
        candidate = value.candidate
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
            f"{candidate.provider} · {_duration_text(candidate.duration_ms)}\n"
            f"{explanation}{rejected}"
        )
        self.alternative_details.setVisible(True)
