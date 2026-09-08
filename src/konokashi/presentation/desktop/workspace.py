"""Stable presentation panel ownership, without a premature movable layout UI."""

from __future__ import annotations

from enum import Enum

from PySide6.QtWidgets import QVBoxLayout, QWidget


class PanelId(Enum):
    METADATA = "metadata"
    LYRICS = "lyrics"
    PROGRESS = "progress"
    STATUS = "status"


class DesktopWorkspace(QWidget):
    """Own complete panel widgets; application state remains outside the host.

    A future dock host can reparent these widgets without recreating lyric
    renderers or starting another playback session. Current placement is fixed.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.panel_layout = QVBoxLayout(self)
        self._panels: dict[PanelId, QWidget] = {}

    def add_panel(
        self, identity: PanelId, widget: QWidget, *, stretch: int = 0
    ) -> None:
        if identity in self._panels:
            raise ValueError(f"Panel already registered: {identity.value}")
        if widget in self._panels.values():
            raise ValueError("A widget cannot represent multiple panels")
        widget.setObjectName(f"panel-{identity.value}")
        self._panels[identity] = widget
        self.panel_layout.addWidget(widget, stretch)

    def panel(self, identity: PanelId) -> QWidget:
        return self._panels[identity]

    @property
    def panel_ids(self) -> tuple[PanelId, ...]:
        return tuple(self._panels)
