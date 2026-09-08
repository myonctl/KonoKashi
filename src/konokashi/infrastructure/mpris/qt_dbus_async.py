"""Qt-native asynchronous MPRIS operations for the desktop event loop."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import suppress
from typing import TypeAlias

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtDBus import (
    QDBusConnection,
    QDBusMessage,
    QDBusPendingCallWatcher,
)

from konokashi.application.ports import (
    MonitorStartCallback,
    PlayerEventHandler,
    PlayerListCallback,
    PositionSampleCallback,
)
from konokashi.domain.models import (
    InspectionFailure,
    PlayerInspection,
    PlayerListResult,
    PlayerSnapshot,
    PlayerWatchStart,
)
from konokashi.domain.synchronization import (
    ObservationReason,
    PlaybackState,
    PositionObservation,
)
from konokashi.infrastructure.clocks import LinuxClock
from konokashi.infrastructure.mpris.backend import (
    MprisBackendError,
    MprisPropertyRead,
    MprisServiceUnavailable,
)
from konokashi.infrastructure.mpris.metadata_mapper import (
    MPRIS_OBJECT_PATH,
    MPRIS_PLAYER_INTERFACE,
    MPRIS_PREFIX,
    MPRIS_ROOT_INTERFACE,
    map_player_snapshot,
    short_service_name,
)
from konokashi.infrastructure.mpris.player_registry import MprisMonitor
from konokashi.infrastructure.mpris.qt_dbus_client import (
    _PROPERTY_NAMES,
    DBUS_INTERFACE,
    DBUS_PATH,
    DBUS_PROPERTIES_INTERFACE,
    DBUS_SERVICE,
    QtDbusBackend,
    _error_message,
    _raise_call_error,
)
from konokashi.infrastructure.mpris.qt_dbus_values import plain_dbus_value

_CallCallback: TypeAlias = Callable[
    [tuple[object, ...] | None, BaseException | None], None
]
_PropertyCallback: TypeAlias = Callable[
    [MprisPropertyRead | None, BaseException | None], None
]


class _TypedPropertySignals(QObject):
    completed = Signal(int, object, object)


class _TypedPropertyJob(QRunnable):
    """Use one worker-owned connection for Qt's typed property proxy."""

    def __init__(
        self,
        job_id: int,
        connection_name: str,
        service: str,
        interface: str,
        name: str,
        timeout_ms: int,
        signals: _TypedPropertySignals,
    ) -> None:
        super().__init__()
        self._job_id = job_id
        self._connection_name = connection_name
        self._service = service
        self._interface = interface
        self._name = name
        self._timeout_ms = timeout_ms
        self._signals = signals

    @Slot()
    def run(self) -> None:
        result: object | None = None
        error: BaseException | None = None
        connection = QDBusConnection.connectToBus(
            QDBusConnection.BusType.SessionBus,
            self._connection_name,
        )
        try:
            backend = QtDbusBackend(connection, timeout_ms=self._timeout_ms)
            result = backend.get_property(
                self._service,
                self._interface,
                self._name,
            )
        except Exception as caught:
            error = caught
        finally:
            QDBusConnection.disconnectFromBus(self._connection_name)
        with suppress(RuntimeError):
            self._signals.completed.emit(self._job_id, result, error)


class _TypedPropertyOperation:
    def __init__(self, reader: QtTypedPropertyReader, job_id: int) -> None:
        self._reader: QtTypedPropertyReader | None = reader
        self._job_id = job_id

    def cancel(self) -> None:
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.cancel(self._job_id)


