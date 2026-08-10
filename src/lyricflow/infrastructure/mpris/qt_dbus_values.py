"""QtDBus value unwrapping and exact signal receiver slots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6.QtCore import QByteArray, QObject, Slot
from PySide6.QtDBus import (
    QDBusArgument,
    QDBusObjectPath,
    QDBusSignature,
    QDBusVariant,
)

from lyricflow.infrastructure.mpris.backend import (
    MprisBackendError,
    PropertiesChangedHandler,
    SeekedHandler,
)

PROPERTIES_SLOT = b"1propertiesChanged(QString,QVariantMap,QStringList)"
SEEKED_SLOT = b"1seeked(qlonglong)"


def plain_dbus_value(value: object) -> object:
    """Recursively remove QtDBus wrappers before values leave infrastructure."""

    if isinstance(value, QDBusVariant):
        return plain_dbus_value(value.variant())
    if isinstance(value, QDBusObjectPath):
        return value.path()
    if isinstance(value, QDBusSignature):
        return value.signature()
    if isinstance(value, QDBusArgument):
        unpacked = value.asVariant()
        if unpacked is value:
            raise MprisBackendError("unsupported self-referential QDBusArgument")
        return plain_dbus_value(unpacked)
    if isinstance(value, QByteArray):
        return bytes(value.data())
    if value is None or isinstance(value, (str, bool, int, float, bytes)):
        return value
    if isinstance(value, Mapping):
        plain: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise MprisBackendError(
                    f"D-Bus mapping has non-string key of type {type(key).__name__}"
                )
            plain[key] = plain_dbus_value(item)
        return plain
    if isinstance(value, Sequence):
        return tuple(plain_dbus_value(item) for item in value)
    raise MprisBackendError(f"unsupported D-Bus value type {type(value).__name__}")


def plain_mapping(value: object) -> Mapping[str, object]:
    """Require a plain, string-keyed D-Bus property mapping."""

    plain = plain_dbus_value(value)
    if not isinstance(plain, Mapping):
        raise MprisBackendError(
            f"D-Bus property reply was {type(plain).__name__}, expected mapping"
        )
    if not all(isinstance(key, str) for key in plain):
        raise MprisBackendError("D-Bus property reply contains a non-string key")
    return plain


class PlayerSignalReceiver(QObject):
    """QObject whose slots exactly match MPRIS signal signatures."""

    def __init__(
        self,
        service: str,
        properties_handler: PropertiesChangedHandler,
        seeked_handler: SeekedHandler,
    ) -> None:
        super().__init__()
        self._service = service
        self._properties_handler = properties_handler
        self._seeked_handler = seeked_handler

    @Slot(str, "QVariantMap", "QStringList")
    def propertiesChanged(
        self,
        interface: str,
        changed: dict[str, object],
        invalidated: list[str],
    ) -> None:
        """Translate one PropertiesChanged payload to plain Python values."""

        try:
            plain_changed = plain_mapping(changed)
            if not all(isinstance(name, str) for name in invalidated):
                raise MprisBackendError(
                    "PropertiesChanged invalidated list contains a non-string"
                )
            plain_invalidated = tuple(invalidated)
        except MprisBackendError as error:
            self._properties_handler(
                self._service,
                interface,
                {},
                (),
                str(error),
            )
            return
        self._properties_handler(
            self._service,
            interface,
            plain_changed,
            plain_invalidated,
            None,
        )

    @Slot("qlonglong")
    def seeked(self, position: int) -> None:
        """Forward a Seeked signal while retaining integer microseconds."""

        self._seeked_handler(self._service, position)
