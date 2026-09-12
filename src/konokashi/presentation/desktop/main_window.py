"""PySide6 Widgets main window for the KonoKashi desktop."""

from __future__ import annotations

from html import escape
from types import MappingProxyType
from typing import cast

from PySide6.QtCore import (
    Property,
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QHideEvent,
    QImage,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QResizeEvent,
    QTextCharFormat,
    QTextLayout,
    QTextLine,
    QTextOption,
)
from PySide6.QtWidgets import (
    QBoxLayout,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from konokashi.application.appearance import (
    AppearanceProfile,
    TextAlignment,
    TextStyle,
    color_contrast_ratio,
    contrast_safe_artwork_tint,
    default_appearance_profile,
)
from konokashi.application.artwork import ArtworkAsset
from konokashi.application.desktop_state import (
    DesktopKaraokeSegment,
    DesktopLyricGroup,
    DesktopLyricsState,
    DesktopViewState,
)
from konokashi.application.review_corrections import ReviewCorrectionSnapshot
from konokashi.application.settings import DesktopInteractionSettings
from konokashi.domain.library import LibraryReviewItem, LibraryScanSummary
from konokashi.domain.representations import RepresentationDisplaySettings
from konokashi.domain.synchronization import ClockHealth, PlaybackState
from konokashi.presentation.desktop.application_menu import ApplicationMenu
from konokashi.presentation.desktop.library_review_dialog import LibraryReviewDialog
from konokashi.presentation.desktop.progress import PlaybackProgress
from konokashi.presentation.desktop.review_dialog import (
    CorrectionActionRequest,
    ReviewCorrectionDialog,
)
from konokashi.presentation.desktop.window_surface import (
    DesktopWindowMode,
    DesktopWindowSurface,
)
from konokashi.presentation.desktop.workspace import DesktopWorkspace, PanelId

# Contract inventory: every public appearance key must terminate in a concrete
# desktop widget/property or in profile resolution. Tests compare this exact map
# with SETTINGS_SCHEMA so a newly exposed but unprojected control fails loudly.
DESKTOP_APPEARANCE_TARGETS = MappingProxyType(
    {
        "appearance.preset": "resolved AppearanceProfile",
        "appearance.theme": "resolved color system",
        "appearance.artwork.visible": "bounded local album-art surface",
        "appearance.artwork.dynamic_background": "contrast-safe window tint",
        **{
            f"appearance.typography.{layer}.{attribute}": target
            for layer, target in (
                ("original", "original/static lyric labels"),
                ("romanization", "romanization lyric labels"),
                ("translation", "translation lyric labels"),
                ("metadata", "title, artist, and album labels"),
                ("status", "status, time, source, and action labels"),
            )
            for attribute in ("family", "size", "weight")
        },
        **{
            f"appearance.typography.{layer}.italic": f"{layer} lyric labels"
            for layer in ("original", "romanization", "translation")
        },
        "appearance.typography.active_size_percent": "active lyric labels",
        "appearance.typography.inactive_size_percent": "context lyric labels",
        "appearance.typography.lyric_scale_percent": "all lyric labels",
        **{
            f"appearance.colors.{name}": target
            for name, target in (
                ("active_lyric", "active lyric labels"),
                ("inactive_lyric", "context lyric labels"),
                ("original_lyric", "untimed original lyric document"),
                ("romanization", "romanization lyric labels"),
                ("translation", "translation lyric labels"),
                ("metadata_primary", "title label"),
                ("metadata_secondary", "artist and album labels"),
                ("background", "window surface"),
                ("foreground", "window foreground palette"),
                ("accent", "application menu and link palette"),
                ("progress", "progress fill"),
                ("status", "status and playback labels"),
                ("muted", "time and source labels"),
                ("selection", "lyric selection palette"),
            )
        },
        **{
            f"appearance.opacity.{name}": target
            for name, target in (
                ("content", "all content palettes"),
                ("background", "window surface alpha"),
                ("inactive_line", "context lyric labels"),
                ("metadata", "title, artist, and album labels"),
                ("secondary_representation", "romanization and translation labels"),
            )
        },
        **{
            f"appearance.spacing.{name}": target
            for name, target in (
                ("outer_margin", "workspace margins"),
                ("lyric_padding", "lyric band padding"),
                ("line", "lyric group spacing"),
                ("representation", "aligned representation spacing"),
                ("metadata", "metadata column spacing"),
                ("progress", "progress row and panel spacing"),
                ("context", "timestamp-context spacing"),
                ("maximum_lyric_width", "lyric viewport maximum width"),
            )
        },
        "appearance.context.previous": "previous timestamp-group slice",
        "appearance.context.following": "following timestamp-group slice",
        "appearance.alignment.lyrics": "timed and untimed lyric alignment",
        "appearance.alignment.metadata": "title, artist, and album alignment",
        **{
            f"appearance.visibility.{name}": target
            for name, target in (
                ("title", "title label"),
                ("artist", "artist label"),
                ("album", "album label"),
                ("source", "source label"),
                ("playback_status", "playback label"),
                ("progress", "progress bar"),
                ("timestamps", "time label"),
                ("inactive_context", "previous and following lyric bands"),
                ("auxiliary_status", "state and untimed status labels"),
                ("chrome", "header action widget"),
            )
        },
        "appearance.motion.transition_ms": "measured lyric movement",
        "appearance.motion.emphasis_transition_ms": "active lyric emphasis",
        "appearance.motion.smooth_scrolling": "lyric transition mode",
        "appearance.motion.reduced": "all lyric animation durations",
        "appearance.progress.thickness": "progress bar height",
        "appearance.progress.track_color": "unfilled progress track",
        "appearance.progress.opacity": "progress fill and track alpha",
        "appearance.progress.corner_radius": "progress fill and track corners",
    }
)


def _plain_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    return label


class _OverlayDragHandle(QLabel):
    """Explicit compositor-owned drag affordance for a frameless overlay."""

    move_requested = Signal()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton:
            self.move_requested.emit()
            event.accept()
            return
        super().mousePressEvent(event)


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


class AlbumArtwork(QWidget):
    """Rounded, aspect-cropped rendering of one already-decoded thumbnail."""

    def __init__(self) -> None:
        super().__init__()
        self._image = QImage()
        self.setAccessibleName("Album artwork")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(96, 96)

    @property
    def has_artwork(self) -> bool:
        return not self._image.isNull()

    def set_asset(self, asset: ArtworkAsset | None) -> None:
        if asset is None:
            self._image = QImage()
            self.setAccessibleDescription("No local album artwork is available")
        else:
            self._image = QImage(
                asset.rgba,
                asset.width,
                asset.height,
                asset.width * 4,
                QImage.Format.Format_RGBA8888,
            ).copy()
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        if self._image.isNull():
            return
        target = QRectF(self.rect())
        source = QRectF(self._image.rect())
        target_ratio = target.width() / max(1.0, target.height())
        source_ratio = source.width() / max(1.0, source.height())
        if source_ratio > target_ratio:
            width = source.height() * target_ratio
            source.setLeft((source.width() - width) / 2)
            source.setWidth(width)
        elif source_ratio < target_ratio:
            height = source.width() / target_ratio
            source.setTop((source.height() - height) / 2)
            source.setHeight(height)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        radius = max(4.0, min(target.width(), target.height()) * 0.11)
        path.addRoundedRect(target, radius, radius)
        painter.setClipPath(path)
        painter.drawImage(target, self._image, source)


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


def _lyric_group_layout_key(group: DesktopLyricGroup) -> tuple[object, ...]:
    """Return lyric content/identity while excluding clock-driven color fill."""

    return (
        group.line_id,
        group.original,
        group.romanized_or_transliterated,
        group.translation,
        group.provenance,
        group.transition_us,
        group.reading_metadata,
        group.translation_metadata,
    )


def _provenance_caption(provenance: str | None) -> str | None:
    if provenance is None:
        return None
    return {
        "provider": "Provider",
        "imported": "Imported",
        "local": "Local",
        "user": "Your version",
        "generated": "Generated",
    }.get(provenance)


def _reading_caption(group: DesktopLyricGroup) -> str:
    metadata = group.reading_metadata
    language = ("" if metadata is None else metadata.language or "").casefold()
    if metadata is not None and metadata.kind == "transliterated":
        layer = "Transliteration"
    elif language.startswith("ja"):
        layer = "Romaji"
    elif language.startswith("zh"):
        layer = "Pinyin"
    elif language.startswith("ko"):
        layer = "Korean reading"
    else:
        layer = "Reading"
    provenance = _provenance_caption(None if metadata is None else metadata.provenance)
    return layer if provenance is None else f"{layer} · {provenance}"


def _translation_caption(group: DesktopLyricGroup) -> str:
    provenance = _provenance_caption(
        None
        if group.translation_metadata is None
        else group.translation_metadata.provenance
    )
    return "Translation" if provenance is None else f"Translation · {provenance}"


def _layer_tooltip(
    caption: str, source_name: str | None, source_version: str | None
) -> str:
    source = source_name or "Source unavailable"
    if source_version:
        source = f"{source} {source_version}"
    return escape(f"{caption}. {source}")


def _desktop_layout_key(state: DesktopViewState) -> tuple[object, ...]:
    """Identify states that require semantic layout or transition work."""

    return (
        state.state,
        state.status_message,
        state.generation,
        state.title,
        state.artists,
        state.album,
        state.player,
        state.playback_state,
        state.duration_us,
        tuple(_lyric_group_layout_key(group) for group in state.previous),
        tuple(_lyric_group_layout_key(group) for group in state.active),
        tuple(_lyric_group_layout_key(group) for group in state.next),
        tuple(_lyric_group_layout_key(group) for group in state.static_lines),
        state.lyrics_source,
        state.match_confidence,
        state.sync_health,
        state.display_delay_us,
        state.diagnostics,
    )


def _timestamp_context(
    groups: tuple[DesktopLyricGroup, ...], count: int, *, preceding: bool
) -> tuple[DesktopLyricGroup, ...]:
    """Select whole timestamp groups; timestamp-less fixtures count individually."""

    if count <= 0:
        return ()
    grouped: list[list[DesktopLyricGroup]] = []
    for group in groups:
        if (
            grouped
            and group.transition_us is not None
            and grouped[-1][0].transition_us == group.transition_us
        ):
            grouped[-1].append(group)
        else:
            grouped.append([group])
    selected = grouped[-count:] if preceding else grouped[:count]
    return tuple(group for timestamp in selected for group in timestamp)


class _WrappedLyricLabel(QLabel):
    """Plain wrapped text with a width-dependent document height."""

    def __init__(self) -> None:
        super().__init__()
        self._karaoke_segments: tuple[DesktopKaraokeSegment, ...] = ()
        self._karaoke_base_color = QColor()
        self._karaoke_highlight_color = QColor()

    @property
    def karaoke_segments(self) -> tuple[DesktopKaraokeSegment, ...]:
        return self._karaoke_segments

    def set_karaoke_segments(self, segments: tuple[DesktopKaraokeSegment, ...]) -> None:
        if segments == self._karaoke_segments:
            return
        self._karaoke_segments = segments
        self.setAccessibleDescription(
            "Word-timed lyric highlighting" if segments else ""
        )
        self._apply_karaoke_palette()
        self.update()

    def set_karaoke_colors(self, base: QColor, highlight: QColor) -> None:
        if (
            base == self._karaoke_base_color
            and highlight == self._karaoke_highlight_color
        ):
            return
        self._karaoke_base_color = QColor(base)
        self._karaoke_highlight_color = QColor(highlight)
        self._apply_karaoke_palette()
        self.update()

    def _apply_karaoke_palette(self) -> None:
        color = (
            self._karaoke_base_color
            if self._karaoke_segments
            else self._karaoke_highlight_color
        )
        if color.isValid():
            _apply_text_palette(self, color)

    def _karaoke_layout(self) -> tuple[QTextLayout, QPointF]:
        rect = self.contentsRect()
        layout = QTextLayout(self.text(), self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        option.setAlignment(
            self.alignment()
            & (
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignRight
                | Qt.AlignmentFlag.AlignHCenter
                | Qt.AlignmentFlag.AlignJustify
            )
        )
        layout.setTextOption(option)
        layout.beginLayout()
        height = 0.0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1, rect.width()))
            line.setPosition(QPointF(0.0, height))
            height += line.height()
        layout.endLayout()
        if self.alignment() & Qt.AlignmentFlag.AlignBottom:
            y = rect.bottom() - height + 1
        elif self.alignment() & Qt.AlignmentFlag.AlignVCenter:
            y = rect.top() + (rect.height() - height) / 2
        else:
            y = rect.top()
        return layout, QPointF(rect.left(), y)

    @staticmethod
    def _segment_line_pieces(
        layout: QTextLayout, segment: DesktopKaraokeSegment
    ) -> tuple[tuple[QTextLine, float, float], ...]:
        pieces: list[tuple[QTextLine, float, float]] = []
        for line_index in range(layout.lineCount()):
            line = layout.lineAt(line_index)
            start = max(segment.start_index, line.textStart())
            end = min(
                segment.end_index,
                line.textStart() + line.textLength(),
            )
            if start >= end:
                continue
            start_x = cast(tuple[float, int], line.cursorToX(start))[0]
            end_x = cast(tuple[float, int], line.cursorToX(end))[0]
            pieces.append((line, start_x, end_x))
        return tuple(pieces)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self._karaoke_segments or self.hasSelectedText():
            return
        layout, origin = self._karaoke_layout()
        painter = QPainter(self)
        painter.setPen(self._karaoke_base_color)
        text_format = QTextCharFormat()
        text_format.setForeground(self._karaoke_highlight_color)
        for segment in self._karaoke_segments:
            progress = min(1.0, max(0.0, segment.highlight_fraction))
            if progress <= 0:
                continue
            selection = QTextLayout.FormatRange()
            selection.start = segment.start_index
            selection.length = segment.end_index - segment.start_index
            selection.format = text_format
            pieces = self._segment_line_pieces(layout, segment)
            remaining = (
                sum(abs(end_x - start_x) for _, start_x, end_x in pieces) * progress
            )
            for line, start_x, end_x in pieces:
                width = abs(end_x - start_x)
                painted_width = min(width, remaining)
                if painted_width <= 0:
                    break
                left = start_x if end_x >= start_x else start_x - painted_width
                clip = QRectF(
                    origin.x() + left,
                    origin.y() + line.y(),
                    painted_width,
                    line.height(),
                )
                layout.draw(painter, origin, (selection,), clip)
                remaining -= painted_width

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        margins = self.contentsMargins()
        inner_width = max(1, width - margins.left() - margins.right())
        flags = int(
            Qt.TextFlag.TextWordWrap
            | Qt.TextFlag.TextExpandTabs
            | Qt.TextFlag.TextDontClip
        )
        bounds = QFontMetrics(self.font()).boundingRect(
            QRect(0, 0, inner_width, 1_000_000),
            flags,
            self.text(),
        )
        return (
            max(
                QFontMetrics(self.font()).lineSpacing(),
                bounds.height(),
            )
            + margins.top()
            + margins.bottom()
        )

    def sizeHint(self) -> QSize:
        width = max(1, self.width())
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.heightForWidth(max(1, self.width())))


