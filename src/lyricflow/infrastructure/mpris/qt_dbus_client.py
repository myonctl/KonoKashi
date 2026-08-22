"""Production PySide6 QtDBus backend and CLI event-loop runtime."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import ClassVar, NoReturn, TypeAlias, cast

from PySide6.QtCore import (
    SLOT,
    Property,
    QCoreApplication,
    QEventLoop,
    QObject,
    Qt,
    QTimer,
    Slot,
)
from PySide6.QtDBus import (
    QDBusAbstractInterface,
    QDBusConnection,
    QDBusError,
    QDBusMessage,
    QDBusPendingCallWatcher,
)

from lyricflow.infrastructure.clocks import LinuxClock
from lyricflow.infrastructure.mpris.backend import (
    MprisBackendError,
    MprisPropertyRead,
    MprisServiceUnavailable,
    PropertiesChangedHandler,
    SeekedHandler,
    ServiceHandler,
)
from lyricflow.infrastructure.mpris.metadata_mapper import MPRIS_OBJECT_PATH
from lyricflow.infrastructure.mpris.player_registry import (
    MprisClient,
    MprisMonitor,
    MprisPositionSampler,
)
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
NAME_OWNER_CHANGED_SLOT = cast(
    bytes,
    SLOT("nameOwnerChanged(QString,QString,QString)"),
)

_CloseCallback: TypeAlias = Callable[[], None]


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


class _NameOwnerChangedReceiver(QObject):
    """Translate daemon NameOwnerChanged signals into service lifecycle calls."""

    def __init__(
        self,
        on_registered: ServiceHandler,
        on_unregistered: ServiceHandler,
    ) -> None:
        super().__init__()
        self._on_registered = on_registered
        self._on_unregistered = on_unregistered

    @Slot(str, str, str)
    def nameOwnerChanged(
        self,
        name: str,
        old_owner: str,
        new_owner: str,
    ) -> None:
        """Report acquisition, loss, and direct owner replacement."""

        if old_owner == new_owner:
            return
        if old_owner:
            self._on_unregistered(name)
        if new_owner:
            self._on_registered(name)


class _MprisInterface(QDBusAbstractInterface):
    """Statically typed proxy base matching Qt's generated D-Bus proxies."""

    interface_name: ClassVar[str]

    def __init__(
        self,
        service: str,
        connection: QDBusConnection,
    ) -> None:
        # PySide6's runtime accepts the textual interface and a null parent;
        # its 6.11.1 stubs incorrectly require bytes and a non-null QObject.
        super().__init__(
            service,
            MPRIS_OBJECT_PATH,
            cast(bytes, self.interface_name),
            connection,
            cast(QObject, None),
        )


def _remote_value(interface: QDBusAbstractInterface, name: str) -> object:
    """Delegate a declared Qt property to QDBusAbstractInterface."""

    return interface.property(name)


class _MprisRootInterface(_MprisInterface):
    """Typed proxy for the standard org.mpris.MediaPlayer2 properties."""

    interface_name = "org.mpris.MediaPlayer2"

    CanQuit = Property(bool, lambda self: _remote_value(self, "CanQuit"))
    CanRaise = Property(bool, lambda self: _remote_value(self, "CanRaise"))
    CanSetFullscreen = Property(
        bool, lambda self: _remote_value(self, "CanSetFullscreen")
    )
    DesktopEntry = Property(str, lambda self: _remote_value(self, "DesktopEntry"))
    Fullscreen = Property(bool, lambda self: _remote_value(self, "Fullscreen"))
    HasTrackList = Property(bool, lambda self: _remote_value(self, "HasTrackList"))
    Identity = Property(str, lambda self: _remote_value(self, "Identity"))
    SupportedMimeTypes = Property(
        cast(type[object], "QStringList"),
        lambda self: _remote_value(self, "SupportedMimeTypes"),
    )
    SupportedUriSchemes = Property(
        cast(type[object], "QStringList"),
        lambda self: _remote_value(self, "SupportedUriSchemes"),
    )


class _MprisPlayerInterface(_MprisInterface):
    """Typed proxy for the standard org.mpris.MediaPlayer2.Player properties."""

    interface_name = "org.mpris.MediaPlayer2.Player"

    CanControl = Property(bool, lambda self: _remote_value(self, "CanControl"))
    CanGoNext = Property(bool, lambda self: _remote_value(self, "CanGoNext"))
    CanGoPrevious = Property(bool, lambda self: _remote_value(self, "CanGoPrevious"))
    CanPause = Property(bool, lambda self: _remote_value(self, "CanPause"))
    CanPlay = Property(bool, lambda self: _remote_value(self, "CanPlay"))
    CanSeek = Property(bool, lambda self: _remote_value(self, "CanSeek"))
    LoopStatus = Property(str, lambda self: _remote_value(self, "LoopStatus"))
    MaximumRate = Property(float, lambda self: _remote_value(self, "MaximumRate"))
    Metadata = Property(
        cast(type[object], "QVariantMap"),
        lambda self: _remote_value(self, "Metadata"),
    )
    MinimumRate = Property(float, lambda self: _remote_value(self, "MinimumRate"))
    PlaybackStatus = Property(str, lambda self: _remote_value(self, "PlaybackStatus"))
    Position = Property(
        cast(type[object], "qlonglong"),
        lambda self: _remote_value(self, "Position"),
    )
    Rate = Property(float, lambda self: _remote_value(self, "Rate"))
    Shuffle = Property(bool, lambda self: _remote_value(self, "Shuffle"))
    Volume = Property(float, lambda self: _remote_value(self, "Volume"))