class QtTypedPropertyReader(QObject):
    """Off-thread typed fallback for nested maps PySide cannot decode dynamically."""

    def __init__(
        self,
        *,
        timeout_ms: int,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._timeout_ms = timeout_ms
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._signals = _TypedPropertySignals(self)
        self._signals.completed.connect(self._completed)
        self._next_job_id = 1
        self._callbacks: dict[int, _CallCallback] = {}
        self._closed = False

    def get_property_async(
        self,
        service: str,
        interface: str,
        name: str,
        callback: _CallCallback,
    ) -> _TypedPropertyOperation:
        if self._closed:
            raise MprisBackendError("desktop typed-property reader is closed")
        job_id = self._next_job_id
        self._next_job_id += 1
        self._callbacks[job_id] = callback
        connection_name = f"konokashi-desktop-properties-{id(self):x}-{job_id}"
        self._pool.start(
            _TypedPropertyJob(
                job_id,
                connection_name,
                service,
                interface,
                name,
                self._timeout_ms,
                self._signals,
            )
        )
        return _TypedPropertyOperation(self, job_id)

    def cancel(self, job_id: int) -> None:
        self._callbacks.pop(job_id, None)

    @Slot(int, object, object)
    def _completed(
        self,
        job_id: int,
        result: object | None,
        error: BaseException | None,
    ) -> None:
        callback = self._callbacks.pop(job_id, None)
        if callback is not None:
            callback(None if error is not None else (result,), error)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._callbacks.clear()
        self._pool.clear()


class _PendingCall(QObject):
    """Own one watcher and suppress completion after timeout or cancellation."""

    def __init__(
        self,
        connection: QDBusConnection,
        message: QDBusMessage,
        timeout_ms: int,
        callback: _CallCallback,
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self._callback: _CallCallback | None = callback
        pending = connection.asyncCall(message, timeout_ms)
        self._watcher = QDBusPendingCallWatcher(pending, self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timed_out)
        self._watcher.finished.connect(self._finished)
        self._timer.start(timeout_ms + 250)
        if self._watcher.isFinished():
            QTimer.singleShot(0, self._finished)

    def cancel(self) -> None:
        """Discard the watcher and guarantee that the client callback is not run."""

        if self._callback is None:
            return
        self._callback = None
        self._timer.stop()
        with suppress(RuntimeError, TypeError):
            self._watcher.finished.disconnect(self._finished)
        self.deleteLater()

    def _complete(
        self,
        result: tuple[object, ...] | None,
        error: BaseException | None,
    ) -> None:
        callback = self._callback
        if callback is None:
            return
        self._callback = None
        self._timer.stop()
        callback(result, error)
        self.deleteLater()

    @Slot()
    def _timed_out(self) -> None:
        self._complete(
            None,
            MprisBackendError("D-Bus call did not finish within its bounded timeout"),
        )

    @Slot()
    def _finished(self) -> None:
        if self._callback is None:
            return
        if self._watcher.isError():
            try:
                _raise_call_error(self._watcher.error())
            except MprisBackendError as error:
                self._complete(None, error)
            return
        try:
            values = tuple(
                plain_dbus_value(value) for value in self._watcher.reply().arguments()
            )
        except MprisBackendError as error:
            self._complete(None, error)
            return
        self._complete(values, None)


class _Operation:
    """Combine child calls under one idempotent cancellation handle."""

    def __init__(self, on_cancel: Callable[[], None] | None = None) -> None:
        self._children: list[object] = []
        self._on_cancel = on_cancel
        self.cancelled = False
        self.finished = False

    def add(self, child: object) -> None:
        if self.cancelled or self.finished:
            cast_cancel = getattr(child, "cancel", None)
            if cast_cancel is not None:
                cast_cancel()
            return
        self._children.append(child)

    def finish(self) -> bool:
        if self.cancelled or self.finished:
            return False
        self.finished = True
        self._children.clear()
        return True

    def cancel(self) -> None:
        if self.cancelled or self.finished:
            return
        self.cancelled = True
        if self._on_cancel is not None:
            self._on_cancel()
            self._on_cancel = None
        children, self._children = self._children, []
        for child in children:
            cancel = getattr(child, "cancel", None)
            if cancel is not None:
                cancel()


class QtAsyncDbusBackend(QObject):
    """Asynchronous calls that keep all QtDBus objects on their owner thread."""

    def __init__(
        self,
        connection: QDBusConnection,
        *,
        timeout_ms: int = 5_000,
        typed_property_reader: QtTypedPropertyReader | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._connection = connection
        self._timeout_ms = timeout_ms
        self._typed_property_reader = typed_property_reader
        self._closed = False
        self._pending: set[_PendingCall] = set()

    def _ensure_connected(self) -> None:
        if self._closed:
            raise MprisBackendError("desktop D-Bus runtime is closed")
        if self._connection.isConnected():
            return
        error = self._connection.lastError()
        raise MprisBackendError(_error_message(error.name(), error.message()))

    def call_async(
        self,
        service: str,
        path: str,
        interface: str,
        method: str,
        arguments: Sequence[object],
        callback: _CallCallback,
    ) -> _PendingCall:
        """Start one bounded call and decode its reply on watcher completion."""

        self._ensure_connected()
        message = QDBusMessage.createMethodCall(service, path, interface, method)
        message.setArguments(list(arguments))

        request: _PendingCall

        def completed(
            result: tuple[object, ...] | None,
            error: BaseException | None,
        ) -> None:
            self._pending.discard(request)
            callback(result, error)

        request = _PendingCall(
            self._connection,
            message,
            self._timeout_ms,
            completed,
            self,
        )
        self._pending.add(request)
        return request

    def list_service_names_async(self, callback: _CallCallback) -> _PendingCall:
        """Issue the daemon ListNames call without a nested event loop."""

        return self.call_async(
            DBUS_SERVICE,
            DBUS_PATH,
            DBUS_INTERFACE,
            "ListNames",
            (),
            callback,
        )

    def get_property_async(
        self,
        service: str,
        interface: str,
        name: str,
        callback: _CallCallback,
    ) -> _PendingCall:
        """Issue Properties.Get; its variant payload is decoded on completion."""

        return self.call_async(
            service,
            MPRIS_OBJECT_PATH,
            DBUS_PROPERTIES_INTERFACE,
            "Get",
            (interface, name),
            callback,
        )

    def read_properties_async(
        self,
        service: str,
        interface: str,
        callback: _PropertyCallback,
        *,
        include_position: bool = False,
    ) -> _Operation:
        """Read declared properties independently without synchronous proxies."""

        operation = _Operation()
        try:
            declared = _PROPERTY_NAMES[interface]
        except KeyError:
            operation.finish()
            QTimer.singleShot(
                0,
                lambda: callback(
                    None, MprisBackendError(f"unsupported MPRIS interface {interface}")
                ),
            )
            return operation
        names = tuple(
            name for name in declared if include_position or name != "Position"
        )
        values: dict[str, object] = {}
        diagnostics: list[str] = []
        remaining = len(names)

        def one_completed(
            name: str,
            result: tuple[object, ...] | None,
            error: BaseException | None,
        ) -> None:
            nonlocal remaining
            if operation.cancelled or operation.finished:
                return
            if isinstance(error, MprisServiceUnavailable):
                if operation.finish():
                    callback(None, error)
                return
            if error is not None:
                diagnostics.append(f"{name}: unavailable ({error})")
            elif result is None or len(result) != 1:
                diagnostics.append(f"{name}: unavailable (unexpected Get reply)")
            else:
                values[name] = result[0]
            remaining -= 1
            if remaining == 0 and operation.finish():
                callback(MprisPropertyRead(values, tuple(diagnostics)), None)

        def property_callback(name: str) -> _CallCallback:
            def completed(
                result: tuple[object, ...] | None,
                error: BaseException | None,
            ) -> None:
                one_completed(name, result, error)

            return completed

        for name in names:
            child: _PendingCall | _TypedPropertyOperation
            if name == "Metadata" and self._typed_property_reader is not None:
                child = self._typed_property_reader.get_property_async(
                    service,
                    interface,
                    name,
                    property_callback(name),
                )
            else:
                child = self.get_property_async(
                    service,
                    interface,
                    name,
                    property_callback(name),
                )
            operation.add(child)
        return operation

    def close(self) -> None:
        """Cancel all pending watchers without waiting for remote services."""

        if self._closed:
            return
        self._closed = True
        pending, self._pending = tuple(self._pending), set()
        for request in pending:
            request.cancel()


class QtAsyncMprisClient:
    """Compose asynchronous daemon and property calls into player snapshots."""

    def __init__(self, backend: QtAsyncDbusBackend) -> None:
        self._backend = backend

    def list_players_async(self, callback: PlayerListCallback) -> _Operation:
        operation = _Operation()

        def names_completed(
            result: tuple[object, ...] | None,
            error: BaseException | None,
        ) -> None:
            if operation.cancelled or operation.finished:
                return
            if error is not None:
                if operation.finish():
                    callback(PlayerListResult(error=str(error)), None)
                return
            if (
                result is None
                or len(result) != 1
                or not isinstance(result[0], Sequence)
                or isinstance(result[0], str)
                or not all(isinstance(name, str) for name in result[0])
            ):
                if operation.finish():
                    callback(
                        PlayerListResult(
                            error="ListNames returned a malformed payload"
                        ),
                        None,
                    )
                return
            names = tuple(
                sorted({name for name in result[0] if name.startswith(MPRIS_PREFIX)})
            )
            if not names:
                if operation.finish():
                    callback(PlayerListResult(), None)
                return
            inspections: dict[str, PlayerInspection] = {}

            def inspected(name: str, inspection: PlayerInspection) -> None:
                if operation.cancelled or operation.finished:
                    return
                inspections[name] = inspection
                if len(inspections) == len(names) and operation.finish():
                    callback(
                        PlayerListResult(
                            players=tuple(inspections[item] for item in names)
                        ),
                        None,
                    )

            def inspection_callback(
                name: str,
            ) -> Callable[[PlayerInspection], None]:
                def completed(inspection: PlayerInspection) -> None:
                    inspected(name, inspection)

                return completed

            for name in names:
                operation.add(
                    self._inspect_player_async(
                        name,
                        inspection_callback(name),
                    )
                )

        operation.add(self._backend.list_service_names_async(names_completed))
        return operation

    def _inspect_player_async(
        self,
        bus_name: str,
        callback: Callable[[PlayerInspection], None],
    ) -> _Operation:
        operation = _Operation()
        reads: dict[str, MprisPropertyRead] = {}

        def read_completed(
            interface: str,
            result: MprisPropertyRead | None,
            error: BaseException | None,
        ) -> None:
            if operation.cancelled or operation.finished:
                return
            if isinstance(error, MprisServiceUnavailable):
                if operation.finish():
                    callback(
                        PlayerInspection(
                            service_name=short_service_name(bus_name),
                            bus_name=bus_name,
                            failure=InspectionFailure.UNAVAILABLE,
                            message=f"player disappeared during inspection ({error})",
                        )
                    )
                return
            if error is not None or result is None:
                result = MprisPropertyRead(
                    diagnostics=(
                        f"{'root' if interface == MPRIS_ROOT_INTERFACE else 'player'} "
                        f"properties: unavailable ({error or 'unknown error'})",
                    )
                )
            reads[interface] = result
            if len(reads) != 2 or not operation.finish():
                return
            root = reads[MPRIS_ROOT_INTERFACE]
            player = reads[MPRIS_PLAYER_INTERFACE]
            diagnostics = (*root.diagnostics, *player.diagnostics)
            if not root.values and not player.values:
                callback(
                    PlayerInspection(
                        service_name=short_service_name(bus_name),
                        bus_name=bus_name,
                        failure=InspectionFailure.BUS_ERROR,
                        message="; ".join(diagnostics)
                        or "no readable MPRIS properties",
                    )
                )
                return
            callback(
                PlayerInspection(
                    service_name=short_service_name(bus_name),
                    bus_name=bus_name,
                    snapshot=map_player_snapshot(
                        bus_name,
                        root.values,
                        player.values,
                        diagnostics,
                    ),
                )
            )

        operation.add(
            self._backend.read_properties_async(
                bus_name,
                MPRIS_ROOT_INTERFACE,
                lambda result, error: read_completed(
                    MPRIS_ROOT_INTERFACE, result, error
                ),
            )
        )
        operation.add(
            self._backend.read_properties_async(
                bus_name,
                MPRIS_PLAYER_INTERFACE,
                lambda result, error: read_completed(
                    MPRIS_PLAYER_INTERFACE, result, error
                ),
            )
        )
        return operation


class QtAsyncPositionSampler:
    """Bracket asynchronous Position calls with the shared monotonic clock."""

    def __init__(self, backend: QtAsyncDbusBackend, clock: LinuxClock) -> None:
        self._backend = backend
        self._clock = clock
        self._inflight_services: set[str] = set()

    def sample_async(
        self,
        snapshot: PlayerSnapshot,
        session_id: str,
        *,
        reason: ObservationReason,
        callback: PositionSampleCallback,
    ) -> _Operation:
        service = snapshot.bus_name
        operation = _Operation(lambda: self._inflight_services.discard(service))
        if service in self._inflight_services:
            operation.finish()
            QTimer.singleShot(
                0,
                lambda: callback(
                    None,
                    MprisBackendError(
                        "a Position request is already in flight for this source"
                    ),
                ),
            )
            return operation
        self._inflight_services.add(service)
        started_ns = self._clock.monotonic_ns()

        def completed(
            result: tuple[object, ...] | None,
            error: BaseException | None,
        ) -> None:
            self._inflight_services.discard(service)
            if operation.cancelled or operation.finished:
                return
            received_ns = self._clock.monotonic_ns()
            if error is not None:
                if operation.finish():
                    callback(None, error)
                return
            if result is None or len(result) != 1:
                if operation.finish():
                    callback(None, MprisBackendError("Position returned no value"))
                return
            value = result[0]
            if not isinstance(value, int) or isinstance(value, bool):
                if operation.finish():
                    callback(
                        None,
                        MprisBackendError(
                            f"Position returned {type(value).__name__}, expected "
                            "integer microseconds"
                        ),
                    )
                return
            if value < 0:
                if operation.finish():
                    callback(
                        None,
                        MprisBackendError(f"Position returned negative value {value}"),
                    )
                return
            state = PlaybackState.from_mpris(snapshot.playback_status)
            if snapshot.rate is None and state is PlaybackState.PLAYING:
                if operation.finish():
                    callback(
                        None,
                        MprisBackendError(
                            "Rate is unavailable; refusing to interpolate a "
                            "playing clock"
                        ),
                    )
                return
            try:
                observation = PositionObservation(
                    session_id=session_id,
                    position_us=value,
                    state=state,
                    rate=snapshot.rate if snapshot.rate is not None else 1.0,
                    request_started_ns=started_ns,
                    response_received_ns=received_ns,
                    reason=reason,
                )
            except ValueError as invalid:
                if operation.finish():
                    callback(
                        None,
                        MprisBackendError(
                            f"invalid MPRIS timing observation: {invalid}"
                        ),
                    )
                return
            if operation.finish():
                callback(observation, None)

        child = self._backend.get_property_async(
            service,
            MPRIS_PLAYER_INTERFACE,
            "Position",
            completed,
        )
        operation.add(child)
        return operation


class QtAsyncMprisMonitor:
    """Subscribe synchronously, but enumerate initial players asynchronously."""

    def __init__(
        self,
        monitor: MprisMonitor,
        backend: QtAsyncDbusBackend,
    ) -> None:
        self._monitor = monitor
        self._backend = backend

    def start_async(
        self,
        handler: PlayerEventHandler,
        callback: MonitorStartCallback,
    ) -> _Operation:
        operation = _Operation()
        started = self._monitor.start_listening(handler)
        if started.error is not None:
            operation.finish()
            callback(started, None)
            return operation

        def names_completed(
            result: tuple[object, ...] | None,
            error: BaseException | None,
        ) -> None:
            if operation.cancelled or operation.finished:
                return
            if error is not None:
                with suppress(MprisBackendError):
                    self._monitor.close()
                if operation.finish():
                    callback(PlayerWatchStart(error=str(error)), None)
                return
            if (
                result is None
                or len(result) != 1
                or not isinstance(result[0], Sequence)
                or isinstance(result[0], str)
                or not all(isinstance(name, str) for name in result[0])
            ):
                with suppress(MprisBackendError):
                    self._monitor.close()
                if operation.finish():
                    callback(
                        PlayerWatchStart(
                            error="ListNames returned a malformed payload"
                        ),
                        None,
                    )
                return
            initial = self._monitor.attach_initial_services(result[0])
            if operation.finish():
                callback(initial, None)

        operation.add(self._backend.list_service_names_async(names_completed))
        return operation

    def close(self) -> None:
        self._monitor.close()


class QtDesktopMprisRuntime:
    """Own asynchronous desktop ports on the same Qt thread as their connection."""

    def __init__(
        self,
        connection: QDBusConnection,
        monitor_backend: QtDbusBackend,
        clock: LinuxClock,
        *,
        timeout_ms: int = 5_000,
        parent: QObject | None = None,
    ) -> None:
        self._typed_property_reader = QtTypedPropertyReader(
            timeout_ms=timeout_ms,
            parent=parent,
        )
        self._backend = QtAsyncDbusBackend(
            connection,
            timeout_ms=timeout_ms,
            typed_property_reader=self._typed_property_reader,
            parent=parent,
        )
        monitor_backend.set_async_property_reader(self._backend.read_properties_async)
        self.client = QtAsyncMprisClient(self._backend)
        self.monitor = QtAsyncMprisMonitor(MprisMonitor(monitor_backend), self._backend)
        self.timing = QtAsyncPositionSampler(self._backend, clock)
        self.clock = clock
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._backend.close()
        self._typed_property_reader.close()
        self.monitor.close()
