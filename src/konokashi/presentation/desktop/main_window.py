"""PySide6 Widgets main window for the KonoKashi desktop."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QKeySequence,
    QPainter,
    QPalette,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QBoxLayout,
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
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from konokashi.application.appearance import (
    AppearanceProfile,
    TextAlignment,
    TextStyle,
    default_appearance_profile,
)
from konokashi.application.desktop_state import (
    DesktopLyricGroup,
    DesktopLyricsState,
    DesktopViewState,
)
from konokashi.application.review_corrections import ReviewCorrectionSnapshot
from konokashi.application.settings import DesktopInteractionSettings
from konokashi.domain.representations import RepresentationDisplaySettings
from konokashi.domain.synchronization import ClockHealth, PlaybackState
from konokashi.presentation.desktop.review_dialog import (
    CorrectionActionRequest,
    ReviewCorrectionDialog,
)


def _plain_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    return label


def _qt_alignment(value: TextAlignment) -> Qt.AlignmentFlag:
    return {
        TextAlignment.LEFT: Qt.AlignmentFlag.AlignLeft,
        TextAlignment.CENTER: Qt.AlignmentFlag.AlignCenter,
        TextAlignment.RIGHT: Qt.AlignmentFlag.AlignRight,
    }[value] | Qt.AlignmentFlag.AlignVCenter


def _semantic_color(value: str, opacity: int = 100) -> QColor:
    color = QColor(value[:7])
    alpha = int(value[7:9], 16) if len(value) == 9 else 255
    color.setAlpha(round(alpha * opacity / 100))
    return color


def _apply_text_palette(widget: QWidget, color: QColor) -> None:
    palette = QPalette(widget.palette())
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text):
        palette.setColor(role, color)
    widget.setPalette(palette)


def _styled_font(widget: QWidget, style: TextStyle, scale: float) -> QFont:
    font = QFont(widget.font())
    if style.family:
        # QFont retains the requested family and lets Qt/fontconfig provide a
        # multilingual fallback when it is absent or lacks a glyph.
        font.setFamily(style.family)
    font.setPointSizeF(max(1.0, style.size * scale))
    font.setWeight(QFont.Weight(style.weight))
    font.setItalic(style.italic)
    return font


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
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._active = active
        self._appearance = default_appearance_profile()
        self._scale = 1.0
        layout = QVBoxLayout(self)
        self._layout = layout
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

    def apply_appearance(self, appearance: AppearanceProfile, scale: float) -> None:
        self._appearance = appearance
        self._scale = scale
        emphasis = (
            appearance.active_size_percent
            if self._active
            else appearance.inactive_size_percent
        ) / 100
        for label, style, color, opacity in (
            (
                self.original,
                appearance.original,
                appearance.colors.active_lyric
                if self._active
                else appearance.colors.inactive_lyric,
                100,
            ),
            (
                self.romanized,
                appearance.romanization,
                appearance.colors.romanization,
                appearance.opacity.secondary_representation,
            ),
            (
                self.translation,
                appearance.translation,
                appearance.colors.translation,
                appearance.opacity.secondary_representation,
            ),
        ):
            label.setFont(_styled_font(label, style, scale * emphasis))
            semantic_color = _semantic_color(
                color, round(opacity * appearance.opacity.content / 100)
            )
            if not self._active:
                semantic_color.setAlpha(
                    round(
                        semantic_color.alpha() * appearance.opacity.inactive_line / 100
                    )
                )
            _apply_text_palette(label, semantic_color)
            label.setAlignment(_qt_alignment(appearance.lyric_alignment))
        self._layout.setSpacing(appearance.spacing.representation)

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
        self._appearance = default_appearance_profile()
        self._scale = 1.0
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
            self.setForegroundRole(QPalette.ColorRole.PlaceholderText)

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
            widget.apply_appearance(self._appearance, self._scale)

    def apply_appearance(self, appearance: AppearanceProfile, scale: float) -> None:
        """Recompute derived visual state only when appearance/size changes."""

        self._appearance = appearance
        self._scale = scale
        self._layout.setSpacing(appearance.spacing.line)
        self._layout.setContentsMargins(
            appearance.spacing.lyric_padding,
            appearance.spacing.lyric_padding,
            appearance.spacing.lyric_padding,
            appearance.spacing.lyric_padding,
        )
        for widget in self._group_widgets:
            widget.apply_appearance(appearance, scale)

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
            widget.apply_appearance(self._appearance, self._scale)
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


class DiagnosticsDialog(QDialog):
    """Bounded details surface that keeps diagnostics out of the lyric view."""

    def __init__(self, state: DesktopViewState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("KonoKashi details")
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setAccessibleName("KonoKashi diagnostic details")
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

    settings_requested = Signal()
    review_requested = Signal()
    correction_requested = Signal(object)
    library_scan_requested = Signal()
    library_scan_cancel_requested = Signal()

    def __init__(
        self,
        settings: RepresentationDisplaySettings | None = None,
        parent: QWidget | None = None,
        *,
        interaction_settings: DesktopInteractionSettings | None = None,
        appearance: AppearanceProfile | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or RepresentationDisplaySettings()
        self._interaction_settings = (
            interaction_settings or DesktopInteractionSettings()
        )
        self._appearance = appearance or default_appearance_profile()
        self._state = DesktopViewState(DesktopLyricsState.WAITING, "Waiting for media…")
        system_point_size = self.font().pointSizeF()
        self._base_point_size = system_point_size if system_point_size > 0 else 10.0
        self._pending_typography_size = QSize(760, 720)
        self._applied_typography_scale: float | None = None
        self._resize_typography_timer = QTimer(self)
        self._resize_typography_timer.setSingleShot(True)
        self._resize_typography_timer.setInterval(80)
        self._resize_typography_timer.timeout.connect(self._finish_resize_typography)
        self.setWindowTitle("KonoKashi")
        self.setMinimumSize(420, 420)
        self.resize(760, 720)

        root = QWidget()
        root.setAccessibleName("KonoKashi main view")
        layout = QVBoxLayout(root)
        self._root_layout = layout

        header = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._header_layout = header
        metadata_widget = QWidget()
        self._metadata_widget = metadata_widget
        metadata = QVBoxLayout(metadata_widget)
        self._metadata_layout = metadata
        metadata.setContentsMargins(0, 0, 0, 0)
        self.title_label = ElidingLabel("KonoKashi")
        self.title_label.setAccessibleName("Track title")
        self.artist_label = ElidingLabel("")
        self.artist_label.setAccessibleName("Track artist")
        metadata.addWidget(self.title_label)
        metadata.addWidget(self.artist_label)
        header.addWidget(metadata_widget, 1)
        actions_widget = QWidget()
        self._actions_widget = actions_widget
        actions = QHBoxLayout(actions_widget)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        self.settings_button = QPushButton("Settings")
        self.settings_button.setAccessibleName("KonoKashi settings")
        self.settings_button.setToolTip("Open KonoKashi settings (Ctrl+,)")
        self.settings_button.clicked.connect(self._open_settings)
        self.review_button = QPushButton("Review")
        self.review_button.setAccessibleName("Review and correct this track and lyrics")
        self.review_button.setToolTip("Review the detected track and lyrics match")
        self.review_button.clicked.connect(self.review_requested)
        self.details_button = QPushButton("Details")
        self.details_button.setAccessibleName("Synchronization and source details")
        self.details_button.setToolTip("Show source and synchronization details")
        self.details_button.clicked.connect(self._open_details)
        self.library_button = QPushButton("Scan library")
        self.library_button.setAccessibleName("Scan configured music library")
        self.library_button.setToolTip("Scan the configured music folders")
        self.library_button.clicked.connect(self._toggle_library_scan)
        for button in (self.review_button, self.details_button, self.library_button):
            button.setFlat(True)
        actions.addWidget(self.settings_button)
        actions.addWidget(self.review_button)
        actions.addWidget(self.details_button)
        actions.addWidget(self.library_button)
        header.addWidget(actions_widget)
        layout.addLayout(header)

        rule = QFrame()
        self._header_rule = rule
        rule.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(rule)

        self.status_label = _plain_label("Waiting for media…")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Lyrics status")

        self.content_stack = QStackedWidget()
        self.content_stack.setAccessibleName("Lyrics content")
        state_page = QWidget()
        state_layout = QVBoxLayout(state_page)
        state_layout.setContentsMargins(0, 0, 0, 0)
        state_layout.addStretch(1)
        state_layout.addWidget(self.status_label)
        state_layout.addStretch(1)
        self.content_stack.addWidget(state_page)
        self._state_page = state_page

        self.previous_band = LyricBand()
        self.active_band = LyricBand(active=True)
        self.next_band = LyricBand()
        timed_page = QWidget()
        timed_page_layout = QHBoxLayout(timed_page)
        timed_page_layout.setContentsMargins(0, 0, 0, 0)
        timed_page_layout.addStretch(1)
        lyric_column = QWidget()
        lyric_column.setMaximumWidth(1_040)
        lyric_column.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        lyric_layout = QVBoxLayout(lyric_column)
        self._lyric_layout = lyric_layout
        lyric_layout.setContentsMargins(0, 0, 0, 0)
        lyric_layout.addStretch(1)
        lyric_layout.addWidget(self.previous_band)
        lyric_layout.addWidget(self.active_band)
        lyric_layout.addWidget(self.next_band)
        lyric_layout.addStretch(1)
        timed_page_layout.addWidget(lyric_column, 100)
        timed_page_layout.addStretch(1)
        self.content_stack.addWidget(timed_page)
        self._timed_page = timed_page
        self._lyric_column = lyric_column

        self.static_lyrics = QPlainTextEdit()
        self.static_lyrics.setReadOnly(True)
        self.static_lyrics.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.static_lyrics.setAccessibleName("Untimed lyrics")
        self.static_lyrics.setFrameShape(QFrame.Shape.NoFrame)
        self._apply_interaction_settings()
        static_page = QWidget()
        static_page_layout = QVBoxLayout(static_page)
        static_page_layout.setContentsMargins(0, 0, 0, 0)
        self.static_status_label = _plain_label("Lyrics without timing")
        self.static_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.static_status_label.setAccessibleName("Untimed lyrics status")
        static_page_layout.addWidget(self.static_status_label)
        static_row = QHBoxLayout()
        static_row.addStretch(1)
        self.static_lyrics.setMaximumWidth(920)
        static_row.addWidget(self.static_lyrics, 100)
        static_row.addStretch(1)
        static_page_layout.addLayout(static_row, 1)
        self.content_stack.addWidget(static_page)
        self._static_page = static_page
        layout.addWidget(self.content_stack, 1)

        self.playback_widget = QWidget()
        progress_row = QHBoxLayout(self.playback_widget)
        self._progress_layout = progress_row
        progress_row.setContentsMargins(0, 0, 0, 0)
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
        layout.addWidget(self.playback_widget)

        self.source_label = _plain_label("")
        self.source_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.source_label.setWordWrap(True)
        self.source_label.setAccessibleName("Lyrics source and synchronization health")
        self.source_label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        layout.addWidget(self.source_label)

        self.settings_action = QAction("Settings", self)
        self.settings_action.setShortcut(QKeySequence("Ctrl+,"))
        self.settings_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.settings_action.triggered.connect(self._open_settings)
        self.addAction(self.settings_action)

        self.setCentralWidget(root)
        self._apply_responsive_typography(
            self._responsive_scale(self.width(), self.height())
        )
        self.set_appearance_profile(self._appearance)
        self.render_state(self._state)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Scale lyric presentation with logical window area, not device pixels."""

        super().resizeEvent(event)
        self._set_header_direction(event.size().width())
        self._pending_typography_size = event.size()
        self._resize_typography_timer.start()

    def _set_header_direction(self, width: int) -> None:
        """Preserve track metadata when compact width cannot hold one row."""

        direction = (
            QBoxLayout.Direction.TopToBottom
            if width < 600
            else QBoxLayout.Direction.LeftToRight
        )
        if self._header_layout.direction() is not direction:
            self._header_layout.setDirection(direction)

    @staticmethod
    def _responsive_scale(width: int, height: int) -> float:
        # Both dimensions must provide useful room before text grows. Extreme
        # width alone improves neither reading distance nor line wrapping.
        useful_ratio = min(width / 760, height / 720)
        return min(1.65, max(0.82, useful_ratio))

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
        appearance = self._appearance
        self.title_label.setFont(
            _styled_font(self.title_label, appearance.metadata, scale)
        )
        self.artist_label.setFont(
            _styled_font(self.artist_label, appearance.metadata, scale)
        )
        for label in (
            self.status_label,
            self.static_status_label,
            self.playback_label,
            self.time_label,
            self.source_label,
        ):
            label.setFont(_styled_font(label, appearance.status, scale))

        for button in (
            self.settings_button,
            self.review_button,
            self.details_button,
            self.library_button,
        ):
            font = button.font()
            font.setPointSizeF(max(8.0, appearance.status.size * scale))
            button.setFont(font)

        self.static_lyrics.setFont(
            _styled_font(self.static_lyrics, appearance.original, scale)
        )
        self.previous_band.set_responsive_size(
            max(9.0, self._base_point_size * 1.05 * scale),
            round(54 * scale),
        )
        self.active_band.set_responsive_size(
            max(15.0, self._base_point_size * 1.75 * scale),
            round(100 * scale),
        )
        self.next_band.set_responsive_size(
            max(9.0, self._base_point_size * 1.05 * scale),
            round(54 * scale),
        )
        for band in (self.previous_band, self.active_band, self.next_band):
            band.apply_appearance(appearance, scale)
        margin = round(appearance.spacing.outer_margin * min(1.6, scale))
        self._root_layout.setContentsMargins(margin, margin, margin, margin)
        self._root_layout.setSpacing(
            round(appearance.spacing.progress * min(1.6, scale))
        )
        self._metadata_layout.setSpacing(appearance.spacing.metadata)
        self._progress_layout.setSpacing(appearance.spacing.progress)
        self._lyric_layout.setSpacing(appearance.spacing.context)
        self._lyric_column.setMaximumWidth(appearance.spacing.maximum_lyric_width)

    @property
    def state(self) -> DesktopViewState:
        return self._state

    @property
    def representation_settings(self) -> RepresentationDisplaySettings:
        return self._settings

    @property
    def interaction_settings(self) -> DesktopInteractionSettings:
        return self._interaction_settings

    @property
    def appearance_profile(self) -> AppearanceProfile:
        return self._appearance

    def set_representation_settings(
        self, settings: RepresentationDisplaySettings
    ) -> None:
        """Update the dialog baseline after shared settings load or save."""

        self._settings = settings

    def set_interaction_settings(self, settings: DesktopInteractionSettings) -> None:
        """Update and apply the persisted interaction mechanics."""

        self._interaction_settings = settings
        self._apply_interaction_settings()

    def set_appearance_profile(self, appearance: AppearanceProfile) -> None:
        """Apply one resolved semantic profile without touching playback state."""

        self._appearance = appearance
        scale = self._applied_typography_scale or self._responsive_scale(
            self.width(), self.height()
        )
        self._apply_responsive_typography(scale)

        palette = QPalette(self.palette())
        palette.setColor(
            QPalette.ColorRole.Window,
            _semantic_color(
                appearance.colors.background, appearance.opacity.background
            ),
        )
        palette.setColor(
            QPalette.ColorRole.WindowText,
            _semantic_color(appearance.colors.foreground, appearance.opacity.content),
        )
        palette.setColor(
            QPalette.ColorRole.Highlight,
            _semantic_color(appearance.colors.selection),
        )
        palette.setColor(
            QPalette.ColorRole.Link,
            _semantic_color(appearance.colors.accent),
        )
        self.setPalette(palette)
        central = self.centralWidget()
        if central is not None:
            central.setAutoFillBackground(True)
            central.setPalette(palette)

        _apply_text_palette(
            self.title_label,
            _semantic_color(
                appearance.colors.metadata_primary,
                round(appearance.opacity.metadata * appearance.opacity.content / 100),
            ),
        )
        _apply_text_palette(
            self.artist_label,
            _semantic_color(
                appearance.colors.metadata_secondary,
                round(appearance.opacity.metadata * appearance.opacity.content / 100),
            ),
        )
        for label in (self.status_label, self.static_status_label, self.playback_label):
            _apply_text_palette(
                label,
                _semantic_color(appearance.colors.status, appearance.opacity.content),
            )
        for label in (self.time_label, self.source_label):
            _apply_text_palette(
                label,
                _semantic_color(appearance.colors.muted, appearance.opacity.content),
            )
        _apply_text_palette(
            self.static_lyrics,
            _semantic_color(
                appearance.colors.original_lyric, appearance.opacity.content
            ),
        )
        progress_palette = QPalette(self.progress.palette())
        progress_palette.setColor(
            QPalette.ColorRole.Highlight,
            _semantic_color(appearance.colors.progress, appearance.opacity.content),
        )
        self.progress.setPalette(progress_palette)

        lyric_alignment = _qt_alignment(appearance.lyric_alignment)
        text_option = self.static_lyrics.document().defaultTextOption()
        text_option.setAlignment(lyric_alignment)
        self.static_lyrics.document().setDefaultTextOption(text_option)
        metadata_alignment = _qt_alignment(appearance.metadata_alignment)
        self.title_label.setAlignment(metadata_alignment)
        self.artist_label.setAlignment(metadata_alignment)
        self.render_state(self._state)

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
        self.title_label.setText(state.title or "KonoKashi")
        self.artist_label.setText(" · ".join(state.artists))
        self.artist_label.setVisible(bool(state.artists))
        self.status_label.setText(state.status_message)
        self.static_status_label.setText(state.status_message)
        appearance = self._appearance
        previous = (
            state.previous[-appearance.context.previous :]
            if appearance.visibility.inactive_context
            and appearance.context.previous > 0
            else ()
        )
        following = (
            state.next[: appearance.context.following]
            if appearance.visibility.inactive_context
            and appearance.context.following > 0
            else ()
        )
        self.previous_band.set_groups(previous)
        self.active_band.set_groups(state.active)
        self.next_band.set_groups(following)
        self.static_lyrics.setPlainText(_group_text(state.static_lines))
        has_lyric_bands = bool(previous or state.active or following)
        self.previous_band.setVisible(bool(previous) and has_lyric_bands)
        self.active_band.setVisible(bool(state.active) and has_lyric_bands)
        self.next_band.setVisible(bool(following) and has_lyric_bands)
        if state.state is DesktopLyricsState.UNTIMED and state.static_lines:
            self.content_stack.setCurrentWidget(self._static_page)
        elif state.state is DesktopLyricsState.TIMED and has_lyric_bands:
            self.content_stack.setCurrentWidget(self._timed_page)
        else:
            self.content_stack.setCurrentWidget(self._state_page)
        self.playback_label.setText(state.playback_state.value)
        if state.progress_fraction is None:
            self.progress.setRange(0, 1000)
            self.progress.setValue(0)
            self.progress.setVisible(False)
            self.progress.setAccessibleDescription("Playback duration is unavailable")
        else:
            self.progress.setVisible(True)
            self.progress.setRange(0, 1000)
            self.progress.setValue(round(state.progress_fraction * 1000))
            self.progress.setAccessibleDescription(
                f"{state.progress_fraction * 100:.0f} percent"
            )
        self.time_label.setText(
            f"{_time_text(state.position_us)} / {_time_text(state.duration_us)}"
        )
        self.playback_widget.setVisible(
            state.player is not None
            or state.playback_state is not PlaybackState.UNKNOWN
            or state.position_us is not None
        )
        source_parts = _normal_status_parts(state)
        self.source_label.setText(" · ".join(source_parts))
        self.source_label.setVisible(bool(source_parts))
        self.details_button.setEnabled(
            bool(source_parts or state.diagnostics or state.player)
        )
        self.review_button.setEnabled(
            bool(state.player and state.title)
            and state.state
            not in {
                DesktopLyricsState.WAITING,
                DesktopLyricsState.RESOLVING,
                DesktopLyricsState.ERROR,
            }
        )
        self._apply_visibility()

    def update_playback(self, state: DesktopViewState) -> None:
        """Refresh progress/status fields without rebuilding lyric layout."""

        self._state = state
        self.playback_label.setText(state.playback_state.value)
        if state.progress_fraction is None:
            self.progress.setRange(0, 1000)
            self.progress.setValue(0)
            self.progress.setVisible(False)
            self.progress.setAccessibleDescription("Playback duration is unavailable")
        else:
            self.progress.setVisible(True)
            self.progress.setRange(0, 1000)
            self.progress.setValue(round(state.progress_fraction * 1000))
            self.progress.setAccessibleDescription(
                f"{state.progress_fraction * 100:.0f} percent"
            )
        self.time_label.setText(
            f"{_time_text(state.position_us)} / {_time_text(state.duration_us)}"
        )
        self._apply_visibility()

    def _apply_visibility(self) -> None:
        """Project profile visibility without mutating semantic track state."""

        visibility = self._appearance.visibility
        state = self._state
        self.title_label.setVisible(visibility.title)
        self.artist_label.setVisible(visibility.artist and bool(state.artists))
        self._actions_widget.setVisible(visibility.chrome)
        self._header_rule.setVisible(
            visibility.chrome or visibility.title or visibility.artist
        )
        self._metadata_widget.setVisible(
            visibility.title or (visibility.artist and bool(state.artists))
        )
        self.status_label.setVisible(visibility.auxiliary_status)
        self.static_status_label.setVisible(visibility.auxiliary_status)
        self.playback_label.setVisible(visibility.playback_status)
        self.progress.setVisible(
            visibility.progress and state.progress_fraction is not None
        )
        self.time_label.setVisible(visibility.timestamps)
        has_playback = (
            state.player is not None
            or state.playback_state is not PlaybackState.UNKNOWN
            or state.position_us is not None
        )
        self.playback_widget.setVisible(
            has_playback
            and (
                visibility.playback_status
                or visibility.progress
                or visibility.timestamps
            )
        )
        self.source_label.setVisible(
            visibility.source and bool(_normal_status_parts(state))
        )

    def _open_settings(self) -> None:
        self.settings_requested.emit()

    def _open_details(self) -> None:
        DiagnosticsDialog(self._state, self).exec()

    def _toggle_library_scan(self) -> None:
        if self.library_button.property("scanRunning"):
            self.library_scan_cancel_requested.emit()
        else:
            self.library_scan_requested.emit()

    def set_library_scan_state(self, running: bool, message: str) -> None:
        """Expose background scan/cancel state without replacing lyric content."""

        self.library_button.setProperty("scanRunning", running)
        self.library_button.setText("Cancel scan" if running else "Scan library")
        self.library_button.setToolTip(escape(message))
        self.library_button.setAccessibleDescription(message)

    def show_review(self, snapshot: ReviewCorrectionSnapshot) -> None:
        """Render one source-bound review model and emit at most one action."""

        dialog = ReviewCorrectionDialog(snapshot, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            action = dialog.action()
            if isinstance(action, CorrectionActionRequest):
                self.correction_requested.emit(action)


def _time_text(value_us: int | None) -> str:
    if value_us is None or value_us < 0:
        return "--:--"
    total_seconds = value_us // 1_000_000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


def _normal_status_parts(state: DesktopViewState) -> tuple[str, ...]:
    parts: list[str] = []
    source = state.lyrics_source
    if source:
        normalized_source = source.casefold()
        if "local" in normalized_source or "sidecar" in normalized_source:
            parts.append("Local lyrics")
        elif "lrclib" in normalized_source:
            parts.append("Lyrics from LRCLIB")
        elif "embedded" in normalized_source:
            parts.append("Embedded lyrics")
        else:
            parts.append(source)
    confidence = state.match_confidence
    if confidence:
        parts.append(
            {
                "Approved": "Approved match",
                "High": "High-confidence match",
                "Medium": "Review suggested",
                "Low": "Uncertain match",
                "Rejected": "Rejected match",
            }.get(confidence, confidence)
        )
    health = state.sync_health
    if health and health is not ClockHealth.PAUSED:
        parts.append(
            {
                ClockHealth.LOCKED: "In sync",
                ClockHealth.CONVERGING: "Syncing",
                ClockHealth.DEGRADED: "Sync needs attention",
                ClockHealth.STALE: "Sync is stale",
                ClockHealth.UNAVAILABLE: "Sync unavailable",
                ClockHealth.DISCONTINUITY: "Resynchronizing",
            }[health]
        )
    return tuple(parts)
