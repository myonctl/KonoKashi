"""QtDBus wrapper-conversion tests that require no live session bus."""

import pytest
from PySide6.QtCore import QObject
from PySide6.QtDBus import QDBusObjectPath, QDBusSignature, QDBusVariant

from lyricflow.infrastructure.mpris.backend import MprisBackendError
from lyricflow.infrastructure.mpris.qt_dbus_values import (
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


def test_opaque_qt_object_cannot_leak_past_backend() -> None:
    with pytest.raises(MprisBackendError, match="unsupported D-Bus value type QObject"):
        plain_dbus_value(QObject())


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
