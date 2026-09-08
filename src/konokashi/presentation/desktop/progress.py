"""Semantic playback progress painting independent of platform widget styling."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPaintEvent
from PySide6.QtWidgets import QProgressBar, QWidget

from konokashi.application.appearance import (
    AppearanceProfile,
    default_appearance_profile,
)


def _color(value: str, opacity: int) -> QColor:
    color = QColor(value[:7])
    alpha = int(value[7:9], 16) if len(value) == 9 else 255
    color.setAlpha(round(alpha * opacity / 100))
    return color


class PlaybackProgress(QProgressBar):
    """Retain Qt's accessible range/value model with restrained semantic paint."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTextVisible(False)
        self.setRange(0, 1000)
        self.set_profile(default_appearance_profile())

    def set_profile(self, profile: AppearanceProfile) -> None:
        self._profile = profile
        self.setFixedHeight(profile.progress.thickness)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        profile = self._profile
        style = profile.progress
        opacity = round(style.opacity * profile.opacity.content / 100)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect())
        radius = min(style.corner_radius, bounds.height() / 2)
        path = QPainterPath()
        path.addRoundedRect(bounds, radius, radius)
        painter.fillPath(path, _color(style.track_color, opacity))
        span = self.maximum() - self.minimum()
        fraction = (self.value() - self.minimum()) / span if span > 0 else 0
        fraction = max(0.0, min(1.0, fraction))
        painter.setClipPath(path)
        width = bounds.width() * fraction
        left = (
            bounds.width() - width
            if self.layoutDirection() == Qt.LayoutDirection.RightToLeft
            else 0
        )
        painter.fillRect(
            QRectF(left, 0, width, bounds.height()),
            _color(profile.colors.progress, opacity),
        )
