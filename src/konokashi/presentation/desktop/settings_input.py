"""Shared protection against accidental wheel edits in desktop preferences."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDial,
    QScrollArea,
    QSlider,
    QWidget,
)


class SettingsWheelGuard(QObject):
    """Wheel gestures over value editors scroll their containing settings page."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, QWidget):
            return False
        widget: QWidget | None = watched
        value_editor = False
        while widget is not None:
            value_editor |= isinstance(
                widget, (QAbstractSpinBox, QComboBox, QSlider, QDial)
            )
            if isinstance(widget, QScrollArea):
                if value_editor:
                    QApplication.sendEvent(widget.viewport(), event)
                    event.accept()
                    return True
                return False
            widget = widget.parentWidget()
        return False
