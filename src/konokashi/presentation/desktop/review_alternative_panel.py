"""Deliberate provider-search and alternative-selection surface."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
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
        f"{_alternative_strength(value.confidence)} — {candidate.artist_name} — "
        f"{candidate.track_name} · {candidate.provider} · {duration}{rejected}"
    )


class ReviewAlternativePanel(QGroupBox):
    """Own search inputs, usefulness filtering, and explicit result selection."""

    search_requested = Signal(str, object)
    refresh_requested = Signal(str, object)
    enrichment_requested = Signal()
    choose_requested = Signal(object)
    reject_requested = Signal(object)

    def __init__(self, snapshot: ReviewCorrectionSnapshot) -> None:
        super().__init__("Find different lyrics")
        self._snapshot = snapshot
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.addWidget(
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
        layout.addLayout(search_layout)

        search_buttons = QHBoxLayout()
        self.search_button = QPushButton("Search")
        self.refresh_button = QPushButton("Refresh")
        self.enrich_button = QPushButton("Use YouTube metadata")
        self.search_button.clicked.connect(self._request_search)
        self.refresh_button.clicked.connect(self._request_refresh)
        self.enrich_button.setEnabled(snapshot.youtube_enrichment_available)
        self.enrich_button.setToolTip(
            "Contact YouTube for this public video's title, description credits, "
            "uploader, and duration. Audio, video, cookies, and lyric text are "
            "never sent or downloaded."
        )
        self.enrich_button.clicked.connect(self.enrichment_requested)
        search_buttons.addWidget(self.search_button)
        search_buttons.addWidget(self.refresh_button)
        search_buttons.addWidget(self.enrich_button)
        search_buttons.addStretch(1)
        layout.addLayout(search_buttons)

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

        self.alternatives = QComboBox()
        self.alternatives.setAccessibleName("Alternative lyric results")
        layout.addWidget(self.alternatives)
        self.show_weak_results_button = QPushButton(
            f"Show weak results ({len(self._weak_alternatives)})"
        )
        self.show_weak_results_button.setCheckable(True)
        self.show_weak_results_button.setVisible(bool(self._weak_alternatives))
        self.show_weak_results_button.toggled.connect(self._toggle_weak_results)
        layout.addWidget(self.show_weak_results_button)
        self.alternative_details = _plain_label("")
        self.alternative_details.setAccessibleName("Selected lyric match evidence")
        layout.addWidget(self.alternative_details)

        match_buttons = QHBoxLayout()
        self.choose_button = QPushButton("Choose alternative")
        self.reject_alternative_button = QPushButton("Reject alternative")
        self.choose_button.setEnabled(False)
        self.reject_alternative_button.setEnabled(False)
        self.choose_button.clicked.connect(self._choose_alternative)
        self.reject_alternative_button.clicked.connect(self._reject_alternative)
        match_buttons.addWidget(self.choose_button)
        match_buttons.addWidget(self.reject_alternative_button)
        match_buttons.addStretch(1)
        layout.addLayout(match_buttons)

        self.alternatives.currentIndexChanged.connect(self._update_alternative_details)
        self._populate_alternatives(include_weak=False)

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

    def _choose_alternative(self) -> None:
        value = self.alternatives.currentData()
        if isinstance(value, LyricsAlternative):
            self.choose_requested.emit(value)

    def _reject_alternative(self) -> None:
        value = self.alternatives.currentData()
        if isinstance(value, LyricsAlternative):
            self.reject_requested.emit(value)

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
        self.choose_button.setEnabled(False)
        self.reject_alternative_button.setEnabled(False)
        self._update_alternative_details()

    def _toggle_weak_results(self, visible: bool) -> None:
        self.show_weak_results_button.setText(
            "Hide weak results"
            if visible
            else f"Show weak results ({len(self._weak_alternatives)})"
        )
        self._populate_alternatives(include_weak=visible)

    def _update_alternative_details(self) -> None:
        value = self.alternatives.currentData()
        selection_available = isinstance(value, LyricsAlternative)
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
