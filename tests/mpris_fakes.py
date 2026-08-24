"""Controlled D-Bus boundary used by Stage 1 tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from lyriflux.infrastructure.mpris.backend import (
    MprisBackendError,
    MprisPropertyRead,
    PropertiesChangedHandler,
    SeekedHandler,
    ServiceHandler,
)


class FakeSubscription:
    def __init__(self, close_callback: Callable[[], None]) -> None:
        self._close_callback: Callable[[], None] | None = close_callback
        self.closed = False

    def close(self) -> None:
        callback = self._close_callback
        self._close_callback = None
        self.closed = True
        if callback is not None:
            callback()


class FakeMprisBackend:
    """In-memory implementation with explicit event emission and failures."""

    def __init__(self, names: Sequence[str] = ()) -> None:
        self.names = list(names)
        self.properties: dict[tuple[str, str], Mapping[str, object]] = {}
        self.single_properties: dict[tuple[str, str, str], object] = {}
        self.get_all_failures: dict[tuple[str, str], MprisBackendError] = {}
        self.property_diagnostics: dict[tuple[str, str], tuple[str, ...]] = {}
        self.get_property_failures: dict[tuple[str, str, str], MprisBackendError] = {}
        self.list_failure: MprisBackendError | None = None
        self.subscribe_player_failures: dict[str, MprisBackendError] = {}
        self._registered_handlers: list[ServiceHandler] = []
        self._unregistered_handlers: list[ServiceHandler] = []
        self._player_handlers: dict[
            str, tuple[PropertiesChangedHandler, SeekedHandler]
        ] = {}

    def list_service_names(self) -> Sequence[str]:
        if self.list_failure is not None:
            raise self.list_failure
        return tuple(self.names)

    def read_properties(self, service: str, interface: str) -> MprisPropertyRead:
        failure = self.get_all_failures.get((service, interface))
        if failure is not None:
            raise failure
        try:
            values = self.properties[(service, interface)]
        except KeyError as error:
            raise MprisBackendError(f"no fixture for {interface}") from error
        return MprisPropertyRead(
            values,
            self.property_diagnostics.get((service, interface), ()),
        )

    def get_property(self, service: str, interface: str, name: str) -> object:
        key = (service, interface, name)
        failure = self.get_property_failures.get(key)
        if failure is not None:
            raise failure
        try:
            return self.single_properties[key]
        except KeyError as error:
            raise MprisBackendError("property is not available") from error

    def subscribe_service_changes(
        self,
        on_registered: ServiceHandler,
        on_unregistered: ServiceHandler,
    ) -> FakeSubscription:
        self._registered_handlers.append(on_registered)
        self._unregistered_handlers.append(on_unregistered)

        def close() -> None:
            self._registered_handlers.remove(on_registered)
            self._unregistered_handlers.remove(on_unregistered)

        return FakeSubscription(close)

    def subscribe_player_signals(
        self,
        service: str,
        on_properties_changed: PropertiesChangedHandler,
        on_seeked: SeekedHandler,
    ) -> FakeSubscription:
        failure = self.subscribe_player_failures.get(service)
        if failure is not None:
            raise failure
        self._player_handlers[service] = (on_properties_changed, on_seeked)

        def close() -> None:
            self._player_handlers.pop(service, None)

        return FakeSubscription(close)

    def emit_registered(self, service: str) -> None:
        self.names.append(service)
        for handler in tuple(self._registered_handlers):
            handler(service)

    def emit_unregistered(self, service: str) -> None:
        if service in self.names:
            self.names.remove(service)
        for handler in tuple(self._unregistered_handlers):
            handler(service)

    def emit_properties(
        self,
        service: str,
        changed: Mapping[str, object],
        *,
        interface: str = "org.mpris.MediaPlayer2.Player",
        invalidated: tuple[str, ...] = (),
        decode_error: str | None = None,
    ) -> None:
        properties_handler, _ = self._player_handlers[service]
        properties_handler(
            service,
            interface,
            changed,
            invalidated,
            (decode_error,) if decode_error is not None else (),
        )

    def emit_seeked(self, service: str, position: object) -> None:
        _, seeked_handler = self._player_handlers[service]
        seeked_handler(service, position)
