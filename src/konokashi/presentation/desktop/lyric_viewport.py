"""Measured multilingual lyric bands and adaptive transition viewport."""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import (
    Property,
    QAbstractAnimation,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QHideEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QResizeEvent,
    QTextCharFormat,
    QTextLayout,
    QTextLine,
    QTextOption,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from konokashi.application.appearance import (
    AppearanceProfile,
    default_appearance_profile,
)
from konokashi.application.desktop_state import (
    DesktopKaraokeSegment,
    DesktopLyricGroup,
)
from konokashi.presentation.desktop.widget_style import (
    _apply_text_palette,
    _qt_alignment,
    _semantic_color,
    _styled_font,
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


def _reading_layer_name(group: DesktopLyricGroup) -> str:
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
    return layer


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
    """Reusable original, reading, and translation hierarchy."""

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
        self._region = region
        self.original = _lyric_layer_label(f"Original {region} lyric")
        self.romanized = _lyric_layer_label(f"Romanized {region} lyric")
        self.translation = _lyric_layer_label(f"Translated {region} lyric")
        layout.addWidget(self.original)
        layout.addWidget(self.romanized)
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
        self.romanized.setAccessibleName(
            f"{_reading_layer_name(group)} {self._region} lyric"
        )
        self.translation.setAccessibleName(f"Translation {self._region} lyric")
        self.original.set_karaoke_segments(
            group.karaoke_segments if self._active else ()
        )
        if layout_changed:
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
                self.romanized,
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

    def set_visible_group_limit(self, limit: int) -> None:
        bounded = min(self._MAX_VISIBLE_GROUPS, max(0, limit))
        if bounded == self._visible_group_limit:
            return
        self._visible_group_limit = bounded
        self._render_groups()

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
