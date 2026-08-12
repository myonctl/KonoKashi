"""QtDBus wrapper-conversion tests that require no live session bus."""

import json
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject
from PySide6.QtDBus import QDBusObjectPath, QDBusSignature, QDBusVariant

from lyricflow.infrastructure.mpris import qt_dbus_values
from lyricflow.infrastructure.mpris.backend import MprisBackendError, MprisPropertyRead
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
    properties: list[tuple[str, str, object, tuple[str, ...], tuple[str, ...]]] = []
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
    assert "cyclic D-Bus container" in properties[0][4][0]


def test_live_firefox_a_sv_shape_is_recovered_by_typed_property_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regress the raw map wrapper observed in Firefox PropertiesChanged."""

    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "mpris"
        / "firefox_properties_changed_raw_map.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture["runtime_type"] == "QDBusArgument"
    assert fixture["signature"] == "a{sv}"
    assert fixture["element_type"] == "MapType"
    assert fixture["as_variant_makes_progress"] is False
    service = fixture["service"]
    assert isinstance(service, str)

    monkeypatch.setattr(qt_dbus_values, "QDBusArgument", _NoProgressArgument)
    properties: list[tuple[str, str, object, tuple[str, ...], tuple[str, ...]]] = []
    reads: list[tuple[str, str]] = []

    def read_properties(service: str, interface: str) -> MprisPropertyRead:
        reads.append((service, interface))
        return MprisPropertyRead(
            {
                "PlaybackStatus": "Playing",
                "Metadata": {
                    "xesam:title": "New Firefox video",
                    "xesam:artist": ("Uploader",),
                    "xesam:url": "https://www.youtube.com/watch?v=fixture",
                },
            }
        )

    receiver = PlayerSignalReceiver(
        service,
        lambda service, interface, changed, invalidated, diagnostics: properties.append(
            (service, interface, changed, invalidated, diagnostics)
        ),
        lambda _service, _position: None,
        read_properties,
    )

    receiver.propertiesChanged(
        "org.mpris.MediaPlayer2.Player",
        _NoProgressArgument(),
        [],
    )

    assert reads == [
        (
            "org.mpris.MediaPlayer2.firefox.instance_1_58",
            "org.mpris.MediaPlayer2.Player",
        )
    ]
    assert properties[0][2] == {
        "PlaybackStatus": "Playing",
        "Metadata": {
            "xesam:title": "New Firefox video",
            "xesam:artist": ("Uploader",),
            "xesam:url": "https://www.youtube.com/watch?v=fixture",
        },
    }
    assert properties[0][4] == ()


def test_qt_signal_receiver_exposes_plain_values_and_exact_slot_signatures() -> None:
    properties: list[tuple[str, str, object, tuple[str, ...], tuple[str, ...]]] = []
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
    assert properties[0][3:] == (("Volume",), ())
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
