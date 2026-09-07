"""Thread-safe result messages used by the settings TUI."""

from __future__ import annotations

from textual.message import Message

from konokashi.application.settings_service import (
    CanonicalSettingsService,
    SettingsChange,
    SettingsReloadResult,
)
from konokashi.presentation.tui.settings_runtime import ConfigSignature


class ServiceReady(Message):
    def __init__(
        self, service: CanonicalSettingsService | None, error: str | None = None
    ) -> None:
        super().__init__()
        self.service = service
        self.error = error


class SnapshotObserved(Message):
    def __init__(self, change: SettingsChange) -> None:
        super().__init__()
        self.change = change


class MutationFinished(Message):
    def __init__(
        self,
        key: str,
        result: SettingsReloadResult | None,
        error: str | None = None,
        signature: ConfigSignature | None = None,
    ) -> None:
        super().__init__()
        self.key = key
        self.result = result
        self.error = error
        self.signature = signature


class DiskObserved(Message):
    def __init__(
        self,
        signature: ConfigSignature,
        result: SettingsReloadResult | None,
    ) -> None:
        super().__init__()
        self.signature = signature
        self.result = result