def _lyric_layer_label(accessible_name: str) -> _WrappedLyricLabel:
    label = _WrappedLyricLabel()
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    label.setAccessibleName(accessible_name)
    return label


class _LyricGroupWidget(QWidget):
    """Reusable original, reading, translation, and provenance labels."""

    def __init__(self, *, active: bool) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._active = active
        self._captions_visible = True
        self._appearance = default_appearance_profile()
        self._scale = 1.0
        layout = QVBoxLayout(self)
        self._layout = layout
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        region = "current" if active else "nearby"
        self._region = region
        self.original = _lyric_layer_label(f"Original {region} lyric")
        self.reading_caption = _lyric_layer_label("Reading provenance")
        self.romanized = _lyric_layer_label(f"Romanized {region} lyric")
        self.translation_caption = _lyric_layer_label("Translation provenance")
        self.translation = _lyric_layer_label(f"Translated {region} lyric")
        layout.addWidget(self.original)
        layout.addWidget(self.reading_caption)
        layout.addWidget(self.romanized)
        layout.addWidget(self.translation_caption)
        layout.addWidget(self.translation)

    def set_group(self, group: DesktopLyricGroup) -> None:
        layout_changed = False
        for label, text in (
            (self.original, group.original),
            (self.romanized, group.romanized_or_transliterated),
            (self.translation, group.translation),
        ):
            rendered = text or ""
            visible = text is not None
            if label.text() != rendered:
                label.setText(rendered)
                layout_changed = True
            if label.isHidden() == visible:
                label.setVisible(visible)
                layout_changed = True
        reading_caption = _reading_caption(group)
        translation_caption = _translation_caption(group)
        for label, text, visible in (
            (
                self.reading_caption,
                reading_caption,
                self._active
                and self._captions_visible
                and group.romanized_or_transliterated is not None,
            ),
            (
                self.translation_caption,
                translation_caption,
                self._active
                and self._captions_visible
                and group.translation is not None,
            ),
        ):
            if label.text() != text:
                label.setText(text)
                layout_changed = True
            if label.isHidden() == visible:
                label.setVisible(visible)
                layout_changed = True
        self.reading_caption.setToolTip(
            _layer_tooltip(
                reading_caption,
                (
                    None
                    if group.reading_metadata is None
                    else group.reading_metadata.source_name
                ),
                (
                    None
                    if group.reading_metadata is None
                    else group.reading_metadata.source_version
                ),
            )
        )
        self.translation_caption.setToolTip(
            _layer_tooltip(
                translation_caption,
                (
                    None
                    if group.translation_metadata is None
                    else group.translation_metadata.source_name
                ),
                (
                    None
                    if group.translation_metadata is None
                    else group.translation_metadata.source_version
                ),
            )
        )
        self.romanized.setAccessibleName(f"{reading_caption} {self._region} lyric")
        self.translation.setAccessibleName(
            f"{translation_caption} {self._region} lyric"
        )
        self.original.set_karaoke_segments(
            group.karaoke_segments if self._active else ()
        )
        if layout_changed:
            self.updateGeometry()

    def set_captions_visible(self, visible: bool) -> None:
        """Keep layer meaning accessible while shedding labels in tight layouts."""

        if visible == self._captions_visible:
            return
        self._captions_visible = visible
        self.reading_caption.setVisible(
            self._active and visible and not self.romanized.isHidden()
        )
        self.translation_caption.setVisible(
            self._active and visible and not self.translation.isHidden()
        )
        self.updateGeometry()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        margins = self._layout.contentsMargins()
        inner_width = max(1, width - margins.left() - margins.right())
        labels = tuple(
            label
            for label in (
                self.original,
                self.reading_caption,
                self.romanized,
                self.translation_caption,
                self.translation,
            )
            if not label.isHidden()
        )
        return (
            margins.top()
            + margins.bottom()
            + sum(label.heightForWidth(inner_width) for label in labels)
            + max(0, len(labels) - 1) * self._layout.spacing()
        )

    def sizeHint(self) -> QSize:
        width = max(1, self.width())
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.heightForWidth(max(1, self.width())))

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
            label.setFont(
                _styled_font(
                    label,
                    style,
                    scale * emphasis * appearance.lyric_scale_percent / 100,
                )
            )
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
        caption_color = _semantic_color(
            appearance.colors.muted,
            round(
                appearance.opacity.secondary_representation
                * appearance.opacity.content
                / 100
            ),
        )
        for caption in (self.reading_caption, self.translation_caption):
            caption.setFont(_styled_font(caption, appearance.status, scale * 0.9))
            _apply_text_palette(caption, caption_color)
            caption.setAlignment(_qt_alignment(appearance.lyric_alignment))
        active_color = _semantic_color(
            appearance.colors.active_lyric, appearance.opacity.content
        )
        karaoke_base = _semantic_color(
            appearance.colors.inactive_lyric, appearance.opacity.content
        )
        if self._active:
            self.original.set_karaoke_colors(karaoke_base, active_color)
        else:
            karaoke_base.setAlpha(
                round(karaoke_base.alpha() * appearance.opacity.inactive_line / 100)
            )
            self.original.set_karaoke_colors(karaoke_base, karaoke_base)
        self._layout.setSpacing(appearance.spacing.representation)
        self.updateGeometry()

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

    def __init__(self, *, active: bool = False, preceding: bool = False) -> None:
        super().__init__()
        self._active = active
        self._preceding = preceding
        self._groups: tuple[DesktopLyricGroup, ...] = ()
        self._rendered_groups: tuple[DesktopLyricGroup, ...] = ()
        self._group_widgets: list[_LyricGroupWidget] = []
        self._visible_group_limit = self._MAX_VISIBLE_GROUPS
        self._captions_visible = True
        self._selection_enabled = False
        self._appearance = default_appearance_profile()
        self._scale = 1.0
        self._fit_scale = 1.0
        initial_point_size = self.font().pointSizeF()
        self._point_size = initial_point_size if initial_point_size > 0 else 10.0
        self._responsive_minimum_height = 0
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
            and minimum_height == self._responsive_minimum_height
        ):
            return
        self._point_size = point_size
        self._responsive_minimum_height = minimum_height
        self._apply_fit_scale()

    def set_fit_scale(self, scale: float) -> None:
        """Apply a bounded presentation-only fit after context has been reduced."""

        bounded = min(1.0, max(0.35, scale))
        if abs(bounded - self._fit_scale) < 1e-6:
            return
        self._fit_scale = bounded
        self._apply_fit_scale()

    @property
    def fit_scale(self) -> float:
        return self._fit_scale

    def _apply_fit_scale(self) -> None:
        font = self.font()
        font.setPointSizeF(max(7.0, self._point_size * self._fit_scale))
        font.setWeight(QFont.Weight.DemiBold if self._active else QFont.Weight.Normal)
        self.setFont(font)
        self.setMinimumHeight(round(self._responsive_minimum_height * self._fit_scale))
        for widget in self._group_widgets:
            widget.apply_appearance(self._appearance, self._scale * self._fit_scale)

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
            widget.apply_appearance(appearance, scale * self._fit_scale)

    def set_groups(self, groups: tuple[DesktopLyricGroup, ...]) -> None:
        same_layout = len(groups) == len(self._groups) and all(
            _lyric_group_layout_key(current) == _lyric_group_layout_key(incoming)
            for current, incoming in zip(self._groups, groups, strict=True)
        )
        self._groups = groups
        if same_layout:
            self._rendered_groups = self._selected_groups()
            for group, widget in zip(
                self._rendered_groups,
                self._group_widgets,
                strict=False,
            ):
                widget.original.set_karaoke_segments(
                    group.karaoke_segments if self._active else ()
                )
            return
        self._render_groups()

    @property
    def available_group_count(self) -> int:
        return min(len(self._groups), self._MAX_VISIBLE_GROUPS)

    @property
    def visible_group_count(self) -> int:
        return len(self._rendered_groups)

    @property
    def captions_visible(self) -> bool:
        return self._captions_visible

    def set_visible_group_limit(self, limit: int) -> None:
        bounded = min(self._MAX_VISIBLE_GROUPS, max(0, limit))
        if bounded == self._visible_group_limit:
            return
        self._visible_group_limit = bounded
        self._render_groups()

    def set_captions_visible(self, visible: bool) -> None:
        if visible == self._captions_visible:
            return
        self._captions_visible = visible
        for widget in self._group_widgets:
            widget.set_captions_visible(visible)
        self.updateGeometry()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        margins = self._layout.contentsMargins()
        inner_width = max(1, width - margins.left() - margins.right())
        groups = tuple(
            widget for widget in self._group_widgets if not widget.isHidden()
        )
        document_height = (
            margins.top()
            + margins.bottom()
            + sum(widget.heightForWidth(inner_width) for widget in groups)
            + max(0, len(groups) - 1) * self._layout.spacing()
        )
        return max(self.minimumHeight(), document_height)

    def sizeHint(self) -> QSize:
        width = max(1, self.width())
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.heightForWidth(max(1, self.width())))

    def widgets_for_lines(self, line_ids: tuple[str, ...]) -> tuple[QWidget, ...]:
        """Return currently rendered stable-ID widgets without allocating."""

        wanted = set(line_ids)
        return tuple(
            widget
            for group, widget in zip(
                self._rendered_groups, self._group_widgets, strict=False
            )
            if group.line_id in wanted and not widget.isHidden()
        )

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
        visible_groups = self._selected_groups()
        self._rendered_groups = visible_groups
        while len(self._group_widgets) < len(visible_groups):
            widget = _LyricGroupWidget(active=self._active)
            widget.apply_appearance(self._appearance, self._scale * self._fit_scale)
            widget.set_captions_visible(self._captions_visible)
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
        self.updateGeometry()

    def _selected_groups(self) -> tuple[DesktopLyricGroup, ...]:
        available = (
            self._groups[-self._MAX_VISIBLE_GROUPS :]
            if self._preceding
            else self._groups[: self._MAX_VISIBLE_GROUPS]
        )
        if self._preceding and self._visible_group_limit:
            return available[-self._visible_group_limit :]
        return available[: self._visible_group_limit]