_PROPERTY_NAMES: dict[str, tuple[str, ...]] = {
    _MprisRootInterface.interface_name: (
        "CanQuit",
        "CanRaise",
        "CanSetFullscreen",
        "DesktopEntry",
        "Fullscreen",
        "HasTrackList",
        "Identity",
        "SupportedMimeTypes",
        "SupportedUriSchemes",
    ),
    _MprisPlayerInterface.interface_name: (
        "CanControl",
        "CanGoNext",
        "CanGoPrevious",
        "CanPause",
        "CanPlay",
        "CanSeek",
        "LoopStatus",
        "MaximumRate",
        "Metadata",
        "MinimumRate",
        "PlaybackStatus",
        "Position",
        "Rate",
        "Shuffle",
        "Volume",
    ),
}


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

    def _remote_interface(self, service: str, interface: str) -> QDBusAbstractInterface:
        """Create a bounded, statically typed proxy for one MPRIS interface."""

        self._ensure_connected()
        if interface == _MprisRootInterface.interface_name:
            remote: QDBusAbstractInterface = _MprisRootInterface(
                service, self._connection
            )
        elif interface == _MprisPlayerInterface.interface_name:
            remote = _MprisPlayerInterface(service, self._connection)
        else:
            raise MprisBackendError(f"unsupported MPRIS interface {interface}")
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
    ) -> MprisPropertyRead:
        """Read every standard property through its declared Qt D-Bus type."""

        properties: dict[str, object] = {}
        diagnostics: list[str] = []
        try:
            names = _PROPERTY_NAMES[interface]
        except KeyError as error:
            raise MprisBackendError(
                f"unsupported MPRIS interface {interface}"
            ) from error
        remote = self._remote_interface(service, interface)
        for name in names:
            value = remote.property(name)
            # QDBusAbstractInterface.lastError() is sticky after a failed
            # property call. A concrete value proves this request succeeded;
            # only a missing value may consume the current error state.
            if value is None:
                call_error = remote.lastError()
                if not call_error.isValid():
                    diagnostics.append(f"{name}: unavailable (no value returned)")
                    continue
                try:
                    _raise_call_error(call_error)
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
        """Read standard properties independently through statically typed proxies."""

        return self._read_properties_individually(service, interface)

    def get_property(self, service: str, interface: str, name: str) -> object:
        """Read one property, notably Position, without rapid polling."""

        if name not in _PROPERTY_NAMES.get(interface, ()):
            raise MprisBackendError(f"property {name} is not available")
        remote = self._remote_interface(service, interface)
        value = remote.property(name)
        if value is None:
            call_error = remote.lastError()
            if call_error.isValid():
                _raise_call_error(call_error)
            raise MprisBackendError(f"property {name} returned no value")
        return plain_dbus_value(value)

    def subscribe_service_changes(
        self,
        on_registered: ServiceHandler,
        on_unregistered: ServiceHandler,
    ) -> _CallableSubscription:
        """Subscribe directly to the daemon's authoritative owner changes."""

        self._ensure_connected()
        receiver = _NameOwnerChangedReceiver(on_registered, on_unregistered)
        try:
            connected = self._connection.connect(
                DBUS_SERVICE,
                DBUS_PATH,
                DBUS_INTERFACE,
                "NameOwnerChanged",
                receiver,
                NAME_OWNER_CHANGED_SLOT,
            )
        except (TypeError, ValueError) as binding_error:
            receiver.deleteLater()
            raise MprisBackendError(
                "installed PySide6 rejected the D-Bus lifecycle slot signature"
            ) from binding_error
        if not connected:
            error = self._connection.lastError()
            receiver.deleteLater()
            raise MprisBackendError(
                "could not connect D-Bus lifecycle signal: "
                f"{_error_message(error.name(), error.message())}"
            )

        def close() -> None:
            disconnected = self._connection.disconnect(
                DBUS_SERVICE,
                DBUS_PATH,
                DBUS_INTERFACE,
                "NameOwnerChanged",
                receiver,
                NAME_OWNER_CHANGED_SLOT,
            )
            receiver.deleteLater()
            if not disconnected:
                error = self._connection.lastError()
                raise MprisBackendError(
                    "could not disconnect D-Bus lifecycle signal: "
                    f"{_error_message(error.name(), error.message())}"
                )

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
        self._wait_loop: QEventLoop | None = None
        self.client = MprisClient(backend)
        self.monitor = MprisMonitor(backend)
        self.clock = LinuxClock()
        self.timing = MprisPositionSampler(backend, self.clock.monotonic_ns)

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

    def wait(self, timeout_ms: int) -> None:
        """Process D-Bus signals during a bounded diagnostic sampling interval."""

        if timeout_ms < 0:
            raise ValueError("wait timeout cannot be negative")
        loop = QEventLoop(self._application)
        timer = QTimer(loop)
        timer.setSingleShot(True)
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        timer.timeout.connect(loop.quit)
        timer.start(timeout_ms)
        self._wait_loop = loop
        try:
            loop.exec()
        finally:
            self._wait_loop = None

    def wake(self) -> None:
        """Wake a one-shot wait so seek/status/track state is rendered now."""

        if self._wait_loop is not None:
            self._wait_loop.quit()


def create_qt_mpris_runtime() -> QtMprisRuntime:
    """Build the production MPRIS runtime using the session D-Bus."""

    application = QCoreApplication.instance()
    if application is None:
        application = QCoreApplication(["lyricflow"])
    if not isinstance(application, QCoreApplication):
        raise MprisBackendError("existing Qt application is not a QCoreApplication")
    return QtMprisRuntime(application, QtDbusBackend())
