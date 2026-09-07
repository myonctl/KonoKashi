"""Event-driven Qt hot reload for atomically replaced TOML files."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer

from konokashi.application.settings_service import (
    CanonicalSettingsService,
    SettingsReloadResult,
)


class QtSettingsWatcher(QObject):
    """Debounce file/directory events and re-arm after atomic replacement."""

    def __init__(
        self,
        service: CanonicalSettingsService,
        callback: Callable[[SettingsReloadResult], None],
        *,
        debounce_ms: int = 100,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._callback = callback
        self._watcher = QFileSystemWatcher(self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self._reload)
        self._watcher.fileChanged.connect(self._schedule)
        self._watcher.directoryChanged.connect(self._schedule)
        service.path.parent.mkdir(parents=True, exist_ok=True)
        self._arm()

    def close(self) -> None:
        self._timer.stop()
        watched = (*self._watcher.files(), *self._watcher.directories())
        if watched:
            self._watcher.removePaths(list(watched))

    def _arm(self) -> None:
        parent = str(self._service.path.parent)
        path = str(self._service.path)
        if parent not in self._watcher.directories():
            self._watcher.addPath(parent)
        if self._service.path.exists() and path not in self._watcher.files():
            self._watcher.addPath(path)

    def _schedule(self, _path: str) -> None:
        self._timer.start()

    def _reload(self) -> None:
        self._arm()
        self._callback(self._service.reload())
        self._arm()