class _TransitionAnchor:
    """Measured visual position for stable incoming lyric IDs."""

    def __init__(self, line_ids: tuple[str, ...], center_y: float) -> None:
        self.line_ids = line_ids
        self.center_y = center_y


class LyricTransitionViewport(QScrollArea):
    """Lay out a measured lyric document and FLIP adjacent stable line IDs."""

    def __init__(
        self,
        previous: LyricBand,
        active: LyricBand,
        following: LyricBand,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAccessibleName("Timed lyrics")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMaximumWidth(1_040)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setWidgetResizable(False)
        self._offset = 0
        self._previous = previous
        self._active = active
        self._following = following
        self._previous_requested = True
        self._following_requested = True
        self._scene = QWidget()
        self._scene.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self.setWidget(self._scene)
        self._content = QWidget(self._scene)
        lyric_layout = QVBoxLayout(self._content)
        self.lyric_layout = lyric_layout
        lyric_layout.setContentsMargins(0, 0, 0, 0)
        lyric_layout.addWidget(previous)
        lyric_layout.addWidget(active)
        lyric_layout.addWidget(following)

        self._movement = QPropertyAnimation(self, b"lyricOffset", self)
        self._movement.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._active_effect = QGraphicsOpacityEffect(active)
        self._active_effect.setOpacity(1.0)
        active.setGraphicsEffect(self._active_effect)
        self._emphasis = QPropertyAnimation(self._active_effect, b"opacity", self)
        self._emphasis.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._document_height = 0

    def lyric_offset(self) -> int:
        return self._offset

    def set_lyric_offset(self, value: int) -> None:
        self._offset = value
        self._content.move(0, value)

    lyricOffset = Property(int, lyric_offset, set_lyric_offset)

    @property
    def animation_running(self) -> bool:
        return any(
            animation.state() is QAbstractAnimation.State.Running
            for animation in (self._movement, self._emphasis)
        )

    @property
    def active_opacity(self) -> float:
        return self._active_effect.opacity()

    @property
    def document_height(self) -> int:
        return self._document_height

    @property
    def adaptive_fit_scale(self) -> float:
        return self._active.fit_scale

    def set_context_visibility(self, previous: bool, following: bool) -> None:
        """Declare desired context; measured fitting may show fewer nearby lines."""

        if (
            previous == self._previous_requested
            and following == self._following_requested
        ):
            return
        self._previous_requested = previous
        self._following_requested = following
        self.relayout(center_active=True)

    def _visual_center(self, line_ids: tuple[str, ...]) -> float | None:
        widgets = tuple(
            widget
            for band in (self._previous, self._active, self._following)
            for widget in band.widgets_for_lines(line_ids)
        )
        if not widgets:
            return None
        tops = [
            widget.mapTo(self.viewport(), widget.rect().topLeft()).y()
            for widget in widgets
        ]
        bottoms = [
            widget.mapTo(self.viewport(), widget.rect().bottomLeft()).y()
            for widget in widgets
        ]
        return (min(tops) + max(bottoms)) / 2

    def capture_anchor(
        self, line_ids: tuple[str, ...], *, adjacent: bool
    ) -> _TransitionAnchor | None:
        """Capture FIRST geometry, including any in-flight visual offset."""

        if not adjacent or not line_ids:
            return None
        center = self._visual_center(line_ids)
        if center is None:
            return None
        self._movement.stop()
        self._emphasis.stop()
        return _TransitionAnchor(line_ids, center)

    def transition(
        self,
        anchor: _TransitionAnchor | None,
        duration_ms: int,
        emphasis_ms: int,
    ) -> None:
        """Invert from measured old geometry and converge to exact final layout."""

        self._movement.stop()
        self._emphasis.stop()
        self.set_lyric_offset(0)
        self._active_effect.setOpacity(1.0)
        self.relayout(center_active=True)
        if anchor is None or duration_ms <= 0:
            return
        final_center = self._visual_center(anchor.line_ids)
        if final_center is None:
            return
        distance = round(anchor.center_y - final_center)
        if distance == 0:
            return
        self._movement.setDuration(duration_ms)
        self._movement.setStartValue(distance)
        self._movement.setEndValue(0)
        self._movement.start()
        if emphasis_ms > 0:
            self._emphasis.setDuration(emphasis_ms)
            self._emphasis.setStartValue(0.78)
            self._emphasis.setEndValue(1.0)
            self._emphasis.start()

    def settle(self) -> None:
        """Stop every visual transition at the exact semantic layout."""

        self._movement.stop()
        self._emphasis.stop()
        self.set_lyric_offset(0)
        self._active_effect.setOpacity(1.0)
        self.relayout(center_active=True)

    def relayout(self, *, center_active: bool = False) -> None:
        """Size the scroll document from real wrapped heights at viewport width."""

        viewport = self.viewport()
        width = max(1, viewport.width())
        self._apply_adaptive_fit(width, max(1, viewport.height()))
        visible_bands = tuple(
            band
            for band in (self._previous, self._active, self._following)
            if not band.isHidden()
        )
        spacing = self.lyric_layout.spacing()
        document_height = (
            sum(band.heightForWidth(width) for band in visible_bands)
            + max(0, len(visible_bands) - 1) * spacing
        )
        self._document_height = max(0, document_height)
        scene_height = max(viewport.height(), self._document_height)
        slack = max(0, scene_height - self._document_height)
        self.lyric_layout.setContentsMargins(0, slack // 2, 0, slack - slack // 2)
        self._scene.resize(width, scene_height)
        self._content.resize(width, scene_height)
        self.lyric_layout.invalidate()
        self.lyric_layout.activate()
        self._content.move(0, self._offset)
        if center_active and not self._active.isHidden():
            active_top = self._active.mapTo(
                self._scene, self._active.rect().topLeft()
            ).y()
            active_height = self._active.height()
            if active_height >= viewport.height():
                target = active_top
            else:
                target = active_top + active_height // 2 - viewport.height() // 2
            self.verticalScrollBar().setValue(target)

    def _document_height_for_width(self, width: int) -> int:
        visible_bands = tuple(
            band
            for band in (self._previous, self._active, self._following)
            if not band.isHidden()
        )
        return (
            sum(band.heightForWidth(width) for band in visible_bands)
            + max(0, len(visible_bands) - 1) * self.lyric_layout.spacing()
        )

    def _apply_adaptive_fit(self, width: int, available_height: int) -> None:
        """Reduce distant context before making one bounded typography adjustment."""

        self._active.set_fit_scale(1.0)
        self._active.set_captions_visible(True)
        previous_count = (
            self._previous.available_group_count if self._previous_requested else 0
        )
        following_count = (
            self._following.available_group_count if self._following_requested else 0
        )
        self._previous.set_visible_group_limit(previous_count)
        self._following.set_visible_group_limit(following_count)
        self._previous.setVisible(previous_count > 0 and bool(self._previous.text()))
        self._following.setVisible(following_count > 0 and bool(self._following.text()))

        while self._document_height_for_width(width) > available_height and (
            previous_count or following_count
        ):
            if previous_count >= following_count and previous_count:
                previous_count -= 1
                self._previous.set_visible_group_limit(previous_count)
                self._previous.setVisible(previous_count > 0)
            elif following_count:
                following_count -= 1
                self._following.set_visible_group_limit(following_count)
                self._following.setVisible(following_count > 0)

        if self._document_height_for_width(width) <= available_height:
            return
        self._active.set_captions_visible(False)
        if self._document_height_for_width(width) <= available_height:
            return

        minimum_scale = 0.35
        self._active.set_fit_scale(minimum_scale)
        if self._document_height_for_width(width) > available_height:
            return
        lower = minimum_scale
        upper = 1.0
        for _step in range(9):
            candidate = (lower + upper) / 2
            self._active.set_fit_scale(candidate)
            if self._document_height_for_width(width) <= available_height:
                lower = candidate
            else:
                upper = candidate
        self._active.set_fit_scale(lower)

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._movement.stop()
        self._emphasis.stop()
        self.set_lyric_offset(0)
        self._active_effect.setOpacity(1.0)
        super().resizeEvent(event)
        self.relayout(center_active=True)
        QTimer.singleShot(0, lambda: self.relayout(center_active=True))

    def hideEvent(self, event: QHideEvent) -> None:
        # QScrollArea can receive an internal hide while its base constructor is
        # still building the viewport, before the animations exist.
        if hasattr(self, "_movement"):
            self.settle()
        super().hideEvent(event)


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


class MainWindow(DesktopWindowSurface):
    """Responsive, palette-aware main window driven only by application state."""

    settings_requested = Signal()
    setting_requested = Signal(str, object)
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
        overlay_recovery_available: bool | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or RepresentationDisplaySettings()
        self._interaction_settings = (
            interaction_settings or DesktopInteractionSettings()
        )
        self._appearance = appearance or default_appearance_profile()
        self._artwork_asset: ArtworkAsset | None = None
        self._state = DesktopViewState(DesktopLyricsState.WAITING, "Waiting for media…")
        self._library_result: (
            tuple[LibraryScanSummary, tuple[LibraryReviewItem, ...]] | None
        ) = None
        self._library_scan_running = False
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

        root = DesktopWorkspace()
        self.workspace = root
        root.setAccessibleName("KonoKashi main view")
        layout = root.panel_layout
        self._root_layout = layout

        self.overlay_controls = QWidget()
        overlay_controls_layout = QHBoxLayout(self.overlay_controls)
        overlay_controls_layout.setContentsMargins(0, 0, 0, 0)
        self.overlay_drag_handle = _OverlayDragHandle(
            "Overlay unlocked — drag here to move"
        )
        self.overlay_drag_handle.setAccessibleName("Move lyrics overlay")
        self.overlay_drag_handle.setToolTip(
            "Drag to ask the compositor to move this overlay"
        )
        self.overlay_drag_handle.setCursor(Qt.CursorShape.SizeAllCursor)
        self.overlay_drag_handle.move_requested.connect(self.start_overlay_move)
        self.overlay_lock_button = QPushButton("Enable click-through")
        self.overlay_lock_button.setAccessibleName("Enable click-through overlay")
        self.overlay_lock_button.clicked.connect(lambda: self.set_overlay_locked(True))
        self.overlay_exit_button = QPushButton("Exit overlay")
        self.overlay_exit_button.setAccessibleName("Return to normal window")
        self.overlay_exit_button.clicked.connect(
            lambda: self.set_window_mode(DesktopWindowMode.NORMAL)
        )
        self.overlay_size_grip = QSizeGrip(self.overlay_controls)
        self.overlay_size_grip.setAccessibleName("Resize lyrics overlay")
        overlay_controls_layout.addWidget(self.overlay_drag_handle, 1)
        overlay_controls_layout.addWidget(self.overlay_lock_button)
        overlay_controls_layout.addWidget(self.overlay_exit_button)
        overlay_controls_layout.addWidget(self.overlay_size_grip)
        self.overlay_controls.setVisible(False)
        layout.addWidget(self.overlay_controls)

        metadata_panel = QWidget()
        header = QBoxLayout(QBoxLayout.Direction.LeftToRight, metadata_panel)
        header.setContentsMargins(0, 0, 0, 0)
        self._header_layout = header
        metadata_widget = QWidget()
        self._metadata_widget = metadata_widget
        metadata = QVBoxLayout(metadata_widget)
        self._metadata_layout = metadata
        metadata.setContentsMargins(0, 0, 0, 0)
        self.title_label = ElidingLabel("KonoKashi")
        self.title_label.setAccessibleName("Track title")
        self.title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.artist_label = ElidingLabel("")
        self.artist_label.setAccessibleName("Track artist")
        self.album_label = ElidingLabel("")
        self.album_label.setAccessibleName("Track album")
        metadata.addWidget(self.title_label)
        metadata.addWidget(self.artist_label)
        metadata.addWidget(self.album_label)
        self.artwork = AlbumArtwork()
        self.artwork.setVisible(False)
        header.addWidget(self.artwork)
        header.addWidget(metadata_widget, 1)
        actions_widget = QWidget()
        self._actions_widget = actions_widget
        actions = QHBoxLayout(actions_widget)
        self._actions_layout = actions
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        self.review_button = QPushButton("Review lyrics")
        self.review_button.setAccessibleName("Review and correct this track and lyrics")
        self.review_button.setToolTip("Review the detected track and lyrics match")
        self.review_button.clicked.connect(self.review_requested)
        self.library_results_button = QPushButton("Library results…")
        self.library_results_button.setAccessibleName("Review library scan results")
        self.library_results_button.setToolTip(
            "Review local scan errors and recordings that need attention"
        )
        self.library_results_button.setFlat(True)
        self.library_results_button.setVisible(False)
        self.library_results_button.clicked.connect(self._open_library_results)
        self.review_button.setFlat(True)
        actions.addWidget(self.review_button)
        actions.addWidget(self.library_results_button)
        header.addWidget(actions_widget)
        root.add_panel(PanelId.METADATA, metadata_panel)

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

        self.previous_band = LyricBand(preceding=True)
        self.active_band = LyricBand(active=True)
        self.next_band = LyricBand()
        timed_page = QWidget()
        timed_page_layout = QHBoxLayout(timed_page)
        timed_page_layout.setContentsMargins(0, 0, 0, 0)
        timed_page_layout.addStretch(1)
        lyric_column = LyricTransitionViewport(
            self.previous_band, self.active_band, self.next_band
        )
        self._lyric_layout = lyric_column.lyric_layout
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
        root.add_panel(PanelId.LYRICS, self.content_stack, stretch=1)

        self.playback_widget = QWidget()
        progress_row = QHBoxLayout(self.playback_widget)
        self._progress_layout = progress_row
        progress_row.setContentsMargins(0, 0, 0, 0)
        self.playback_label = _plain_label("Unknown")
        self.playback_label.setAccessibleName("Playback state")
        self.progress = PlaybackProgress()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Playback progress")
        self.time_label = _plain_label("--:-- / --:--")
        self.time_label.setAccessibleName("Playback position and duration")
        progress_row.addWidget(self.playback_label)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.time_label)
        root.add_panel(PanelId.PROGRESS, self.playback_widget)

        self.source_label = _plain_label("")
        self.source_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.source_label.setWordWrap(True)
        self.source_label.setAccessibleName("Lyrics source and synchronization health")
        self.source_label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        root.add_panel(PanelId.STATUS, self.source_label)

        self.settings_action = QAction("&Settings…", self)
        self.settings_action.setShortcut(QKeySequence("Ctrl+,"))
        self.settings_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.settings_action.triggered.connect(self._open_settings)
        self.addAction(self.settings_action)

        self.review_action = QAction("&Review track and lyrics…", self)
        self.review_action.setShortcut(QKeySequence("Ctrl+Shift+R"))
        self.review_action.triggered.connect(self.review_requested)
        self.details_action = QAction("Lyrics &details…", self)
        self.details_action.triggered.connect(self._open_details)
        self.scan_action = QAction("Scan &library", self)
        self.scan_action.triggered.connect(self._toggle_library_scan)
        self.quit_action = QAction("&Quit", self)
        self.quit_action.triggered.connect(self.close)
        self.restore_window_action = QAction("Exit transient window mode", self)
        self.restore_window_action.setShortcut(QKeySequence("Escape"))
        self.restore_window_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.restore_window_action.triggered.connect(self.restore_from_transient_mode)
        self.restore_window_action.setEnabled(False)
        self.addAction(self.restore_window_action)

        self.more_button = QToolButton()
        self.more_button.setText("More…")
        self.more_button.setAccessibleName("More KonoKashi actions")
        self.more_button.setToolTip("Settings, lyric details, and local library tools")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_menu = QMenu(self.more_button)
        self.more_menu.addAction(self.settings_action)
        self.more_menu.addAction(self.details_action)
        self.more_menu.addSeparator()
        self.more_menu.addAction(self.scan_action)
        self.more_button.setMenu(self.more_menu)
        self._actions_layout.addWidget(self.more_button)
        QWidget.setTabOrder(self.review_button, self.more_button)
        self.application_menu = ApplicationMenu(
            self.settings_action,
            self.review_action,
            self.details_action,
            self.scan_action,
            self.quit_action,
            self,
        )
        self.application_menu.setting_requested.connect(self.setting_requested)
        self.application_menu.about_requested.connect(self._about)
        self.application_menu.window_mode_requested.connect(self.set_window_mode)
        self.application_menu.overlay_lock_requested.connect(self.set_overlay_locked)
        self.application_menu.screen_requested.connect(self._move_to_screen)
        self.setMenuBar(self.application_menu)

        actual_tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self._probe_overlay_recovery = overlay_recovery_available is None
        recovery_available = (
            actual_tray_available
            if overlay_recovery_available is None
            else overlay_recovery_available
        )
        self.set_overlay_recovery_available(recovery_available)
        self.tray_menu = QMenu(self)
        self.tray_show_action = self.tray_menu.addAction("Show KonoKashi")
        self.tray_show_action.triggered.connect(self._show_from_tray)
        self.tray_menu.addSeparator()
        for mode in DesktopWindowMode:
            self.tray_menu.addAction(self.application_menu.mode_actions[mode])
        self.tray_menu.addAction(self.application_menu.overlay_lock_action)
        self.tray_menu.addMenu(self.application_menu.screen_menu)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.quit_action)
        self.tray_icon = QSystemTrayIcon(self.windowIcon(), self)
        self.tray_icon.setToolTip("KonoKashi lyrics")
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.setVisible(actual_tray_available)
        self._overlay_safety_timer = QTimer(self)
        self._overlay_safety_timer.setInterval(1_000)
        self._overlay_safety_timer.timeout.connect(self._verify_overlay_recovery)

        self.window_mode_changed.connect(self._window_mode_changed)
        self.overlay_lock_changed.connect(self._overlay_lock_changed)
        gui_application = cast(QGuiApplication | None, QGuiApplication.instance())
        if gui_application is not None:
            gui_application.screenAdded.connect(self._screen_topology_changed)
            gui_application.screenRemoved.connect(self._screen_topology_changed)

        self.setCentralWidget(root)
        self._apply_responsive_typography(
            self._responsive_scale(self.width(), self.height())
        )
        self.set_appearance_profile(self._appearance)
        self.render_state(self._state)
        self._project_window_controls()

    def _window_mode_changed(self, value: object) -> None:
        if not isinstance(value, DesktopWindowMode):
            return
        if value is DesktopWindowMode.NORMAL:
            self.setMinimumSize(420, 420)
            self.setWindowTitle("KonoKashi")
        elif value is DesktopWindowMode.COMPACT:
            self.setMinimumSize(380, 280)
            if value not in self._mode_geometries:
                self.resize(560, 420)
            self.setWindowTitle("KonoKashi — Compact lyrics")
        elif value is DesktopWindowMode.OVERLAY:
            self.setMinimumSize(420, 180)
            if value not in self._mode_geometries:
                self.resize(760, 360)
            self.setWindowTitle("KonoKashi — Lyrics overlay")
        else:
            self.setMinimumSize(420, 420)
            self.setWindowTitle("KonoKashi — Fullscreen lyrics")
        self.application_menu.setVisible(
            value in {DesktopWindowMode.NORMAL, DesktopWindowMode.COMPACT}
        )
        self.restore_window_action.setEnabled(
            value in {DesktopWindowMode.OVERLAY, DesktopWindowMode.FULLSCREEN}
        )
        self._apply_visibility()
        self._project_window_controls()
        self._pending_typography_size = self.size()
        self._finish_resize_typography()

    def _overlay_lock_changed(self, _locked: bool) -> None:
        if _locked and self._probe_overlay_recovery:
            self._overlay_safety_timer.start()
        else:
            self._overlay_safety_timer.stop()
        self._apply_visibility()
        self._project_window_controls()

    def _verify_overlay_recovery(self) -> None:
        """Fail open if the tray escape route disappears while click-through."""

        available = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray_icon.setVisible(available)
        self.set_overlay_recovery_available(available)
        self._project_window_controls()

    def _project_window_controls(self) -> None:
        overlay = self.window_mode is DesktopWindowMode.OVERLAY
        unlocked_overlay = overlay and not self.overlay_locked
        self.overlay_controls.setVisible(unlocked_overlay)
        self.overlay_lock_button.setEnabled(self.overlay_recovery_available)
        self.overlay_lock_button.setText(
            "Enable click-through"
            if self.overlay_recovery_available
            else "Click-through unavailable"
        )
        self.overlay_lock_button.setAccessibleName(self.overlay_lock_button.text())
        self.overlay_lock_button.setToolTip(
            "Pointer input will pass through; unlock from the system tray"
            if self.overlay_recovery_available
            else "A system tray is required so the overlay can always be unlocked"
        )
        self.application_menu.project_window_state(
            self.window_mode,
            overlay_locked=self.overlay_locked,
            recovery_available=self.overlay_recovery_available,
            screen_names=self.screen_names,
            current_screen=self.current_screen_index,
            backend_description=self.overlay_backend_description,
        )
        if self.overlay_locked:
            self.tray_show_action.setText("Unlock lyrics overlay")
        elif not self.isVisible():
            self.tray_show_action.setText("Show KonoKashi")
        else:
            self.tray_show_action.setText("Raise KonoKashi")

    def _move_to_screen(self, index: int) -> None:
        self.move_to_screen(index)
        self._project_window_controls()

    def _screen_topology_changed(self, _screen: object) -> None:
        self._project_window_controls()

    def _show_from_tray(self) -> None:
        if self.overlay_locked:
            self.set_overlay_locked(False)
        self.show()
        self.raise_()
        self.activateWindow()
        self._project_window_controls()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self._show_from_tray()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Scale lyric presentation with logical window area, not device pixels."""

        super().resizeEvent(event)
        self._set_header_direction(event.size().width())
        self._apply_visibility()
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
            _styled_font(self.title_label, appearance.metadata, scale * 1.12)
        )
        self.artist_label.setFont(
            _styled_font(self.artist_label, appearance.metadata, scale * 0.96)
        )
        self.album_label.setFont(
            _styled_font(self.album_label, appearance.metadata, scale * 0.9)
        )
        artwork_size = round(72 * min(1.65, max(0.82, scale)))
        self.artwork.setFixedSize(artwork_size, artwork_size)
        for label in (
            self.status_label,
            self.static_status_label,
            self.playback_label,
            self.time_label,
            self.source_label,
        ):
            label.setFont(_styled_font(label, appearance.status, scale))

        for button in (
            self.review_button,
            self.library_results_button,
            self.more_button,
        ):
            font = button.font()
            font.setPointSizeF(max(8.0, appearance.status.size * scale))
            button.setFont(font)

        self.static_lyrics.setFont(
            _styled_font(
                self.static_lyrics,
                appearance.original,
                scale * appearance.lyric_scale_percent / 100,
            )
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

    def set_artwork(self, asset: ArtworkAsset | None) -> None:
        """Project one bounded thumbnail without retaining its source URI."""

        self._artwork_asset = asset
        self.artwork.set_asset(asset)
        self._update_artwork_accessibility()
        self._apply_background_surface()
        self._apply_visibility()

    def _update_artwork_accessibility(self) -> None:
        if self._artwork_asset is None:
            return
        artists = " · ".join(self._state.artists)
        description = f"Album artwork for {self._state.title or 'current track'}"
        if artists:
            description += f" by {artists}"
        self.artwork.setAccessibleDescription(description)

    def _apply_background_surface(self) -> None:
        appearance = self._appearance
        background = _semantic_color(
            appearance.colors.background, appearance.opacity.background
        )
        wash: QColor | None = None
        asset = self._artwork_asset
        if asset is not None and appearance.artwork.dynamic_background:
            safe_color = contrast_safe_artwork_tint(
                appearance.colors.background,
                asset.palette_color,
                (
                    appearance.colors.active_lyric,
                    appearance.colors.original_lyric,
                    appearance.colors.metadata_primary,
                    appearance.colors.status,
                ),
            )
            wash = QColor(safe_color)
            wash.setAlpha(background.alpha())
        self.set_background_color(background, wash)

    def set_appearance_profile(self, appearance: AppearanceProfile) -> None:
        """Apply one resolved semantic profile without touching playback state."""

        self._lyric_column.settle()
        self._appearance = appearance
        self.application_menu.project(appearance)
        scale = self._applied_typography_scale or self._responsive_scale(
            self.width(), self.height()
        )
        self._apply_responsive_typography(scale)

        palette = QPalette(self.palette())
        background = _semantic_color(
            appearance.colors.background, appearance.opacity.background
        )
        foreground = _semantic_color(
            appearance.colors.foreground, appearance.opacity.content
        )
        button = QColor(background)
        button.setAlpha(255)
        button = button.lighter(125) if button.lightness() < 128 else button.darker(106)
        palette.setColor(
            QPalette.ColorRole.Window,
            background,
        )
        for role in (
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
        ):
            palette.setColor(role, foreground)
        palette.setColor(QPalette.ColorRole.Base, background)
        palette.setColor(QPalette.ColorRole.AlternateBase, button)
        palette.setColor(QPalette.ColorRole.Button, button)
        palette.setColor(
            QPalette.ColorRole.PlaceholderText,
            _semantic_color(appearance.colors.muted, appearance.opacity.content),
        )
        palette.setColor(
            QPalette.ColorRole.Highlight,
            _semantic_color(appearance.colors.selection),
        )
        highlighted_text = (
            "#000000"
            if color_contrast_ratio("#000000", appearance.colors.selection)
            >= color_contrast_ratio("#FFFFFF", appearance.colors.selection)
            else "#FFFFFF"
        )
        palette.setColor(
            QPalette.ColorRole.HighlightedText, _semantic_color(highlighted_text)
        )
        palette.setColor(
            QPalette.ColorRole.Link,
            _semantic_color(appearance.colors.accent),
        )
        if hasattr(QPalette.ColorRole, "Accent"):
            palette.setColor(
                QPalette.ColorRole.Accent,
                _semantic_color(appearance.colors.accent),
            )
        self.setPalette(palette)
        self.application_menu.setPalette(palette)
        self.more_menu.setPalette(palette)
        self._apply_background_surface()
        central = self.centralWidget()
        if central is not None:
            central.setAutoFillBackground(False)
            central.setPalette(palette)
        self.static_lyrics.viewport().setAutoFillBackground(False)

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
        _apply_text_palette(
            self.album_label,
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
        self.progress.set_profile(appearance)

        lyric_alignment = _qt_alignment(appearance.lyric_alignment)
        text_option = self.static_lyrics.document().defaultTextOption()
        text_option.setAlignment(lyric_alignment)
        self.static_lyrics.document().setDefaultTextOption(text_option)
        metadata_alignment = _qt_alignment(appearance.metadata_alignment)
        self.title_label.setAlignment(metadata_alignment)
        self.artist_label.setAlignment(metadata_alignment)
        self.album_label.setAlignment(metadata_alignment)
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

        previous_state = self._state
        if _desktop_layout_key(previous_state) == _desktop_layout_key(state):
            self._state = state
            self._update_artwork_accessibility()
            self.active_band.set_groups(state.active)
            self.update_playback(state)
            return
        transition_direction = self._lyric_transition_direction(previous_state, state)
        transition_anchor = self._lyric_column.capture_anchor(
            tuple(group.line_id for group in state.active),
            adjacent=transition_direction != 0,
        )
        self._state = state
        self._update_artwork_accessibility()
        self.title_label.setText(state.title or "KonoKashi")
        self.artist_label.setText(" · ".join(state.artists))
        self.artist_label.setVisible(bool(state.artists))
        self.album_label.setText(state.album or "")
        self.album_label.setVisible(bool(state.album))
        self.status_label.setText(state.status_message)
        self.static_status_label.setText(state.status_message)
        appearance = self._appearance
        previous = (
            _timestamp_context(
                state.previous, appearance.context.previous, preceding=True
            )
            if appearance.visibility.inactive_context
            and appearance.context.previous > 0
            else ()
        )
        following = (
            _timestamp_context(
                state.next, appearance.context.following, preceding=False
            )
            if appearance.visibility.inactive_context
            and appearance.context.following > 0
            else ()
        )
        self.previous_band.set_groups(previous)
        self.active_band.set_groups(state.active)
        self.next_band.set_groups(following)
        self.static_lyrics.setPlainText(_group_text(state.static_lines))
        has_lyric_bands = bool(_group_text(previous + state.active + following))
        self.active_band.setVisible(bool(state.active) and has_lyric_bands)
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
        details_available = bool(
            state.lyrics_source
            or state.match_confidence
            or state.sync_health
            or state.diagnostics
            or state.player
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
        possible_matches = state.state in {
            DesktopLyricsState.AMBIGUOUS,
            DesktopLyricsState.NO_RESULT,
        }
        self.review_button.setText(
            "Find lyrics…" if possible_matches else "Review lyrics"
        )
        self.review_button.setFlat(not possible_matches)
        self.review_button.setAccessibleName(
            "Possible lyrics matches"
            if possible_matches
            else "Review and correct this track and lyrics"
        )
        self.review_button.setToolTip(
            "Search, refresh, and choose a possible lyrics match"
            if possible_matches
            else "Review the detected track and lyrics match"
        )
        self.review_action.setText(
            "&Possible lyrics matches…"
            if possible_matches
            else "&Review track and lyrics…"
        )
        self.review_action.setEnabled(self.review_button.isEnabled())
        self.details_action.setEnabled(details_available)
        self._apply_visibility()
        self._lyric_column.transition(
            transition_anchor,
            appearance.motion.effective_transition_ms,
            appearance.motion.effective_emphasis_transition_ms,
        )

    @staticmethod
    def _lyric_transition_direction(
        previous: DesktopViewState, current: DesktopViewState
    ) -> int:
        """Return adjacent direction only; seeks and source changes snap promptly."""

        if (
            previous.state is not DesktopLyricsState.TIMED
            or current.state is not DesktopLyricsState.TIMED
            or previous.generation != current.generation
            or previous.title != current.title
            or previous.artists != current.artists
            or previous.player != current.player
        ):
            return 0
        old_active = tuple(group.line_id for group in previous.active)
        new_active = tuple(group.line_id for group in current.active)
        if not new_active or new_active == old_active:
            return 0
        if previous.next and previous.next[0].line_id in new_active:
            return 1
        if previous.previous and previous.previous[-1].line_id in new_active:
            return -1
        return 0

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
        mode = self.window_mode
        lyric_only = mode is DesktopWindowMode.OVERLAY
        focused = mode is DesktopWindowMode.FULLSCREEN
        compact = mode is DesktopWindowMode.COMPACT
        show_metadata = not lyric_only
        show_title = show_metadata and visibility.title
        show_artist = show_metadata and visibility.artist and bool(state.artists)
        show_album = (
            show_metadata and not compact and visibility.album and bool(state.album)
        )
        show_artwork = (
            show_metadata
            and not compact
            and self.width() >= 600
            and self._appearance.artwork.visible
            and self.artwork.has_artwork
        )
        show_actions = mode is DesktopWindowMode.NORMAL and visibility.chrome
        self.artwork.setVisible(show_artwork)
        self.title_label.setVisible(show_title)
        self.artist_label.setVisible(show_artist)
        self.album_label.setVisible(show_album)
        self._actions_widget.setVisible(show_actions)
        self._header_rule.setVisible(
            show_actions or show_title or show_artist or show_album or show_artwork
        )
        self._metadata_widget.setVisible(show_title or show_artist or show_album)
        show_context = visibility.inactive_context and not compact
        self._lyric_column.set_context_visibility(
            show_context and bool(self.previous_band.text()),
            show_context and bool(self.next_band.text()),
        )
        self.status_label.setVisible(visibility.auxiliary_status)
        self.static_status_label.setVisible(visibility.auxiliary_status)
        self.playback_label.setVisible(visibility.playback_status)
        self.progress.setVisible(
            not lyric_only
            and visibility.progress
            and state.progress_fraction is not None
        )
        self.time_label.setVisible(not lyric_only and visibility.timestamps)
        has_playback = (
            state.player is not None
            or state.playback_state is not PlaybackState.UNKNOWN
            or state.position_us is not None
        )
        self.playback_widget.setVisible(
            not lyric_only
            and has_playback
            and (
                visibility.playback_status
                or visibility.progress
                or visibility.timestamps
            )
        )
        self.source_label.setVisible(
            not lyric_only
            and not focused
            and not compact
            and visibility.source
            and bool(_normal_status_parts(state))
        )

    def _open_settings(self) -> None:
        self.settings_requested.emit()

    def _about(self) -> None:
        from konokashi import DISPLAY_VERSION

        QMessageBox.about(
            self,
            "About KonoKashi",
            f"KonoKashi {DISPLAY_VERSION}\nLocal-first synchronized lyrics for Linux.",
        )

    def _open_details(self) -> None:
        DiagnosticsDialog(self._state, self).exec()

    def _toggle_library_scan(self) -> None:
        if self._library_scan_running:
            self.library_scan_cancel_requested.emit()
        else:
            self.library_scan_requested.emit()

    def set_library_scan_state(self, running: bool, message: str) -> None:
        """Expose background scan/cancel state without replacing lyric content."""

        self._library_scan_running = running
        self.scan_action.setText("Cancel &scan" if running else "Scan &library")
        self.scan_action.setToolTip(escape(message))
        self.scan_action.setStatusTip(message)

    def set_library_scan_result(
        self,
        summary: LibraryScanSummary,
        review_items: tuple[LibraryReviewItem, ...],
    ) -> None:
        """Retain one bounded local review model until the next scan completes."""

        self._library_result = (summary, review_items)
        self.library_results_button.setVisible(
            bool(summary.errors or summary.review or review_items)
        )

    def _open_library_results(self) -> None:
        if self._library_result is None:
            return
        summary, review_items = self._library_result
        dialog = LibraryReviewDialog(summary, review_items, self)
        dialog.scan_again_requested.connect(self.library_scan_requested.emit)
        dialog.exec()

    def show_review(self, snapshot: ReviewCorrectionSnapshot) -> None:
        """Render one source-bound review model and emit at most one action."""

        dialog = ReviewCorrectionDialog(
            snapshot,
            self,
            position_ms=lambda: (
                None
                if self._state.position_us is None
                else max(0, self._state.position_us // 1_000)
            ),
        )
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
    """Return only listening-state information that merits attention."""

    parts: list[str] = []
    confidence = state.match_confidence
    if confidence in {"Medium", "Low", "Rejected"}:
        parts.append(
            {
                "Medium": "Review suggested",
                "Low": "Uncertain match",
                "Rejected": "Rejected match",
            }[confidence]
        )
    health = state.sync_health
    if health is not None and health not in {
        ClockHealth.PAUSED,
        ClockHealth.LOCKED,
    }:
        parts.append(
            {
                ClockHealth.CONVERGING: "Syncing",
                ClockHealth.DEGRADED: "Sync needs attention",
                ClockHealth.STALE: "Sync is stale",
                ClockHealth.UNAVAILABLE: "Sync unavailable",
                ClockHealth.DISCONTINUITY: "Resynchronizing",
            }[health]
        )
    return tuple(parts)
