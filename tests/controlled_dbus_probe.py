"""Opt-in private-bus probe for the PySide6 raw ``a{sv}`` recovery path."""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import QCoreApplication, QMetaType
from PySide6.QtDBus import QDBus, QDBusArgument, QDBusConnection, QDBusMessage

from konokashi.infrastructure.mpris.backend import MprisPropertyRead
from konokashi.infrastructure.mpris.qt_dbus_values import PlayerSignalReceiver


def main() -> int:
    """Verify the installed binding's real raw-map shape and typed recovery."""

    QCoreApplication.instance() or QCoreApplication(["konokashi-dbus-probe"])
    connection = QDBusConnection.sessionBus()
    if not connection.isConnected():
        raise RuntimeError(connection.lastError().message())

    message = QDBusMessage.createMethodCall(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "GetConnectionCredentials",
    )
    message.setArguments([connection.baseService()])
    reply = connection.call(message, QDBus.CallMode.Block, 5_000)
    if reply.type() == QDBusMessage.MessageType.ErrorMessage:
        raise RuntimeError(f"{reply.errorName()}: {reply.errorMessage()}")
    arguments = reply.arguments()
    if len(arguments) != 1 or not isinstance(arguments[0], QDBusArgument):
        raise RuntimeError("private bus did not return the expected raw a{sv} value")
    raw_map = arguments[0]
    if raw_map.currentSignature() != "a{sv}":
        raise RuntimeError(f"unexpected signature {raw_map.currentSignature()!r}")

    captured: list[Mapping[str, object]] = []

    def capture(
        _service: str,
        _interface: str,
        changed: Mapping[str, object],
        _invalidated: tuple[str, ...],
        diagnostics: tuple[str, ...],
    ) -> None:
        if diagnostics:
            raise RuntimeError("; ".join(diagnostics))
        captured.append(changed)

    receiver = PlayerSignalReceiver(
        "org.mpris.MediaPlayer2.controlled",
        capture,
        lambda _service, _position: None,
        lambda _service, _interface: MprisPropertyRead(
            {
                "PlaybackStatus": "Playing",
                "Metadata": {
                    "xesam:title": "Controlled metadata refresh",
                    "xesam:artist": ("Probe",),
                },
            }
        ),
    )
    receiver.propertiesChanged("org.mpris.MediaPlayer2.Player", raw_map, [])
    if not captured or captured[0]["PlaybackStatus"] != "Playing":
        raise RuntimeError("typed property refresh did not recover the raw map")
    if not QMetaType.fromName(b"QVariantMap").isValid():
        raise RuntimeError("Qt's built-in QVariantMap metatype is unavailable")

    print(
        "controlled D-Bus probe passed: raw a{sv} observed; "
        "typed property refresh produced a Python mapping"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
