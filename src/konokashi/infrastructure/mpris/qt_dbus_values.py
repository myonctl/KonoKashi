"""QtDBus value unwrapping and exact signal receiver slots."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TypeAlias, cast

from PySide6.QtCore import SLOT, QByteArray, QObject, Slot
from PySide6.QtDBus import (
    QDBusArgument,
    QDBusObjectPath,
    QDBusSignature,
    QDBusVariant,
)

from konokashi.infrastructure.mpris.backend import (
    MprisBackendError,
    MprisPropertyRead,
    PropertiesChangedHandler,
    SeekedHandler,
)

PropertyReader: TypeAlias = Callable[[str, str], MprisPropertyRead]

PROPERTIES_SLOT = cast(
    bytes,
    SLOT("propertiesChanged(QString,QVariantMap,QStringList)"),
)
SEEKED_SLOT = cast(bytes, SLOT("seeked(qlonglong)"))

_MAX_CONTAINER_DEPTH = 64


def _argument_state(value: QDBusArgument) -> tuple[str, str]:
    """Describe conversion-relevant state without relying on wrapper identity."""

    return value.currentSignature(), value.currentType().name


def _plain_dbus_value(
    value: object,
    *,
    active_containers: frozenset[int],
    depth: int,
) -> object:
    if depth > _MAX_CONTAINER_DEPTH:
        raise MprisBackendError(
            f"D-Bus value exceeds {_MAX_CONTAINER_DEPTH} nested containers"
        )

    if isinstance(value, QDBusVariant):
        unpacked = value.variant()
        if unpacked is value:
            raise MprisBackendError("QDBusVariant conversion made no progress")
        return _plain_dbus_value(
            unpacked,
            active_containers=active_containers,
            depth=depth + 1,
        )
    if isinstance(value, QDBusObjectPath):
        return value.path()
    if isinstance(value, QDBusSignature):
        return value.signature()
    if isinstance(value, QDBusArgument):
        state = _argument_state(value)
        unpacked = value.asVariant()
        if unpacked is value or (
            isinstance(unpacked, QDBusArgument) and _argument_state(unpacked) == state
        ):
            signature = state[0] or "unknown"
            raise MprisBackendError(
                f"QDBusArgument conversion made no progress for signature {signature}"
            )
        return _plain_dbus_value(
            unpacked,
            active_containers=active_containers,
            depth=depth + 1,
        )
    if isinstance(value, QByteArray):
        return bytes(value.data())
    if value is None or isinstance(value, (str, bool, int, float, bytes)):
        return value

    container_id = id(value)
    if container_id in active_containers:
        raise MprisBackendError(
            f"cyclic D-Bus container of type {type(value).__name__}"
        )
    descendants = active_containers | {container_id}
    if isinstance(value, Mapping):
        plain: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise MprisBackendError(
                    f"D-Bus mapping has non-string key of type {type(key).__name__}"
                )
            plain[key] = _plain_dbus_value(
                item,
                active_containers=descendants,
                depth=depth + 1,
            )
        return plain
    if isinstance(value, Sequence):
        return tuple(
            _plain_dbus_value(
                item,
                active_containers=descendants,
                depth=depth + 1,
            )
            for item in value
        )
    raise MprisBackendError(f"unsupported D-Bus value type {type(value).__name__}")


def plain_dbus_value(value: object) -> object:
    """Recursively remove QtDBus wrappers before values leave infrastructure."""

    return _plain_dbus_value(value, active_containers=frozenset(), depth=0)


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
        property_reader: PropertyReader | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._properties_handler = properties_handler
        self._seeked_handler = seeked_handler
        self._property_reader = property_reader

    @Slot(str, "QVariantMap", "QStringList")
    def propertiesChanged(
        self,
        interface: str,
        changed: object,
        invalidated: list[str],
    ) -> None:
        """Translate one PropertiesChanged payload to plain Python values."""

        decode_diagnostics: tuple[str, ...] = ()
        try:
            plain_changed = plain_mapping(changed)
        except MprisBackendError as conversion_error:
            if self._property_reader is None:
                self._properties_handler(
                    self._service,
                    interface,
                    {},
                    (),
                    (str(conversion_error),),
                )
                return
            try:
                refreshed = self._property_reader(self._service, interface)
            except MprisBackendError as refresh_error:
                self._properties_handler(
                    self._service,
                    interface,
                    {},
                    (),
                    (
                        f"PropertiesChanged payload could not be decoded "
                        f"({conversion_error}); property refresh failed "
                        f"({refresh_error})",
                    ),
                )
                return
            plain_changed = refreshed.values
            decode_diagnostics = refreshed.diagnostics
        try:
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
                (str(error),),
            )
            return
        self._properties_handler(
            self._service,
            interface,
            plain_changed,
            plain_invalidated,
            decode_diagnostics,
        )

    @Slot("qlonglong")
    def seeked(self, position: int) -> None:
        """Forward a Seeked signal while retaining integer microseconds."""

        self._seeked_handler(self._service, position)
