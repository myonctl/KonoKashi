"""PySide6 Widgets main window for the Stage 7 Desktop MVP."""

from __future__ import annotations

from collections.abc import Callable
from html import escape

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lyricflow.application.desktop_state import (
    DesktopLyricGroup,
    DesktopLyricsState,
    DesktopViewState,
)
from lyricflow.domain.representations import RepresentationDisplaySettings


def _plain_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    return label


class ElidingLabel(QLabel):
    """Single-line plain-text label that never expands the window indefinitely."""

    def __init__(self, text: str = "") -> None:
        super().__init__()
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full_text = text
        # Qt tooltips auto-detect rich text, so escape untrusted metadata before
        # handing it to that surface while the painted label remains plain text.
        self.setToolTip(escape(text))
        self.update()

    def text(self) -> str:
        return self._full_text

    def paintEvent(self, event: QEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        metrics = QFontMetrics(self.font())
        text = metrics.elidedText(
            self._full_text,
            Qt.TextElideMode.ElideRight,
            max(0, self.contentsRect().width()),
        )
        painter.drawText(
            self.contentsRect(),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )


def _group_text(groups: tuple[DesktopLyricGroup, ...]) -> str:
    rendered: list[str] = []
    for group in groups:
        layers = tuple(
            text
            for text in (
                group.original,
                group.romanized_or_transliterated,
                group.translation,
            )
            if text is not None
        )
        if layers:
            rendered.append("\n".join(layers))
    return "\n\n".join(rendered)


class LyricBand(QLabel):
    """A reusable plain-text band for one semantic lyric context region."""

    def __init__(self, *, active: bool = False) -> None:
        super().__init__()
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByKeyboard)
        font = self.font()
        if active:
            font.setPointSizeF(max(18.0, font.pointSizeF() * 1.8))
            font.setWeight(QFont.Weight.DemiBold)
            self.setAccessibleName("Current lyric")
            self.setMinimumHeight(100)
        else:
            font.setPointSizeF(max(11.0, font.pointSizeF() * 1.1))
            self.setAccessibleName("Nearby lyric")
            palette = self.palette()
            palette.setColor(
                QPalette.ColorRole.WindowText,
                palette.color(QPalette.ColorRole.PlaceholderText),
            )
            self.setPalette(palette)
            self.setMinimumHeight(54)
        self.setFont(font)

    def set_groups(self, groups: tuple[DesktopLyricGroup, ...]) -> None:
        text = _group_text(groups)
        self.setText(text)
        self.setVisible(bool(text))


