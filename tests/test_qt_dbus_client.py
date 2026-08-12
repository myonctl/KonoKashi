"""Production QtDBus subscription construction tests without a live bus."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest
from PySide6.QtDBus import QDBusVariant

from lyricflow.infrastructure.mpris import qt_dbus_client
from lyricflow.infrastructure.mpris.backend import (
    MprisBackendError,
    MprisServiceUnavailable,
    Subscription,
)
from lyricflow.infrastructure.mpris.qt_dbus_client import (
    DBUS_PROPERTIES_INTERFACE,
    QtDbusBackend,
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


class _FakeProperty:
    def __init__(self, name: str) -> None:
        self._name = name

    def isReadable(self) -> bool:
        return True

    def name(self) -> str:
        return self._name


class _FakeMetaObject:
    _names = ("PlaybackStatus", "Metadata", "Position", "MaximumRate")

    def propertyOffset(self) -> int:
        return 0

    def propertyCount(self) -> int:
        return len(self._names)

    def property(self, index: int) -> _FakeProperty:
        return _FakeProperty(self._names[index])

    def indexOfProperty(self, name: str) -> int:
        try:
            return self._names.index(name)
        except ValueError:
            return -1


class _FakeRemoteInterface:
    def __init__(self, failures: dict[str, _FakeError] | None = None) -> None:
        self.timeout_ms: int | None = None
        self.failures = failures or {}
        self.last_property: str | None = None
        self.values = {
            "PlaybackStatus": "Playing",
            "Metadata": {
                "xesam:title": QDBusVariant("Example"),
                "xesam:artist": QDBusVariant(["Artist"]),
            },
            "Position": 123456,
            "MaximumRate": 2.0,
        }

    def setTimeout(self, timeout_ms: int) -> None:
        self.timeout_ms = timeout_ms

    def isValid(self) -> bool:
        return True

    def lastError(self) -> _FakeError:
        if self.last_property is None:
            return _FakeError()
        return self.failures.get(self.last_property, _FakeError())

    def metaObject(self) -> _FakeMetaObject:
        return _FakeMetaObject()

    def property(self, name: str) -> object:
        self.last_property = name
        return self.values.get(name)


def _backend(connection: _FakeConnection) -> QtDbusBackend:
    return QtDbusBackend(connection=cast(Any, connection))


def _subscribe(backend: QtDbusBackend) -> Subscription:
    return backend.subscribe_player_signals(
        "org.mpris.MediaPlayer2.test",
        cast(Callable[..., None], lambda *_arguments: None),
        cast(Callable[..., None], lambda *_arguments: None),
    )


def test_get_all_success_uses_one_compound_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _backend(_FakeConnection())
    calls: list[tuple[object, ...]] = []

    def successful_call(*arguments: object) -> tuple[object, ...]:
        calls.append(arguments)
        return (
            {
                "PlaybackStatus": "Playing",
                "Metadata": {
                    "xesam:title": QDBusVariant("Example"),
                    "xesam:artist": QDBusVariant(["Artist"]),
                },
                "Position": 123456,
            },
        )

    monkeypatch.setattr(backend, "_call", successful_call)

    result = backend.read_properties(
        "org.mpris.MediaPlayer2.test",
        "org.mpris.MediaPlayer2.Player",
    )

    assert len(calls) == 1
    assert result.diagnostics == ()
    assert result.values == {
        "PlaybackStatus": "Playing",
        "Metadata": {
            "xesam:title": "Example",
            "xesam:artist": ("Artist",),
        },
        "Position": 123456,
    }


def test_broken_get_all_falls_back_and_keeps_optional_property_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = _FakeRemoteInterface(
        {
            "MaximumRate": _FakeError(
                valid=True,
                name="org.freedesktop.DBus.Error.NotSupported",
                message="MaximumRate is not supported",
            )
        }
    )
    monkeypatch.setattr(qt_dbus_client, "QDBusInterface", lambda *_args: remote)
    backend = _backend(_FakeConnection())
    get_all_calls = 0

    def broken_get_all(*_arguments: object) -> tuple[object, ...]:
        nonlocal get_all_calls
        get_all_calls += 1
        raise MprisBackendError(
            "QDBusArgument conversion made no progress for signature a{sv}"
        )

    monkeypatch.setattr(backend, "_call", broken_get_all)

    result = backend.read_properties(
        "org.mpris.MediaPlayer2.test",
        "org.mpris.MediaPlayer2.Player",
    )

    assert remote.timeout_ms == 5_000
    assert result.values["PlaybackStatus"] == "Playing"
    assert result.values["Metadata"] == {
        "xesam:title": "Example",
        "xesam:artist": ("Artist",),
    }
    assert result.values["Position"] == 123456
    assert "MaximumRate" not in result.values
    assert not any("a{sv}" in item for item in result.diagnostics)
    assert any("MaximumRate: unavailable" in item for item in result.diagnostics)

    backend.read_properties(
        "org.mpris.MediaPlayer2.another",
        "org.mpris.MediaPlayer2.Player",
    )
    assert get_all_calls == 1


def test_service_disappearance_during_get_all_remains_player_level_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _backend(_FakeConnection())

    def disappeared(*_arguments: object) -> tuple[object, ...]:
        raise MprisServiceUnavailable("name has no owner")

    monkeypatch.setattr(backend, "_call", disappeared)

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
