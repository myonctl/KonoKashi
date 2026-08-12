"""Production QtDBus subscription construction tests without a live bus."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtDBus import QDBusConnection, QDBusObjectPath, QDBusVariant

from lyricflow.infrastructure.mpris.backend import (
    MprisBackendError,
    MprisServiceUnavailable,
    Subscription,
)
from lyricflow.infrastructure.mpris.qt_dbus_client import (
    DBUS_PROPERTIES_INTERFACE,
    QtDbusBackend,
    _MprisPlayerInterface,
)
from lyricflow.infrastructure.mpris.qt_dbus_values import (
    PROPERTIES_SLOT,
    SEEKED_SLOT,
)


class _FakeError:
    def __init__(
        self,
        *,
        valid: bool = False,
        name: str = "org.example.SubscriptionError",
        message: str = "subscription rejected",
    ) -> None:
        self._valid = valid
        self._name = name
        self._message = message

    def name(self) -> str:
        return self._name

    def message(self) -> str:
        return self._message

    def isValid(self) -> bool:
        return self._valid


class _FakeConnectionInterface:
    pass


class _FakeConnection:
    def __init__(
        self,
        *,
        connect_results: tuple[bool, ...] = (True, True),
        disconnect_results: tuple[bool, ...] = (True, True),
        connect_error: Exception | None = None,
    ) -> None:
        self.connect_results = list(connect_results)
        self.disconnect_results = list(disconnect_results)
        self.connect_error = connect_error
        self.connect_calls: list[tuple[object, ...]] = []
        self.disconnect_calls: list[tuple[object, ...]] = []

    def interface(self) -> _FakeConnectionInterface:
        return _FakeConnectionInterface()

    def isConnected(self) -> bool:
        return True

    def lastError(self) -> _FakeError:
        return _FakeError()

    def connect(self, *arguments: object) -> bool:
        self.connect_calls.append(arguments)
        if self.connect_error is not None:
            raise self.connect_error
        return self.connect_results.pop(0)

    def disconnect(self, *arguments: object) -> bool:
        self.disconnect_calls.append(arguments)
        return self.disconnect_results.pop(0)


class _FakeRemoteInterface:
    def __init__(
        self,
        failures: dict[str, _FakeError] | None = None,
        *,
        sticky_error: bool = False,
    ) -> None:
        self.timeout_ms: int | None = None
        self.failures = failures or {}
        self.sticky_error = sticky_error
        self.retained_error = _FakeError()
        self.last_property: str | None = None
        self.values = {
            "CanControl": True,
            "CanGoNext": True,
            "CanGoPrevious": True,
            "CanPause": True,
            "CanPlay": True,
            "CanSeek": True,
            "LoopStatus": "None",
            "MaximumRate": 2.0,
            "PlaybackStatus": "Playing",
            "Metadata": {
                "mpris:artUrl": QDBusVariant("file:///tmp/cover.jpg"),
                "mpris:length": QDBusVariant(9876543),
                "mpris:trackid": QDBusVariant(QDBusObjectPath("/track/42")),
                "xesam:title": QDBusVariant("Example"),
                "xesam:artist": QDBusVariant(["Artist"]),
                "xesam:album": QDBusVariant("Album"),
                "xesam:url": QDBusVariant("file:///music/example.flac"),
            },
            "MinimumRate": 1.0,
            "Position": 123456,
            "Rate": 1.0,
            "Shuffle": False,
            "Volume": 0.75,
        }

    def setTimeout(self, timeout_ms: int) -> None:
        self.timeout_ms = timeout_ms

    def isValid(self) -> bool:
        return True

    def lastError(self) -> _FakeError:
        if self.last_property is None:
            return _FakeError()
        current = self.failures.get(self.last_property, _FakeError())
        if current.isValid():
            self.retained_error = current
        if self.sticky_error:
            return self.retained_error
        return current

    def property(self, name: str) -> object:
        self.last_property = name
        if self.failures.get(name, _FakeError()).isValid():
            return None
        return self.values.get(name)


def _backend(connection: _FakeConnection) -> QtDbusBackend:
    return QtDbusBackend(connection=cast(Any, connection))


def _subscribe(backend: QtDbusBackend) -> Subscription:
    return backend.subscribe_player_signals(
        "org.mpris.MediaPlayer2.test",
        cast(Callable[..., None], lambda *_arguments: None),
        cast(Callable[..., None], lambda *_arguments: None),
    )


def test_player_proxy_declares_metadata_as_qvariant_map() -> None:
    QCoreApplication.instance() or QCoreApplication(["lyricflow-test"])
    connection = QDBusConnection("lyricflow-test-no-bus")
    proxy = _MprisPlayerInterface("org.mpris.MediaPlayer2.test", connection)
    meta_object = proxy.metaObject()
    declared_types = {
        meta_object.property(index).name(): meta_object.property(index).typeName()
        for index in range(meta_object.propertyOffset(), meta_object.propertyCount())
    }

    assert declared_types["Metadata"] == "QVariantMap"
    assert declared_types["Position"] == "qlonglong"


def test_typed_property_reads_decode_real_shaped_metadata_without_get_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _backend(_FakeConnection())
    remotes: list[_FakeRemoteInterface] = []

    def typed_remote(*_arguments: object) -> _FakeRemoteInterface:
        remote = _FakeRemoteInterface()
        remotes.append(remote)
        return remote

    monkeypatch.setattr(backend, "_remote_interface", typed_remote)
    monkeypatch.setattr(
        backend,
        "_call",
        lambda *_arguments: pytest.fail("property reads must not use raw GetAll"),
    )

    result = backend.read_properties(
        "org.mpris.MediaPlayer2.test",
        "org.mpris.MediaPlayer2.Player",
    )

    assert len(remotes) == 1
    assert result.diagnostics == ()
    assert result.values["PlaybackStatus"] == "Playing"
    assert result.values["Position"] == 123456
    assert result.values["Metadata"] == {
        "mpris:artUrl": "file:///tmp/cover.jpg",
        "mpris:length": 9876543,
        "mpris:trackid": "/track/42",
        "xesam:title": "Example",
        "xesam:artist": ("Artist",),
        "xesam:album": "Album",
        "xesam:url": "file:///music/example.flac",
    }


def test_each_typed_property_read_has_independent_error_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failures = {
        "MinimumRate": _FakeError(
            valid=True,
            name="org.freedesktop.DBus.Error.NotSupported",
            message="MinimumRate is not supported",
        )
    }
    backend = _backend(_FakeConnection())
    remotes: list[_FakeRemoteInterface] = []

    def sticky_remote(*_arguments: object) -> _FakeRemoteInterface:
        remote = _FakeRemoteInterface(failures, sticky_error=True)
        remotes.append(remote)
        return remote

    monkeypatch.setattr(backend, "_remote_interface", sticky_remote)

    result = backend.read_properties(
        "org.mpris.MediaPlayer2.test",
        "org.mpris.MediaPlayer2.Player",
    )

    assert len(remotes) == 1
    assert result.values["PlaybackStatus"] == "Playing"
    assert result.values["Position"] == 123456
    assert result.values["Rate"] == 1.0
    assert result.values["Volume"] == 0.75
    assert "MinimumRate" not in result.values
    assert result.diagnostics == (
        "MinimumRate: unavailable (org.freedesktop.DBus.Error.NotSupported: "
        "MinimumRate is not supported)",
    )


def test_service_disappearance_during_typed_read_remains_player_level_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _backend(_FakeConnection())

    def disappeared(*_arguments: object) -> _FakeRemoteInterface:
        raise MprisServiceUnavailable("name has no owner")

    monkeypatch.setattr(backend, "_remote_interface", disappeared)

    with pytest.raises(MprisServiceUnavailable, match="name has no owner"):
        backend.read_properties(
            "org.mpris.MediaPlayer2.test",
            "org.mpris.MediaPlayer2.Player",
        )


def test_player_signal_subscriptions_use_runtime_compatible_slot_strings() -> None:
    connection = _FakeConnection()
    subscription = _subscribe(_backend(connection))

    assert len(connection.connect_calls) == 2
    properties, seeked = connection.connect_calls
    assert properties[2:4] == (DBUS_PROPERTIES_INTERFACE, "PropertiesChanged")
    assert properties[-1] == PROPERTIES_SLOT
    assert seeked[2:4] == ("org.mpris.MediaPlayer2.Player", "Seeked")
    assert seeked[-1] == SEEKED_SLOT
    assert isinstance(properties[-1], str)
    assert isinstance(seeked[-1], str)

    subscription.close()

    assert len(connection.disconnect_calls) == 2
    assert connection.disconnect_calls[0] == properties
    assert connection.disconnect_calls[1] == seeked


@pytest.mark.parametrize("results", [(False, True), (True, False)])
def test_false_signal_connection_result_is_an_explicit_error(
    results: tuple[bool, bool],
) -> None:
    connection = _FakeConnection(connect_results=results)

    with pytest.raises(MprisBackendError, match="SubscriptionError"):
        _subscribe(_backend(connection))


def test_binding_value_error_is_converted_to_backend_error() -> None:
    connection = _FakeConnection(connect_error=ValueError("wrong argument values"))

    with pytest.raises(MprisBackendError, match="rejected an MPRIS signal"):
        _subscribe(_backend(connection))


def test_false_disconnect_result_is_an_explicit_error() -> None:
    connection = _FakeConnection(disconnect_results=(True, False))
    subscription = _subscribe(_backend(connection))

    with pytest.raises(MprisBackendError, match="could not disconnect MPRIS signals"):
        subscription.close()
