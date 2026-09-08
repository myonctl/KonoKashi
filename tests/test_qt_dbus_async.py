"""Deterministic delayed-reply tests for the desktop QtDBus path."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic_ns
from typing import Any, cast

import pytest
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtDBus import QDBusConnection, QDBusMessage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from konokashi.domain.models import PlayerCapabilities, PlayerSnapshot, RawTrackMetadata
from konokashi.domain.synchronization import ObservationReason
from konokashi.infrastructure.clocks import LinuxClock
from konokashi.infrastructure.mpris.qt_dbus_async import (
    QtAsyncDbusBackend,
    QtAsyncMprisClient,
    QtAsyncPositionSampler,
)
from konokashi.infrastructure.mpris.qt_dbus_client import (
    DBUS_INTERFACE,
    DBUS_SERVICE,
)

_SERVICE = "org.mpris.MediaPlayer2.delayed"


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    application = QApplication.instance() or QApplication(["async-dbus-test"])
    assert isinstance(application, QApplication)
    return application


@dataclass(slots=True)
class _PendingResult:
    delay_ms: int
    arguments: tuple[object, ...]
    error: _FakeError | None = None


class _FakeError:
    def __init__(self, name: str = "", message: str = "") -> None:
        self._name = name
        self._message = message

    def name(self) -> str:
        return self._name

    def message(self) -> str:
        return self._message


class _FakeReply:
    def __init__(self, arguments: tuple[object, ...]) -> None:
        self._arguments = arguments

    def arguments(self) -> tuple[object, ...]:
        return self._arguments


class _DelayedWatcher(QObject):
    finished = Signal()

    def __init__(self, pending: _PendingResult, parent: QObject) -> None:
        super().__init__(parent)
        self._pending = pending
        self._finished = False
        QTimer.singleShot(pending.delay_ms, self._complete)

    def _complete(self) -> None:
        self._finished = True
        self.finished.emit()

    def isFinished(self) -> bool:
        return self._finished

    def isError(self) -> bool:
        return self._pending.error is not None

    def error(self) -> _FakeError:
        return self._pending.error or _FakeError()

    def reply(self) -> _FakeReply:
        return _FakeReply(self._pending.arguments)


class _DelayedConnection:
    def __init__(self, delay_ms: int = 80) -> None:
        self.delay_ms = delay_ms
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.position_us = 1_500_000

    def isConnected(self) -> bool:
        return True

    def lastError(self) -> _FakeError:
        return _FakeError()

    def asyncCall(self, message: QDBusMessage, _timeout_ms: int) -> _PendingResult:
        method = message.member()
        arguments = tuple(message.arguments())
        self.calls.append((method, arguments))
        if message.service() == DBUS_SERVICE and message.interface() == DBUS_INTERFACE:
            return _PendingResult(self.delay_ms, ((_SERVICE,),))
        assert method == "Get"
        name = cast(str, arguments[1])
        values: dict[str, object] = {
            "Identity": "Delayed Player",
            "DesktopEntry": "delayed",
            "PlaybackStatus": "Playing",
            "Metadata": {
                "mpris:length": 180_000_000,
                "mpris:trackid": "/track/delayed",
                "xesam:title": "Delayed Track",
                "xesam:artist": ("Example Artist",),
            },
            "Position": self.position_us,
            "Rate": 1.0,
        }
        return _PendingResult(self.delay_ms, (values.get(name),))


class _PositionReplyConnection(_DelayedConnection):
    def __init__(
        self,
        reply: object,
        *,
        delay_ms: int = 20,
        error: _FakeError | None = None,
    ) -> None:
        super().__init__(delay_ms)
        self.reply = reply
        self.error = error

    def asyncCall(self, message: QDBusMessage, _timeout_ms: int) -> _PendingResult:
        self.calls.append((message.member(), tuple(message.arguments())))
        return _PendingResult(self.delay_ms, (self.reply,), self.error)


def _backend(
    monkeypatch: pytest.MonkeyPatch,
    connection: _DelayedConnection,
    qt_app: QApplication,
    *,
    timeout_ms: int = 500,
) -> QtAsyncDbusBackend:
    monkeypatch.setattr(
        "konokashi.infrastructure.mpris.qt_dbus_async.QDBusPendingCallWatcher",
        _DelayedWatcher,
    )
    return QtAsyncDbusBackend(
        cast(QDBusConnection, cast(Any, connection)),
        timeout_ms=timeout_ms,
        parent=qt_app,
    )


def test_delayed_list_names_and_property_reads_keep_heartbeat_alive(
    monkeypatch: pytest.MonkeyPatch,
    qt_app: QApplication,
) -> None:
    connection = _DelayedConnection()
    client = QtAsyncMprisClient(_backend(monkeypatch, connection, qt_app))
    heartbeats: list[int] = []
    results = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: heartbeats.append(monotonic_ns()))
    timer.start()

    client.list_players_async(lambda result, error: results.append((result, error)))
    QTest.qWait(220)
    timer.stop()

    assert len(heartbeats) >= 10
    assert len(results) == 1
    result, error = results[0]
    assert error is None
    assert result is not None
    assert result.players[0].snapshot is not None
    assert result.players[0].snapshot.metadata.title == "Delayed Track"
    assert all(method in {"ListNames", "Get"} for method, _ in connection.calls)


def test_delayed_position_is_bracketed_without_blocking_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    qt_app: QApplication,
) -> None:
    connection = _DelayedConnection(100)
    backend = _backend(monkeypatch, connection, qt_app)
    sampler = QtAsyncPositionSampler(backend, LinuxClock())
    snapshot = PlayerSnapshot(
        service_name="delayed",
        bus_name=_SERVICE,
        identity="Delayed Player",
        desktop_entry="delayed",
        playback_status="Playing",
        rate=1.0,
        metadata=RawTrackMetadata(title="Delayed Track"),
        position_us=None,
        capabilities=PlayerCapabilities(),
    )
    heartbeats: list[int] = []
    results = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: heartbeats.append(monotonic_ns()))
    timer.start()

    sampler.sample_async(
        snapshot,
        "session",
        reason=ObservationReason.INITIAL,
        callback=lambda result, error: results.append((result, error)),
    )
    QTest.qWait(160)
    timer.stop()

    assert len(heartbeats) >= 8
    observation, error = results[0]
    assert error is None
    assert observation is not None
    assert observation.position_us == 1_500_000
    assert observation.request_started_ns < observation.response_received_ns


def test_close_suppresses_a_late_pending_reply(
    monkeypatch: pytest.MonkeyPatch,
    qt_app: QApplication,
) -> None:
    connection = _DelayedConnection(100)
    backend = _backend(monkeypatch, connection, qt_app)
    callbacks = []

    backend.list_service_names_async(
        lambda result, error: callbacks.append((result, error))
    )
    backend.close()
    QTest.qWait(150)

    assert callbacks == []


def test_timeout_completes_once_and_late_reply_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
    qt_app: QApplication,
) -> None:
    connection = _DelayedConnection(500)
    backend = _backend(
        monkeypatch,
        connection,
        qt_app,
        timeout_ms=10,
    )
    callbacks = []

    backend.list_service_names_async(
        lambda result, error: callbacks.append((result, error))
    )
    QTest.qWait(320)

    assert len(callbacks) == 1
    assert callbacks[0][0] is None
    assert "bounded timeout" in str(callbacks[0][1])
    QTest.qWait(250)
    assert len(callbacks) == 1


@pytest.mark.parametrize("reply", ("late", -1, None))
def test_malformed_position_reply_is_a_controlled_failure(
    monkeypatch: pytest.MonkeyPatch,
    qt_app: QApplication,
    reply: object,
) -> None:
    connection = _PositionReplyConnection(reply)
    sampler = QtAsyncPositionSampler(
        _backend(monkeypatch, connection, qt_app), LinuxClock()
    )
    snapshot = PlayerSnapshot(
        service_name="delayed",
        bus_name=_SERVICE,
        identity="Delayed Player",
        desktop_entry="delayed",
        playback_status="Playing",
        metadata=RawTrackMetadata(title="Delayed Track"),
        position_us=None,
        capabilities=PlayerCapabilities(),
        rate=1.0,
    )
    results = []

    sampler.sample_async(
        snapshot,
        "session",
        reason=ObservationReason.PERIODIC,
        callback=lambda result, error: results.append((result, error)),
    )
    QTest.qWait(60)

    assert results[0][0] is None
    assert "Position" in str(results[0][1])


def test_player_disappearance_maps_to_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    qt_app: QApplication,
) -> None:
    connection = _PositionReplyConnection(
        0,
        error=_FakeError(
            "org.freedesktop.DBus.Error.NameHasNoOwner",
            "player disappeared",
        ),
    )
    backend = _backend(monkeypatch, connection, qt_app)
    results = []

    backend.get_property_async(
        _SERVICE,
        "org.mpris.MediaPlayer2.Player",
        "Position",
        lambda result, error: results.append((result, error)),
    )
    QTest.qWait(60)

    assert results[0][0] is None
    assert "player disappeared" in str(results[0][1])


def test_second_position_request_is_rejected_while_first_is_in_flight(
    monkeypatch: pytest.MonkeyPatch,
    qt_app: QApplication,
) -> None:
    connection = _DelayedConnection(100)
    sampler = QtAsyncPositionSampler(
        _backend(monkeypatch, connection, qt_app), LinuxClock()
    )
    snapshot = PlayerSnapshot(
        service_name="delayed",
        bus_name=_SERVICE,
        identity="Delayed Player",
        desktop_entry="delayed",
        playback_status="Playing",
        metadata=RawTrackMetadata(title="Delayed Track"),
        position_us=None,
        capabilities=PlayerCapabilities(),
        rate=1.0,
    )
    first = []
    second = []

    sampler.sample_async(
        snapshot,
        "session",
        reason=ObservationReason.PERIODIC,
        callback=lambda result, error: first.append((result, error)),
    )
    sampler.sample_async(
        snapshot,
        "session",
        reason=ObservationReason.PERIODIC,
        callback=lambda result, error: second.append((result, error)),
    )
    QTest.qWait(140)

    assert len(first) == 1
    assert first[0][1] is None
    assert second[0][0] is None
    assert "already in flight" in str(second[0][1])
