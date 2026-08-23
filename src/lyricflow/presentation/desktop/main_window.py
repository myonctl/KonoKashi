"""PySide6 Widgets main window for the Stage 7 Desktop MVP."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from math import sqrt

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPalette, QResizeEvent
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
from lyricflow.application.settings import DesktopInteractionSettings
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
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return self._full_text

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self.font())
        return QSize(0, metrics.lineSpacing() + 6)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event: QEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setFont(self.font())
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


def _lyric_layer_label(accessible_name: str) -> QLabel:
    label = _plain_label()
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    label.setAccessibleName(accessible_name)
    return label


class _LyricGroupWidget(QWidget):
    """Three reusable plain-text labels for one aligned representation group."""

    def __init__(self, *, active: bool) -> None:
        super().__init__()
        self._active = active
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        region = "current" if active else "nearby"
        self.original = _lyric_layer_label(f"Original {region} lyric")
        self.romanized = _lyric_layer_label(f"Romanized {region} lyric")
        self.translation = _lyric_layer_label(f"Translated {region} lyric")
        layout.addWidget(self.original)
        layout.addWidget(self.romanized)
        layout.addWidget(self.translation)

    def set_group(self, group: DesktopLyricGroup) -> None:
        for label, text in (
            (self.original, group.original),
            (self.romanized, group.romanized_or_transliterated),
            (self.translation, group.translation),
        ):
            label.setText(text or "")
            label.setVisible(text is not None)

    def set_point_size(self, original_point_size: float) -> None:
        for label, point_size, weight in (
            (
                self.original,
                original_point_size,
                QFont.Weight.DemiBold if self._active else QFont.Weight.Normal,
            ),
            (self.romanized, original_point_size * 0.78, QFont.Weight.Normal),
            (self.translation, original_point_size * 0.7, QFont.Weight.Normal),
        ):
            font = label.font()
            font.setPointSizeF(point_size)
            font.setWeight(weight)
            label.setFont(font)

    def set_selection_enabled(self, enabled: bool) -> None:
        for label in (self.original, self.romanized, self.translation):
            label.setTextInteractionFlags(
                (
                    Qt.TextInteractionFlag.TextSelectableByMouse
                    | Qt.TextInteractionFlag.TextSelectableByKeyboard
                )
                if enabled
                else Qt.TextInteractionFlag.NoTextInteraction
            )
            label.setCursor(
                Qt.CursorShape.IBeamCursor if enabled else Qt.CursorShape.ArrowCursor
            )


class LyricBand(QWidget):
    """A reusable plain-text band for one semantic lyric context region."""

    _MAX_VISIBLE_GROUPS = 8

    def __init__(self, *, active: bool = False) -> None:
        super().__init__()
        self._active = active
        self._groups: tuple[DesktopLyricGroup, ...] = ()
        self._group_widgets: list[_LyricGroupWidget] = []
        self._selection_enabled = False
        initial_point_size = self.font().pointSizeF()
        self._point_size = initial_point_size if initial_point_size > 0 else 10.0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8 if active else 4)
        self.set_selection_enabled(False)
        if active:
            self.setAccessibleName("Current lyric")
        else:
            self.setAccessibleName("Nearby lyric")
            palette = self.palette()
            palette.setColor(
                QPalette.ColorRole.WindowText,
                palette.color(QPalette.ColorRole.PlaceholderText),
            )
            self.setPalette(palette)

    def set_responsive_size(self, point_size: float, minimum_height: int) -> None:
        """Apply one logical-size typography update without changing content."""

        if (
            abs(point_size - self._point_size) < 0.01
            and minimum_height == self.minimumHeight()
        ):
            return
        font = self.font()
        font.setPointSizeF(point_size)
        font.setWeight(QFont.Weight.DemiBold if self._active else QFont.Weight.Normal)
        self.setFont(font)
        self._point_size = point_size
        self.setMinimumHeight(minimum_height)
        for widget in self._group_widgets:
            widget.set_point_size(point_size)

    def set_groups(self, groups: tuple[DesktopLyricGroup, ...]) -> None:
        self._groups = groups
        self._render_groups()

    def set_selection_enabled(self, enabled: bool) -> None:
        """Make copying lyrics an explicit opt-in interaction mechanic."""

        self._selection_enabled = enabled
        self.setCursor(
            Qt.CursorShape.IBeamCursor if enabled else Qt.CursorShape.ArrowCursor
        )
        for widget in self._group_widgets:
            widget.set_selection_enabled(enabled)

    def text(self) -> str:
        """Expose the rendered plain text for accessibility-oriented tests."""

        return _group_text(self._groups)

    def textInteractionFlags(self) -> Qt.TextInteractionFlag:
        if self._selection_enabled:
            return (
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
        return Qt.TextInteractionFlag.NoTextInteraction

    @staticmethod
    def textFormat() -> Qt.TextFormat:
        return Qt.TextFormat.PlainText

    @staticmethod
    def openExternalLinks() -> bool:
        return False

    def _render_groups(self) -> None:
        visible_groups = self._groups[: self._MAX_VISIBLE_GROUPS]
        while len(self._group_widgets) < len(visible_groups):
            widget = _LyricGroupWidget(active=self._active)
            widget.set_point_size(self._point_size)
            widget.set_selection_enabled(self._selection_enabled)
            self._layout.addWidget(widget)
            self._group_widgets.append(widget)
        for index, widget in enumerate(self._group_widgets):
            if index < len(visible_groups):
                widget.set_group(visible_groups[index])
                widget.setVisible(True)
            else:
                widget.setVisible(False)
        self.setVisible(bool(_group_text(visible_groups)))


@dataclass(frozen=True, slots=True)
class DesktopSettingsUpdate:
    """One settings-dialog result spanning content and interaction policy."""

    representations: RepresentationDisplaySettings
    interactions: DesktopInteractionSettings


class RepresentationSettingsDialog(QDialog):
    """Small semantic editor for the existing shared display settings."""

    def __init__(
        self,
        settings: RepresentationDisplaySettings,
        parent: QWidget | None = None,
        *,
        interaction_settings: DesktopInteractionSettings | None = None,
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
        self.lyric_selection = QCheckBox("Allow lyric text selection")
        self.lyric_selection.setChecked(
            (interaction_settings or DesktopInteractionSettings()).allow_lyric_selection
        )
        self.lyric_selection.setToolTip(
            "When enabled, lyric lines use an I-beam cursor and can be selected."
        )
        layout.addWidget(self.lyric_selection)
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

    def interaction_value(self) -> DesktopInteractionSettings:
        return DesktopInteractionSettings(self.lyric_selection.isChecked())


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
        *,
        interaction_settings: DesktopInteractionSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or RepresentationDisplaySettings()
        self._interaction_settings = (
            interaction_settings or DesktopInteractionSettings()
        )
        self._state = DesktopViewState(DesktopLyricsState.WAITING, "Waiting for media…")
        system_point_size = self.font().pointSizeF()
        self._base_point_size = system_point_size if system_point_size > 0 else 10.0
        self._pending_typography_size = QSize(760, 720)
        self._applied_typography_scale: float | None = None
        self._resize_typography_timer = QTimer(self)
        self._resize_typography_timer.setSingleShot(True)
        self._resize_typography_timer.setInterval(80)
        self._resize_typography_timer.timeout.connect(self._finish_resize_typography)
        self.setWindowTitle("LyricFlow")
        self.setMinimumSize(420, 420)
        self.resize(760, 720)

        root = QWidget()
        root.setAccessibleName("LyricFlow main view")
        layout = QVBoxLayout(root)
        self._root_layout = layout

        header = QHBoxLayout()
        metadata = QVBoxLayout()
        self.title_label = ElidingLabel("LyricFlow")
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
        self._apply_interaction_settings()
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
        self._apply_responsive_typography(
            self._responsive_scale(self.width(), self.height())
        )
        self.render_state(self._state)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Scale lyric presentation with logical window area, not device pixels."""

        super().resizeEvent(event)
        self._pending_typography_size = event.size()
        self._resize_typography_timer.start()

    @staticmethod
    def _responsive_scale(width: int, height: int) -> float:
        # The geometric mean responds to either useful width or height while
        # avoiding runaway sizes on an ultrawide or very tall window. Qt maps
        # these point sizes to the current screen's device-pixel ratio.
        area_ratio = max(1, width * height) / (760 * 720)
        return min(2.2, max(0.8, sqrt(area_ratio)))

    def _finish_resize_typography(self) -> None:
        size = self._pending_typography_size
        exact_scale = self._responsive_scale(size.width(), size.height())
        if (
            self._applied_typography_scale is None
            or abs(exact_scale - self._applied_typography_scale) >= 0.002
        ):
            self._apply_responsive_typography(exact_scale)

    def _apply_responsive_typography(self, scale: float) -> None:
        self._applied_typography_scale = scale
        body_size = self._base_point_size * scale

        title_font = self.title_label.font()
        title_font.setPointSizeF(max(13.0, self._base_point_size * 1.45 * scale))
        title_font.setWeight(QFont.Weight.DemiBold)
        self.title_label.setFont(title_font)

        for label in (
            self.artist_label,
            self.status_label,
            self.playback_label,
            self.time_label,
            self.source_label,
        ):
            font = label.font()
            font.setPointSizeF(max(8.0, body_size))
            label.setFont(font)

        for button in (self.settings_button, self.details_button):
            font = button.font()
            font.setPointSizeF(max(8.0, body_size))
            button.setFont(font)

        static_font = self.static_lyrics.font()
        static_font.setPointSizeF(max(9.0, self._base_point_size * 1.1 * scale))
        self.static_lyrics.setFont(static_font)
        self.previous_band.set_responsive_size(
            max(9.0, self._base_point_size * 1.1 * scale),
            round(54 * scale),
        )
        self.active_band.set_responsive_size(
            max(15.0, self._base_point_size * 1.8 * scale),
            round(100 * scale),
        )
        self.next_band.set_responsive_size(
            max(9.0, self._base_point_size * 1.1 * scale),
            round(54 * scale),
        )
        margin_x = round(28 * min(1.6, scale))
        margin_y = round(24 * min(1.6, scale))
        self._root_layout.setContentsMargins(margin_x, margin_y, margin_x, margin_y)
        self._root_layout.setSpacing(round(14 * min(1.6, scale)))

    @property
    def state(self) -> DesktopViewState:
        return self._state

    @property
    def representation_settings(self) -> RepresentationDisplaySettings:
        return self._settings

    @property
    def interaction_settings(self) -> DesktopInteractionSettings:
        return self._interaction_settings

    def set_representation_settings(
        self, settings: RepresentationDisplaySettings
    ) -> None:
        """Update the dialog baseline after shared settings load or save."""

        self._settings = settings

    def set_interaction_settings(self, settings: DesktopInteractionSettings) -> None:
        """Update and apply the persisted interaction mechanics."""

        self._interaction_settings = settings
        self._apply_interaction_settings()

    def _apply_interaction_settings(self) -> None:
        enabled = self._interaction_settings.allow_lyric_selection
        for band in (self.previous_band, self.active_band, self.next_band):
            band.set_selection_enabled(enabled)
        if enabled:
            self.static_lyrics.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
            self.static_lyrics.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        else:
            self.static_lyrics.setTextInteractionFlags(
                Qt.TextInteractionFlag.NoTextInteraction
            )
            self.static_lyrics.viewport().setCursor(Qt.CursorShape.ArrowCursor)

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
        dialog = RepresentationSettingsDialog(
            self._settings,
            self,
            interaction_settings=self._interaction_settings,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._settings = dialog.value()
            self._interaction_settings = dialog.interaction_value()
            self._apply_interaction_settings()
            self.settings_requested.emit(
                DesktopSettingsUpdate(self._settings, self._interaction_settings)
            )

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
