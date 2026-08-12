"""QtDBus wrapper-conversion tests that require no live session bus."""

from typing import Any

import pytest
from PySide6.QtCore import QObject
from PySide6.QtDBus import QDBusObjectPath, QDBusSignature, QDBusVariant

from lyricflow.infrastructure.mpris import qt_dbus_values
from lyricflow.infrastructure.mpris.backend import MprisBackendError
from lyricflow.infrastructure.mpris.qt_dbus_values import (
    PROPERTIES_SLOT,
    SEEKED_SLOT,
    PlayerSignalReceiver,
    plain_dbus_value,
)


def test_qt_dbus_wrappers_are_removed_recursively() -> None:
    value = {
        "path": QDBusVariant(QDBusObjectPath("/track/1")),
        "signature": QDBusSignature("a{sv}"),
        "nested": [QDBusVariant("text"), 42],
    }

    assert plain_dbus_value(value) == {
        "path": "/track/1",
        "signature": "a{sv}",
        "nested": ("text", 42),
    }


@pytest.mark.parametrize("value", [None, "text", True, 42, 1.25, b"bytes"])
def test_ordinary_primitive_values_are_preserved(value: object) -> None:
    assert plain_dbus_value(value) == value


def test_representative_get_all_metadata_remains_typed() -> None:
    value = {
        "PlaybackStatus": "Playing",
        "Position": 123456,
        "Metadata": QDBusVariant(
            {
                "mpris:trackid": QDBusVariant(QDBusObjectPath("/track/42")),
                "mpris:length": QDBusVariant(9876543),
                "xesam:title": QDBusVariant("Example"),
                "xesam:artist": QDBusVariant(["One", "Two"]),
            }
        ),
    }

    assert plain_dbus_value(value) == {
        "PlaybackStatus": "Playing",
        "Position": 123456,
        "Metadata": {
            "mpris:trackid": "/track/42",
            "mpris:length": 9876543,
            "xesam:title": "Example",
            "xesam:artist": ("One", "Two"),
        },
    }


class _NoProgressArgument:
    def __init__(self, *, self_returning: bool = False) -> None:
        self._self_returning = self_returning

    def currentSignature(self) -> str:
        return "a{sv}"

    def currentType(self) -> Any:
        class ElementType:
            name = "MapType"

        return ElementType()

    def asVariant(self) -> object:
        if self._self_returning:
            return self
        return _NoProgressArgument()


@pytest.mark.parametrize("self_returning", [True, False])
def test_qdbus_argument_that_makes_no_progress_is_controlled(
    monkeypatch: pytest.MonkeyPatch,
    self_returning: bool,
) -> None:
    monkeypatch.setattr(qt_dbus_values, "QDBusArgument", _NoProgressArgument)

    with pytest.raises(
        MprisBackendError,
        match=r"conversion made no progress for signature a\{sv\}",
    ):
        plain_dbus_value(_NoProgressArgument(self_returning=self_returning))


@pytest.mark.parametrize("container_type", [list, dict])
def test_cyclic_python_container_is_controlled(container_type: type[object]) -> None:
    if container_type is list:
        value: object = []
        assert isinstance(value, list)
        value.append(value)
    else:
        value = {}
        assert isinstance(value, dict)
        value["cycle"] = value

    with pytest.raises(MprisBackendError, match="cyclic D-Bus container"):
        plain_dbus_value(value)


def test_opaque_qt_object_cannot_leak_past_backend() -> None:
    with pytest.raises(MprisBackendError, match="unsupported D-Bus value type QObject"):
        plain_dbus_value(QObject())


def test_malformed_cyclic_signal_value_becomes_receiver_diagnostic() -> None:
    properties: list[tuple[str, str, object, tuple[str, ...], str | None]] = []
    receiver = PlayerSignalReceiver(
        "org.mpris.MediaPlayer2.test",
        lambda service, interface, changed, invalidated, error: properties.append(
            (service, interface, changed, invalidated, error)
        ),
        lambda _service, _position: None,
    )
    cycle: dict[str, object] = {}
    cycle["Metadata"] = cycle

    receiver.propertiesChanged(
        "org.mpris.MediaPlayer2.Player",
        cycle,
        [],
    )

    assert properties[0][2:4] == ({}, ())
    assert properties[0][4] is not None
    assert "cyclic D-Bus container" in properties[0][4]


def test_qt_signal_receiver_exposes_plain_values_and_exact_slot_signatures() -> None:
    properties: list[tuple[str, str, object, tuple[str, ...], str | None]] = []
    seeks: list[tuple[str, object]] = []
    receiver = PlayerSignalReceiver(
        "org.mpris.MediaPlayer2.test",
        lambda service, interface, changed, invalidated, error: properties.append(
            (service, interface, changed, invalidated, error)
        ),
        lambda service, position: seeks.append((service, position)),
    )

    receiver.propertiesChanged(
        "org.mpris.MediaPlayer2.Player",
        {"PlaybackStatus": QDBusVariant("Paused")},
        ["Volume"],
    )
    receiver.seeked(5000000)

    changed = properties[0][2]
    assert isinstance(changed, dict)
    assert changed == {"PlaybackStatus": "Paused"}
    assert properties[0][3:] == (("Volume",), None)
    assert seeks == [("org.mpris.MediaPlayer2.test", 5000000)]

    meta_object = receiver.metaObject()
    signatures = {
        bytes(meta_object.method(index).methodSignature()).decode()
        for index in range(meta_object.methodOffset(), meta_object.methodCount())
    }
    assert "propertiesChanged(QString,QVariantMap,QStringList)" in signatures
    assert "seeked(qlonglong)" in signatures
    assert isinstance(PROPERTIES_SLOT, str)
    assert isinstance(SEEKED_SLOT, str)
    assert PROPERTIES_SLOT == "1propertiesChanged(QString,QVariantMap,QStringList)"
    assert SEEKED_SLOT == "1seeked(qlonglong)"
