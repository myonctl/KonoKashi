"""Bounded QtDBus access to the XDG desktop directory chooser portal."""

from __future__ import annotations

import ctypes
from contextlib import suppress
from pathlib import Path
from typing import cast
from uuid import uuid4

from PySide6.QtCore import SLOT, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtDBus import (
    QDBusArgument,
    QDBusConnection,
    QDBusMessage,
    QDBusObjectPath,
    QDBusPendingCallWatcher,
    QDBusVariant,
)
from PySide6.QtGui import QGuiApplication, QWindow
from shiboken6 import getCppPointer

PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
FILE_CHOOSER_INTERFACE = "org.freedesktop.portal.FileChooser"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"
RESPONSE_SLOT = cast(bytes, SLOT("response(uint,QVariantMap)"))
_REQUEST_PATH_PREFIX = "/org/freedesktop/portal/desktop/request/"
_METHOD_TIMEOUT_MS = 10_000
_PARENT_IDENTIFIER_CAPACITY = 1024
_PARENT_BRIDGE = Path("/app/lib/libkonokashi_portal_parent.so")


class DesktopPortalError(RuntimeError):
    """A controlled desktop-portal failure safe to show in Settings."""


def is_flatpak_session(info_path: Path = Path("/.flatpak-info")) -> bool:
    """Return whether the process is running inside a Flatpak sandbox."""

    return info_path.is_file()


def portal_parent_identifier(
    window: QWindow | None,
    *,
    bridge_path: Path = _PARENT_BRIDGE,
) -> str:
    """Return a portal window identifier for a realized Qt window."""

    if window is None:
        return ""
    platform = QGuiApplication.platformName()
    if platform == "xcb":
        return f"x11:{int(window.winId()):x}"
    if not platform.startswith("wayland"):
        return ""
    if not bridge_path.is_file():
        raise DesktopPortalError(
            "the Flatpak portal parent bridge is missing from this installation"
        )
    try:
        bridge = ctypes.CDLL(str(bridge_path))
        export = bridge.konokashi_portal_parent_identifier
        export.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        export.restype = ctypes.c_size_t
        pointer = getCppPointer(window)[0]
        buffer = ctypes.create_string_buffer(_PARENT_IDENTIFIER_CAPACITY)
        length = export(ctypes.c_void_p(pointer), buffer, len(buffer))
    except (AttributeError, IndexError, OSError) as error:
        raise DesktopPortalError(
            "the Flatpak portal parent bridge could not be loaded"
        ) from error
    if not isinstance(length, int) or length <= 0 or length >= len(buffer):
        raise DesktopPortalError(
            "the desktop could not export the KonoKashi window to the portal"
        )
    encoded = bytes(buffer.raw[:length])
    try:
        identifier = encoded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DesktopPortalError(
            "the desktop returned an invalid portal window identifier"
        ) from error
    if not identifier.startswith("wayland:") or len(identifier) == len("wayland:"):
        raise DesktopPortalError(
            "the desktop returned an invalid portal window identifier"
        )
    return identifier


def _unwrap(value: object) -> object:
    """Remove the QtDBus wrappers used by the two portal reply shapes."""

    seen: set[tuple[type[object], str]] = set()
    while isinstance(value, (QDBusVariant, QDBusArgument)):
        signature = (
            value.currentSignature() if isinstance(value, QDBusArgument) else "variant"
        )
        state = (type(value), signature)
        if state in seen:
            raise DesktopPortalError("the desktop portal returned an unreadable value")
        seen.add(state)
        value = (
            value.asVariant() if isinstance(value, QDBusArgument) else value.variant()
        )
    if isinstance(value, QDBusObjectPath):
        return value.path()
    return value


def selected_directory(results: object) -> str:
    """Validate one successful FileChooser response and return its local path."""

    results = _unwrap(results)
    if not isinstance(results, dict):
        raise DesktopPortalError("the desktop portal returned invalid chooser results")
    uris = _unwrap(results.get("uris"))
    if not isinstance(uris, (list, tuple)) or len(uris) != 1:
        raise DesktopPortalError("the desktop portal did not return one folder")
    uri = _unwrap(uris[0])
    if not isinstance(uri, str):
        raise DesktopPortalError("the desktop portal returned an invalid folder URI")
    url = QUrl(uri)
    if not url.isValid() or not url.isLocalFile():
        raise DesktopPortalError("the selected portal location is not a local folder")
    selected = url.toLocalFile()
    if not selected or not Path(selected).is_absolute():
        raise DesktopPortalError("the desktop portal returned an invalid local path")
    return selected


class _ResponseReceiver(QObject):
    def __init__(self, request: PortalDirectoryRequest) -> None:
        super().__init__(request)
        self._request = request

    @Slot("uint", "QVariantMap")
    def response(self, response: int, results: object) -> None:
        """Forward the standard Request.Response signal to its owner."""

        self._request._response_received(response, results)