class RepresentationSettingsDialog(QDialog):
    """Small semantic editor for the existing shared display settings."""

    def __init__(
        self, settings: RepresentationDisplaySettings, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Lyric display")
        self.setModal(True)
        layout = QVBoxLayout(self)
        explanation = _plain_label(
            "Choose which aligned lyric layers are visible. Original lyrics are "
            "kept unchanged in storage."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.original = QCheckBox("Show original lyrics")
        self.romanized = QCheckBox("Show romanization or transliteration")
        self.translated = QCheckBox("Show translation when available")
        self.original.setChecked(settings.show_original)
        self.romanized.setChecked(settings.show_romanized)
        self.translated.setChecked(settings.show_translated)
        layout.addWidget(self.original)
        layout.addWidget(self.romanized)
        layout.addWidget(self.translated)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> RepresentationDisplaySettings:
        return RepresentationDisplaySettings(
            self.original.isChecked(),
            self.romanized.isChecked(),
            self.translated.isChecked(),
        )


class DiagnosticsDialog(QDialog):
    """Bounded details surface that keeps diagnostics out of the lyric view."""

    def __init__(self, state: DesktopViewState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LyricFlow details")
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setAccessibleName("LyricFlow diagnostic details")
        lines = [
            f"state: {state.state.value}",
            f"player: {state.player or 'unavailable'}",
            f"playback: {state.playback_state.value}",
            f"lyrics source: {state.lyrics_source or 'unavailable'}",
            f"match confidence: {state.match_confidence or 'unknown'}",
            "sync health: "
            + (state.sync_health.value if state.sync_health else "unavailable"),
            f"document display delay: {state.display_delay_us / 1000:+.0f} ms",
        ]
        if state.diagnostics:
            lines.extend(("", "Limitations / diagnostics:"))
            lines.extend(f"- {item}" for item in state.diagnostics[:20])
            if len(state.diagnostics) > 20:
                lines.append(f"- … {len(state.diagnostics) - 20} more")
        details.setPlainText("\n".join(lines))
        layout.addWidget(details)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    """Responsive, palette-aware main window driven only by application state."""

    settings_requested = Signal(object)

    def __init__(
        self,
        settings: RepresentationDisplaySettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or RepresentationDisplaySettings()
        self._state = DesktopViewState(DesktopLyricsState.WAITING, "Waiting for media…")
        self.setWindowTitle("LyricFlow")
        self.setMinimumSize(420, 420)
        self.resize(760, 720)

        root = QWidget()
        root.setAccessibleName("LyricFlow main view")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        header = QHBoxLayout()
        metadata = QVBoxLayout()
        self.title_label = ElidingLabel("LyricFlow")
        title_font = self.title_label.font()
        title_font.setPointSizeF(max(16.0, title_font.pointSizeF() * 1.45))
        title_font.setWeight(QFont.Weight.DemiBold)
        self.title_label.setFont(title_font)
        self.title_label.setAccessibleName("Track title")
        self.artist_label = ElidingLabel("")
        self.artist_label.setAccessibleName("Track artist")
        metadata.addWidget(self.title_label)
        metadata.addWidget(self.artist_label)
        header.addLayout(metadata, 1)
        self.settings_button = QPushButton("Settings")
        self.settings_button.setAccessibleName("Lyric display settings")
        self.settings_button.clicked.connect(self._open_settings)
        self.details_button = QPushButton("Details")
        self.details_button.setAccessibleName("Synchronization and source details")
        self.details_button.clicked.connect(self._open_details)
        header.addWidget(self.settings_button)
        header.addWidget(self.details_button)
        layout.addLayout(header)

        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(rule)

        self.status_label = _plain_label("Waiting for media…")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Lyrics status")
        layout.addWidget(self.status_label)

        self.previous_band = LyricBand()
        self.active_band = LyricBand(active=True)
        self.next_band = LyricBand()
        layout.addStretch(1)
        layout.addWidget(self.previous_band)
        layout.addWidget(self.active_band)
        layout.addWidget(self.next_band)

        self.static_lyrics = QPlainTextEdit()
        self.static_lyrics.setReadOnly(True)
        self.static_lyrics.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.static_lyrics.setAccessibleName("Untimed lyrics")
        self.static_lyrics.setVisible(False)
        layout.addWidget(self.static_lyrics, 1)
        layout.addStretch(1)

        progress_row = QHBoxLayout()
        self.playback_label = _plain_label("Unknown")
        self.playback_label.setAccessibleName("Playback state")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Playback progress")
        self.time_label = _plain_label("--:-- / --:--")
        self.time_label.setAccessibleName("Playback position and duration")
        progress_row.addWidget(self.playback_label)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.time_label)
        layout.addLayout(progress_row)

        self.source_label = _plain_label("")
        self.source_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.source_label.setWordWrap(True)
        self.source_label.setAccessibleName("Lyrics source and synchronization health")
        source_palette = self.source_label.palette()
        source_palette.setColor(
            QPalette.ColorRole.WindowText,
            source_palette.color(QPalette.ColorRole.PlaceholderText),
        )
        self.source_label.setPalette(source_palette)
        layout.addWidget(self.source_label)

        self.setCentralWidget(root)
        self.render_state(self._state)

    @property
    def state(self) -> DesktopViewState:
        return self._state

    @property
    def representation_settings(self) -> RepresentationDisplaySettings:
        return self._settings

    def set_representation_settings(
        self, settings: RepresentationDisplaySettings
    ) -> None:
        """Update the dialog baseline after shared settings load or save."""

        self._settings = settings

    def render_state(self, state: DesktopViewState) -> None:
        """Render one immutable semantic state without external side effects."""

        self._state = state
        self.title_label.setText(state.title or "LyricFlow")
        self.artist_label.setText(" · ".join(state.artists))
        self.artist_label.setVisible(bool(state.artists))
        self.status_label.setText(state.status_message)
        self.previous_band.set_groups(state.previous)
        self.active_band.set_groups(state.active)
        self.next_band.set_groups(state.next)
        self.static_lyrics.setPlainText(_group_text(state.static_lines))
        self.static_lyrics.setVisible(bool(state.static_lines))
        has_lyric_bands = bool(state.previous or state.active or state.next)
        self.previous_band.setVisible(bool(state.previous) and has_lyric_bands)
        self.active_band.setVisible(bool(state.active) and has_lyric_bands)
        self.next_band.setVisible(bool(state.next) and has_lyric_bands)
        self.playback_label.setText(state.playback_state.value)
        if state.progress_fraction is None:
            self.progress.setRange(0, 0)
            self.progress.setAccessibleDescription("Playback duration is unavailable")
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(round(state.progress_fraction * 1000))
            self.progress.setAccessibleDescription(
                f"{state.progress_fraction * 100:.0f} percent"
            )
        self.time_label.setText(
            f"{_time_text(state.position_us)} / {_time_text(state.duration_us)}"
        )
        source_parts = []
        if state.lyrics_source:
            source_parts.append(state.lyrics_source)
        if state.match_confidence:
            source_parts.append(state.match_confidence)
        if state.sync_health:
            source_parts.append(state.sync_health.value)
        self.source_label.setText(" · ".join(source_parts))
        self.source_label.setVisible(bool(source_parts))
        self.details_button.setEnabled(
            bool(source_parts or state.diagnostics or state.player)
        )

    def update_playback(self, state: DesktopViewState) -> None:
        """Refresh progress/status fields without rebuilding lyric layout."""

        self._state = state
        self.playback_label.setText(state.playback_state.value)
        if state.progress_fraction is None:
            self.progress.setRange(0, 0)
            self.progress.setAccessibleDescription("Playback duration is unavailable")
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(round(state.progress_fraction * 1000))
            self.progress.setAccessibleDescription(
                f"{state.progress_fraction * 100:.0f} percent"
            )
        self.time_label.setText(
            f"{_time_text(state.position_us)} / {_time_text(state.duration_us)}"
        )

    def _open_settings(self) -> None:
        dialog = RepresentationSettingsDialog(self._settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._settings = dialog.value()
            self.settings_requested.emit(self._settings)

    def _open_details(self) -> None:
        DiagnosticsDialog(self._state, self).exec()


def _time_text(value_us: int | None) -> str:
    if value_us is None or value_us < 0:
        return "--:--"
    total_seconds = value_us // 1_000_000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


SettingsHandler = Callable[[RepresentationDisplaySettings], None]
