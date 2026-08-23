"""Passive Stage 8 review dialog over frontend-neutral correction values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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

from lyricflow.application.review_corrections import ReviewCorrectionSnapshot
from lyricflow.domain.lyrics import LyricsAlternative


class CorrectionActionKind(Enum):
    """One deliberate mutation requested from the passive Qt dialog."""

    PUT_TRACK_OVERRIDE = "put-track-override"
    RESET_TRACK_OVERRIDE = "reset-track-override"
    APPROVE_CURRENT = "approve-current"
    REJECT_CURRENT = "reject-current"
    CHOOSE_ALTERNATIVE = "choose-alternative"
    RESET_MATCH = "reset-match"
    SET_DELAY = "set-delay"
    RESET_DELAY = "reset-delay"


@dataclass(frozen=True, slots=True)
class CorrectionActionRequest:
    """Plain values emitted to the coordinator for worker-owned application work."""

    kind: CorrectionActionKind
    title: str | None = None
    artists: tuple[str, ...] = ()
    alternative: LyricsAlternative | None = None
    delay_us: int | None = None


def _plain_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _artists(values: tuple[str, ...] | None) -> str:
    if values is None:
        return "<unavailable>"
    return " · ".join(values) if values else "<empty>"


class ReviewCorrectionDialog(QDialog):
    """Review raw evidence and request one explicit correction at a time."""

    def __init__(
        self, snapshot: ReviewCorrectionSnapshot, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._snapshot = snapshot
        self._action: CorrectionActionRequest | None = None
        self.setWindowTitle("Review and correct")
        self.resize(760, 680)
        root = QVBoxLayout(self)

        intro = _plain_label(
            "Corrections apply only to this stable source or exact lyric document. "
            "Raw player metadata, provider values, lyric text, timestamps, and audio "
            "tags remain unchanged."
        )
        root.addWidget(intro)

        metadata_group = QGroupBox("Resolved track")
        metadata_layout = QFormLayout(metadata_group)
        self.title_edit = QLineEdit(snapshot.track.effective_title or "")
        self.title_edit.setAccessibleName("Corrected track title")
        self.artists_edit = QLineEdit("; ".join(snapshot.track.effective_artists))
        self.artists_edit.setAccessibleName("Corrected track artists")
        self.artists_edit.setPlaceholderText(
            "Separate multiple artists with semicolons"
        )
        metadata_layout.addRow("Title", self.title_edit)
        metadata_layout.addRow("Artist(s)", self.artists_edit)
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

        lyrics_group = QGroupBox("Lyrics match")
        lyrics_layout = QVBoxLayout(lyrics_group)
        current = _plain_label(
            "Current: "
            + (snapshot.current_lyrics_source or "no lyric document")
            + " · "
            + (
                "unknown"
                if snapshot.current_match_confidence is None
                else snapshot.current_match_confidence.value
            )
            + " · "
            + (
                "no durable decision"
                if snapshot.current_match_decision is None
                else snapshot.current_match_decision.value
            )
        )
        lyrics_layout.addWidget(current)
        self.alternatives = QComboBox()
        self.alternatives.setAccessibleName("Alternative lyric results")
        for item in snapshot.alternatives:
            flags = []
            if item.current:
                flags.append("current")
            if item.rejected:
                flags.append("rejected")
            suffix = "" if not flags else f" [{', '.join(flags)}]"
            duration = (
                "unknown duration"
                if item.candidate.duration_ms is None
                else f"{item.candidate.duration_ms / 1000:.1f} s"
            )
            self.alternatives.addItem(
                f"{item.candidate.artist_name} — {item.candidate.track_name} · "
                f"{duration} · {item.confidence.value}{suffix}",
                item,
            )
        self.alternatives.setEnabled(bool(snapshot.alternatives))
        lyrics_layout.addWidget(self.alternatives)
        match_buttons = QHBoxLayout()
        self.choose_button = QPushButton("Choose alternative")
        self.approve_button = QPushButton("Approve current")
        self.reject_button = QPushButton("Reject current")
        self.reset_match_button = QPushButton("Reset match decision")
        has_document = snapshot.current_document_id is not None
        self.choose_button.setEnabled(snapshot.durable and bool(snapshot.alternatives))
        self.approve_button.setEnabled(snapshot.durable and has_document)
        self.reject_button.setEnabled(snapshot.durable and has_document)
        self.reset_match_button.setEnabled(
            snapshot.durable and snapshot.current_match_decision is not None
        )
        self.choose_button.clicked.connect(self._choose_alternative)
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
            self.approve_button,
            self.reject_button,
            self.reset_match_button,
        ):
            match_buttons.addWidget(button)
        lyrics_layout.addLayout(match_buttons)
        root.addWidget(lyrics_group)

        delay_group = QGroupBox("Recording lyric timing")
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

        audit = QPlainTextEdit()
        audit.setReadOnly(True)
        audit.setAccessibleName("Raw metadata and interpretation evidence")
        audit.setPlainText(self._audit_text(snapshot))
        root.addWidget(audit, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def action(self) -> CorrectionActionRequest | None:
        """Return the single deliberate action selected before the dialog closed."""

        return self._action

    def _save_track(self) -> None:
        artists = tuple(
            item.strip() for item in self.artists_edit.text().split(";") if item.strip()
        )
        self._finish(
            CorrectionActionKind.PUT_TRACK_OVERRIDE,
            title=self.title_edit.text().strip(),
            artists=artists,
        )

    def _choose_alternative(self) -> None:
        value = self.alternatives.currentData()
        if isinstance(value, LyricsAlternative):
            self._finish(
                CorrectionActionKind.CHOOSE_ALTERNATIVE,
                alternative=value,
            )

    def _finish(
        self,
        kind: CorrectionActionKind,
        *,
        title: str | None = None,
        artists: tuple[str, ...] = (),
        alternative: LyricsAlternative | None = None,
        delay_us: int | None = None,
    ) -> None:
        self._action = CorrectionActionRequest(
            kind,
            title=title,
            artists=artists,
            alternative=alternative,
            delay_us=delay_us,
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
        ]
        if snapshot.diagnostics:
            lines.extend(
                ("Diagnostics:", *(f"- {item}" for item in snapshot.diagnostics))
            )
        return "\n".join(lines)
