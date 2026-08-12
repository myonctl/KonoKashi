"""Qt-independent boundary implemented by the production QtDBus backend."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

ServiceHandler = Callable[[str], None]
PropertiesChangedHandler = Callable[
    [str, str, Mapping[str, object], tuple[str, ...], str | None], None
]
SeekedHandler = Callable[[str, object], None]


class MprisBackendError(RuntimeError):
    """A sanitized D-Bus boundary failure."""


class MprisServiceUnavailable(MprisBackendError):
    """The target service vanished or has no current owner."""


class Subscription(Protocol):
    """Disconnect one or more backend signal subscriptions."""

    def close(self) -> None:
        """Disconnect idempotently."""


class MprisBusBackend(Protocol):
    """Minimal D-Bus operations required by Stage 1."""

    def list_service_names(self) -> Sequence[str]:
        """Return every currently owned session-bus name."""

    def get_all(self, service: str, interface: str) -> Mapping[str, object]:
        """Read every property for an interface."""

    def get_property(self, service: str, interface: str, name: str) -> object:
        """Read one property, used for MPRIS Position when needed."""

    def subscribe_service_changes(
        self,
        on_registered: ServiceHandler,
        on_unregistered: ServiceHandler,
    ) -> Subscription:
        """Subscribe to session-bus name registration changes."""

    def subscribe_player_signals(
        self,
        service: str,
        on_properties_changed: PropertiesChangedHandler,
        on_seeked: SeekedHandler,
    ) -> Subscription:
        """Subscribe to one player's PropertiesChanged and Seeked signals."""
