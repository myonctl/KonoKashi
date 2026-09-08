"""Ordinary decorated desktop surface with independently painted background alpha."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPaintEvent
from PySide6.QtWidgets import QMainWindow, QWidget


class DesktopWindowSurface(QMainWindow):
    """Request alpha before native creation; unsupported platforms stay opaque."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        platform = QGuiApplication.platformName()
        self._alpha_requested = platform in {
            "wayland",
            "wayland-egl",
            "xcb",
            "offscreen",
        }
        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, self._alpha_requested
        )
        self._background_color = QColor("#202124")

    @property
    def background_transparency_available(self) -> bool:
        if not self._alpha_requested:
            return False
        handle = self.windowHandle()
        return handle is None or handle.format().alphaBufferSize() > 0

    def set_background_color(self, color: QColor) -> None:
        self._background_color = QColor(color)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        color = QColor(self._background_color)
        if not self.background_transparency_available:
            color.setAlpha(255)
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(event.rect(), color)
