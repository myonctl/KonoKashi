"""Stable terminal controls and source-bound user edit messages."""

from __future__ import annotations

from typing import Self

from textual.geometry import Size
from textual.message import Message
from textual.reactive import Reactive
from textual.widgets import Input, OptionList, Switch


class FixedSurfaceInput(Input):
    """Fixed CSS geometry owns layout rather than each text edit."""

    virtual_size = Reactive(Size(0, 0), layout=False)


class FixedSurfaceOptionList(OptionList):
    """Scroll inside the fixed box without reflowing its parents."""

    virtual_size = Reactive(Size(0, 0), layout=False)


class SettingSwitch(Switch):
    """Distinguish snapshot projection from a user activation of an exact key."""

    class Edited(Message):
        def __init__(self, key: str, value: bool) -> None:
            super().__init__()
            self.key = key
            self.value = value

    def __init__(self, *, id: str) -> None:
        super().__init__(id=id, animate=False)
        self.setting_key = ""

    def project(self, key: str, value: bool) -> None:
        self.setting_key = key
        with self.prevent(Switch.Changed):
            self.value = value

    def toggle(self) -> Self:
        if not self.disabled and self.setting_key:
            key = self.setting_key
            with self.prevent(Switch.Changed):
                super().toggle()
            self.post_message(self.Edited(key, self.value))
        return self
