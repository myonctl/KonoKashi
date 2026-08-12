"""Production PySide6 QtDBus backend and CLI event-loop runtime."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import NoReturn, TypeAlias, cast

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtDBus import (
    QDBusConnection,
    QDBusError,
    QDBusInterface,
    QDBusMessage,
    QDBusPendingCallWatcher,
)

from lyricflow.infrastructure.mpris.backend import (
    MprisBackendError,
    MprisPropertyRead,
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

_RAW_PROPERTY_MAP_ERROR = (
    "QDBusArgument conversion made no progress for signature a{sv}"
)


def _error_message(name: str, message: str) -> str:
    detail = message.strip() or "no diagnostic message"
    return f"{name or 'D-Bus error'}: {detail}"


def _raise_call_error(error: QDBusError) -> NoReturn:
    text = _error_message(error.name(), error.message())
    unavailable_names = {
        "org.freedesktop.DBus.Error.NameHasNoOwner",
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "org.freedesktop.DBus.Error.UnknownObject",
    }
    if error.name() in unavailable_names:
        raise MprisServiceUnavailable(text)
    raise MprisBackendError(text)


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
        self._raw_get_all_observed = False

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
            _raise_call_error(watcher.error())
        return tuple(plain_dbus_value(value) for value in watcher.reply().arguments())

    def _remote_interface(self, service: str, interface: str) -> QDBusInterface:
        """Create a bounded typed-property interface for one MPRIS object."""

        self._ensure_connected()
        remote = QDBusInterface(
            service,
            MPRIS_OBJECT_PATH,
            interface,
            self._connection,
        )
        remote.setTimeout(self._timeout_ms)
        if not remote.isValid():
            _raise_call_error(remote.lastError())
        return remote

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

    def _read_properties_individually(
        self,
        service: str,
        interface: str,
        get_all_error: MprisBackendError,
    ) -> MprisPropertyRead:
        """Fall back to independent typed reads after a broken GetAll call."""

        remote = self._remote_interface(service, interface)
        meta_object = remote.metaObject()
        properties: dict[str, object] = {}
        diagnostics = []
        if _RAW_PROPERTY_MAP_ERROR not in str(get_all_error):
            diagnostics.append(
                f"GetAll: unavailable ({get_all_error}); used individual property reads"
            )
        for index in range(meta_object.propertyOffset(), meta_object.propertyCount()):
            property_meta = meta_object.property(index)
            if not property_meta.isReadable():
                continue
            name = cast(str, property_meta.name())
            value = remote.property(name)
            error = remote.lastError()
            if error.isValid():
                try:
                    _raise_call_error(error)
                except MprisServiceUnavailable:
                    raise
                except MprisBackendError as property_error:
                    diagnostics.append(f"{name}: unavailable ({property_error})")
                    continue
            try:
                properties[name] = plain_dbus_value(value)
            except MprisBackendError as property_error:
                diagnostics.append(f"{name}: unavailable ({property_error})")
        return MprisPropertyRead(plain_mapping(properties), tuple(diagnostics))

    def read_properties(self, service: str, interface: str) -> MprisPropertyRead:
        """Prefer GetAll, falling back when one player or binding cannot decode it."""

        if self._raw_get_all_observed:
            return self._read_properties_individually(
                service,
                interface,
                MprisBackendError(_RAW_PROPERTY_MAP_ERROR),
            )
        try:
            arguments = self._call(
                service,
                MPRIS_OBJECT_PATH,
                DBUS_PROPERTIES_INTERFACE,
                "GetAll",
                (interface,),
            )
            if len(arguments) != 1:
                raise MprisBackendError("GetAll returned an unexpected payload")
            return MprisPropertyRead(plain_mapping(arguments[0]))
        except MprisServiceUnavailable:
            raise
        except MprisBackendError as error:
            if _RAW_PROPERTY_MAP_ERROR in str(error):
                self._raw_get_all_observed = True
            return self._read_properties_individually(service, interface, error)

    def get_property(self, service: str, interface: str, name: str) -> object:
        """Read one property, notably Position, without rapid polling."""

        remote = self._remote_interface(service, interface)
        property_index = remote.metaObject().indexOfProperty(name)
        if property_index < 0:
            raise MprisBackendError(f"property {name} is not available")
        value = remote.property(name)
        error = remote.lastError()
        if error.isValid():
            _raise_call_error(error)
        return plain_dbus_value(value)

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
        receiver = PlayerSignalReceiver(
            service,
            on_properties_changed,
            on_seeked,
            self.read_properties,
        )
        properties_connected = False
        seeked_connected = False
        try:
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
        except (TypeError, ValueError) as binding_error:
            if properties_connected:
                self._connection.disconnect(
                    service,
                    MPRIS_OBJECT_PATH,
                    DBUS_PROPERTIES_INTERFACE,
                    "PropertiesChanged",
                    receiver,
                    PROPERTIES_SLOT,
                )
            receiver.deleteLater()
            raise MprisBackendError(
                "installed PySide6 rejected an MPRIS signal slot signature"
            ) from binding_error
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
            receiver.deleteLater()
            raise MprisBackendError(
                "could not connect MPRIS signals: "
                f"{_error_message(error.name(), error.message())}"
            )

        def close() -> None:
            properties_disconnected = self._connection.disconnect(
                service,
                MPRIS_OBJECT_PATH,
                DBUS_PROPERTIES_INTERFACE,
                "PropertiesChanged",
                receiver,
                PROPERTIES_SLOT,
            )
            seeked_disconnected = self._connection.disconnect(
                service,
                MPRIS_OBJECT_PATH,
                "org.mpris.MediaPlayer2.Player",
                "Seeked",
                receiver,
                SEEKED_SLOT,
            )
            receiver.deleteLater()
            if not properties_disconnected or not seeked_disconnected:
                error = self._connection.lastError()
                raise MprisBackendError(
                    "could not disconnect MPRIS signals: "
                    f"{_error_message(error.name(), error.message())}"
                )

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
