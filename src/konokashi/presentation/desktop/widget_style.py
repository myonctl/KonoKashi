"""Shared conversions from appearance values to concrete Qt styling."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QWidget

from konokashi.application.appearance import TextAlignment, TextStyle


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
