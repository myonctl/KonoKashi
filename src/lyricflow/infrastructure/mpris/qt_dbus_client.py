"""Production PySide6 QtDBus backend and CLI event-loop runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TypeAlias

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtDBus import (
    QDBusConnection,
    QDBusMessage,
    QDBusPendingCallWatcher,
)

from lyricflow.infrastructure.mpris.backend import (
    MprisBackendError,
    MprisServiceUnavailable,
    PropertiesChangedHandler,
    SeekedHandler,
    ServiceHandler,
)
from lyricflow.infrastructure.mpris.metadata_mapper import MPRIS_OBJECT_PATH
from lyricflow.infrastructure.mpris.player_registry import MprisClient, MprisMonitor
from lyricflow.infrastructure.mpris.qt_dbus_values import (
    PROPERTIES_SLOT,
    SEEKED_SLOT,
    PlayerSignalReceiver,
    plain_dbus_value,
    plain_mapping,
)

DBUS_SERVICE = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"
DBUS_INTERFACE = "org.freedesktop.DBus"
DBUS_PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"

_CloseCallback: TypeAlias = Callable[[], None]


def _error_message(name: str, message: str) -> str:
    detail = message.strip() or "no diagnostic message"
    return f"{name or 'D-Bus error'}: {detail}"


class _CallableSubscription:
    def __init__(self, close_callback: _CloseCallback) -> None:
        self._close_callback: _CloseCallback | None = close_callback

    def close(self) -> None:
        callback = self._close_callback
        self._close_callback = None
        if callback is not None:
            callback()


class QtDbusBackend:
    """Session-bus implementation using asynchronous QtDBus method calls."""

    def __init__(
        self,
        connection: QDBusConnection | None = None,
        *,
        timeout_ms: int = 5_000,
    ) -> None:
        self._connection = connection or QDBusConnection.sessionBus()
        self._timeout_ms = timeout_ms
        self._connection_interface = self._connection.interface()

    def _ensure_connected(self) -> None:
        if self._connection.isConnected():
            return
        error = self._connection.lastError()
        raise MprisBackendError(_error_message(error.name(), error.message()))

    def _call(
        self,
        service: str,
        path: str,
        interface: str,
        method: str,
        arguments: Sequence[object] = (),
    ) -> tuple[object, ...]:
        self._ensure_connected()
        message = QDBusMessage.createMethodCall(service, path, interface, method)
        message.setArguments(list(arguments))
        pending = self._connection.asyncCall(message, self._timeout_ms)
        loop = QEventLoop()
        watcher = QDBusPendingCallWatcher(pending, loop)
        timeout = QTimer(loop)
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        watcher.finished.connect(loop.quit)
        timeout.start(self._timeout_ms + 250)
        if not watcher.isFinished():
            loop.exec()
        if not watcher.isFinished():
            raise MprisBackendError(
                f"D-Bus call {method} did not finish within {self._timeout_ms} ms"
            )
        if watcher.isError():
            error = watcher.error()
            text = _error_message(error.name(), error.message())
            unavailable_names = {
                "org.freedesktop.DBus.Error.NameHasNoOwner",
                "org.freedesktop.DBus.Error.ServiceUnknown",
                "org.freedesktop.DBus.Error.UnknownObject",
            }
            if error.name() in unavailable_names:
                raise MprisServiceUnavailable(text)
            raise MprisBackendError(text)
        return tuple(plain_dbus_value(value) for value in watcher.reply().arguments())

    def list_service_names(self) -> Sequence[str]:
        """List current session-bus names using the D-Bus daemon."""

        arguments = self._call(
            DBUS_SERVICE,
            DBUS_PATH,
            DBUS_INTERFACE,
            "ListNames",
        )
        if len(arguments) != 1 or not isinstance(arguments[0], Sequence):
            raise MprisBackendError("ListNames returned an unexpected payload")
        names = arguments[0]
        if isinstance(names, str) or not all(isinstance(name, str) for name in names):
            raise MprisBackendError("ListNames returned a malformed service list")
        return tuple(names)

    def get_all(self, service: str, interface: str) -> Mapping[str, object]:
        """Read an interface's properties through org.freedesktop.DBus.Properties."""

        arguments = self._call(
            service,
            MPRIS_OBJECT_PATH,
            DBUS_PROPERTIES_INTERFACE,
            "GetAll",
            (interface,),
        )
        if len(arguments) != 1:
            raise MprisBackendError("GetAll returned an unexpected argument count")
        return plain_mapping(arguments[0])

    def get_property(self, service: str, interface: str, name: str) -> object:
        """Read one property, notably Position, without rapid polling."""

        arguments = self._call(
            service,
            MPRIS_OBJECT_PATH,
            DBUS_PROPERTIES_INTERFACE,
            "Get",
            (interface, name),
        )
        if len(arguments) != 1:
            raise MprisBackendError("Get returned an unexpected argument count")
        return arguments[0]

    def subscribe_service_changes(
        self,
        on_registered: ServiceHandler,
        on_unregistered: ServiceHandler,
    ) -> _CallableSubscription:
        """Use the connection interface's service lifecycle signals."""

        self._ensure_connected()
        self._connection_interface.serviceRegistered.connect(on_registered)
        self._connection_interface.serviceUnregistered.connect(on_unregistered)

        def close() -> None:
            self._connection_interface.serviceRegistered.disconnect(on_registered)
            self._connection_interface.serviceUnregistered.disconnect(on_unregistered)

        return _CallableSubscription(close)

    def subscribe_player_signals(
        self,
        service: str,
        on_properties_changed: PropertiesChangedHandler,
        on_seeked: SeekedHandler,
    ) -> _CallableSubscription:
        """Attach PropertiesChanged and Seeked directly to one MPRIS service."""

        self._ensure_connected()
        receiver = PlayerSignalReceiver(service, on_properties_changed, on_seeked)
        properties_connected = self._connection.connect(
            service,
            MPRIS_OBJECT_PATH,
            DBUS_PROPERTIES_INTERFACE,
            "PropertiesChanged",
            receiver,
            PROPERTIES_SLOT,
        )
        seeked_connected = self._connection.connect(
            service,
            MPRIS_OBJECT_PATH,
            "org.mpris.MediaPlayer2.Player",
            "Seeked",
            receiver,
            SEEKED_SLOT,
        )
        if not properties_connected or not seeked_connected:
            if properties_connected:
                self._connection.disconnect(
                    service,
                    MPRIS_OBJECT_PATH,
                    DBUS_PROPERTIES_INTERFACE,
                    "PropertiesChanged",
                    receiver,
                    PROPERTIES_SLOT,
                )
            if seeked_connected:
                self._connection.disconnect(
                    service,
                    MPRIS_OBJECT_PATH,
                    "org.mpris.MediaPlayer2.Player",
                    "Seeked",
                    receiver,
                    SEEKED_SLOT,
                )
            error = self._connection.lastError()
            raise MprisBackendError(_error_message(error.name(), error.message()))

        def close() -> None:
            self._connection.disconnect(
                service,
                MPRIS_OBJECT_PATH,
                DBUS_PROPERTIES_INTERFACE,
                "PropertiesChanged",
                receiver,
                PROPERTIES_SLOT,
            )
            self._connection.disconnect(
                service,
                MPRIS_OBJECT_PATH,
                "org.mpris.MediaPlayer2.Player",
                "Seeked",
                receiver,
                SEEKED_SLOT,
            )
            receiver.deleteLater()

        return _CallableSubscription(close)


class QtMprisRuntime:
    """Own the Qt event loop and Stage 1 discovery/monitor ports."""

    def __init__(self, application: QCoreApplication, backend: QtDbusBackend) -> None:
        self._application = application
        self._signal_timer = QTimer(application)
        self._signal_timer.setInterval(250)
        self._signal_timer.timeout.connect(lambda: None)
        self.client = MprisClient(backend)
        self.monitor = MprisMonitor(backend)

    def exec(self) -> int:
        """Run Qt while periodically returning to Python for SIGINT delivery."""

        self._signal_timer.start()
        try:
            return self._application.exec()
        finally:
            self._signal_timer.stop()

    def quit(self) -> None:
        """Request a clean stop of the Qt event loop."""

        self._application.quit()


def create_qt_mpris_runtime() -> QtMprisRuntime:
    """Build the production MPRIS runtime using the session D-Bus."""

    application = QCoreApplication.instance()
    if application is None:
        application = QCoreApplication(["lyricflow"])
    if not isinstance(application, QCoreApplication):
        raise MprisBackendError("existing Qt application is not a QCoreApplication")
    return QtMprisRuntime(application, QtDbusBackend())