class PortalDirectoryRequest(QObject):
    """Own one non-blocking FileChooser request and its exact signal match."""

    finished = Signal(object, object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        connection: QDBusConnection | None = None,
        token: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._connection = connection or QDBusConnection.sessionBus()
        self._token = token or f"konokashi_{uuid4().hex}"
        self._receiver = _ResponseReceiver(self)
        self._watcher: QDBusPendingCallWatcher | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._method_timed_out)
        self._request_path: str | None = None
        self._connected_path: str | None = None
        self._done = False
        if parent is not None:
            parent.destroyed.connect(lambda _object=None: self.cancel())

    def start(
        self,
        title: str = "Add music library folder",
        *,
        parent_window: str = "",
    ) -> None:
        """Subscribe first, then issue one asynchronous directory request."""

        if self._watcher is not None or self._done:
            raise RuntimeError("portal directory request has already been started")
        if not self._connection.isConnected():
            self._complete(
                None,
                DesktopPortalError("the desktop portal session bus is unavailable"),
            )
            return
        unique_name = self._connection.baseService()
        if not unique_name.startswith(":"):
            self._complete(
                None,
                DesktopPortalError("the desktop portal session has no unique bus name"),
            )
            return
        sender = unique_name[1:].replace(".", "_")
        predicted_path = f"{_REQUEST_PATH_PREFIX}{sender}/{self._token}"
        try:
            self._connect_response(predicted_path)
        except DesktopPortalError as error:
            self._complete(None, error)
            return

        message = QDBusMessage.createMethodCall(
            PORTAL_SERVICE,
            PORTAL_PATH,
            FILE_CHOOSER_INTERFACE,
            "OpenFile",
        )
        message.setArguments(
            [
                parent_window,
                title,
                {
                    # PySide marshals mapping values as D-Bus variants. Passing
                    # QDBusVariant here would produce an invalid nested variant.
                    "directory": True,
                    "multiple": False,
                    "modal": True,
                    "handle_token": self._token,
                },
            ]
        )
        pending = self._connection.asyncCall(message, _METHOD_TIMEOUT_MS)
        self._watcher = QDBusPendingCallWatcher(pending, self)
        self._watcher.finished.connect(self._method_finished)
        self._timer.start(_METHOD_TIMEOUT_MS + 250)
        if self._watcher.isFinished():
            QTimer.singleShot(0, self._method_finished)

    def cancel(self) -> None:
        """Close an outstanding portal request and suppress its UI callback."""

        if self._done:
            return
        self._done = True
        self._timer.stop()
        request_path = self._request_path or self._connected_path
        self._disconnect_response()
        if request_path is not None and self._connection.isConnected():
            message = QDBusMessage.createMethodCall(
                PORTAL_SERVICE,
                request_path,
                REQUEST_INTERFACE,
                "Close",
            )
            self._connection.asyncCall(message, _METHOD_TIMEOUT_MS)

    def _connect_response(self, request_path: str) -> None:
        if not request_path.startswith(_REQUEST_PATH_PREFIX):
            raise DesktopPortalError(
                "the desktop portal returned an invalid request path"
            )
        if self._connected_path == request_path:
            return
        self._disconnect_response()
        try:
            connected = self._connection.connect(
                PORTAL_SERVICE,
                request_path,
                REQUEST_INTERFACE,
                "Response",
                self._receiver,
                RESPONSE_SLOT,
            )
        except (TypeError, ValueError) as binding_error:
            raise DesktopPortalError(
                "the installed Qt runtime rejected the portal response signature"
            ) from binding_error
        if not connected:
            connection_error = self._connection.lastError()
            detail = (
                connection_error.message().strip()
                or connection_error.name()
                or "unknown D-Bus error"
            )
            raise DesktopPortalError(
                f"the desktop portal response could not be connected ({detail})"
            )
        self._connected_path = request_path

    def _disconnect_response(self) -> None:
        request_path = self._connected_path
        self._connected_path = None
        if request_path is None:
            return
        with suppress(RuntimeError, TypeError):
            self._connection.disconnect(
                PORTAL_SERVICE,
                request_path,
                REQUEST_INTERFACE,
                "Response",
                self._receiver,
                RESPONSE_SLOT,
            )

    @Slot()
    def _method_timed_out(self) -> None:
        self._complete(
            None,
            DesktopPortalError("the desktop portal did not open the chooser in time"),
        )

    @Slot()
    def _method_finished(self) -> None:
        watcher = self._watcher
        if self._done or watcher is None:
            return
        self._timer.stop()
        if watcher.isError():
            error = watcher.error()
            detail = error.message().strip() or error.name() or "unknown D-Bus error"
            self._complete(
                None,
                DesktopPortalError(
                    f"the desktop portal could not open the folder chooser ({detail})"
                ),
            )
            return
        arguments = watcher.reply().arguments()
        if len(arguments) != 1:
            self._complete(
                None,
                DesktopPortalError(
                    "the desktop portal returned an invalid request handle"
                ),
            )
            return
        request_path = _unwrap(arguments[0])
        if not isinstance(request_path, str):
            self._complete(
                None,
                DesktopPortalError(
                    "the desktop portal returned an invalid request handle"
                ),
            )
            return
        try:
            self._connect_response(request_path)
        except DesktopPortalError as error:
            self._complete(None, error)
            return
        self._request_path = request_path

    def _response_received(self, response: int, results: object) -> None:
        if self._done:
            return
        if response == 1:
            self._complete(None, None)
            return
        if response != 0:
            self._complete(
                None,
                DesktopPortalError("the desktop portal could not select that folder"),
            )
            return
        try:
            directory = selected_directory(results)
        except DesktopPortalError as error:
            self._complete(None, error)
            return
        self._complete(directory, None)

    def _complete(
        self,
        directory: str | None,
        error: DesktopPortalError | None,
    ) -> None:
        if self._done:
            return
        self._done = True
        self._timer.stop()
        self._disconnect_response()
        self.finished.emit(directory, error)
