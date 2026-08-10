"""Qt-free player discovery, inspection, and event-registry implementation."""

from __future__ import annotations

from collections.abc import Mapping

from lyricflow.application.ports import PlayerEventHandler
from lyricflow.domain.models import (
    InspectionFailure,
    PlayerEvent,
    PlayerEventKind,
    PlayerInspection,
    PlayerListResult,
    PlayerWatchStart,
)
from lyricflow.infrastructure.mpris.backend import (
    MprisBackendError,
    MprisBusBackend,
    MprisServiceUnavailable,
    Subscription,
)
from lyricflow.infrastructure.mpris.metadata_mapper import (
    MPRIS_PLAYER_INTERFACE,
    MPRIS_PREFIX,
    MPRIS_ROOT_INTERFACE,
    full_service_name,
    map_player_snapshot,
    short_service_name,
)


def _failure(
    bus_name: str,
    category: InspectionFailure,
    message: str,
) -> PlayerInspection:
    return PlayerInspection(
        service_name=short_service_name(bus_name),
        bus_name=bus_name,
        failure=category,
        message=message,
    )


class MprisClient:
    """Inspect current MPRIS services through a replaceable bus backend."""

    def __init__(self, backend: MprisBusBackend) -> None:
        self._backend = backend

    def list_players(self) -> PlayerListResult:
        """Enumerate and inspect all players without selection or suppression."""

        try:
            bus_names = self._backend.list_service_names()
        except MprisBackendError as error:
            return PlayerListResult(error=str(error))

        mpris_names = sorted(
            {name for name in bus_names if name.startswith(MPRIS_PREFIX)}
        )
        return PlayerListResult(
            players=tuple(self.inspect_player(name) for name in mpris_names)
        )

    def inspect_player(self, service_name: str) -> PlayerInspection:
        """Read and map one player, treating disappearance as an expected race."""

        bus_name = full_service_name(service_name)
        diagnostics: list[str] = []
        try:
            root_properties = self._backend.get_all(bus_name, MPRIS_ROOT_INTERFACE)
            player_properties = dict(
                self._backend.get_all(bus_name, MPRIS_PLAYER_INTERFACE)
            )
            if "Position" not in player_properties:
                try:
                    player_properties["Position"] = self._backend.get_property(
                        bus_name, MPRIS_PLAYER_INTERFACE, "Position"
                    )
                except MprisServiceUnavailable:
                    raise
                except MprisBackendError as error:
                    diagnostics.append(f"Position: unavailable ({error})")
        except MprisServiceUnavailable as error:
            return _failure(
                bus_name,
                InspectionFailure.UNAVAILABLE,
                f"player disappeared during inspection ({error})",
            )
        except MprisBackendError as error:
            return _failure(bus_name, InspectionFailure.BUS_ERROR, str(error))

        return PlayerInspection(
            service_name=short_service_name(bus_name),
            bus_name=bus_name,
            snapshot=map_player_snapshot(
                bus_name,
                root_properties,
                player_properties,
                diagnostics,
            ),
        )


class MprisMonitor:
    """Maintain event subscriptions for every currently registered player."""

    def __init__(self, backend: MprisBusBackend) -> None:
        self._backend = backend
        self._handler: PlayerEventHandler | None = None
        self._service_subscription: Subscription | None = None
        self._known_players: set[str] = set()
        self._player_subscriptions: dict[str, Subscription] = {}

    def start(self, handler: PlayerEventHandler) -> PlayerWatchStart:
        """Attach lifecycle first, then enumerate to close the startup race."""

        if self._service_subscription is not None:
            return PlayerWatchStart(error="watcher is already running")
        self._handler = handler
        try:
            self._service_subscription = self._backend.subscribe_service_changes(
                self._service_registered,
                self._service_unregistered,
            )
            bus_names = self._backend.list_service_names()
        except MprisBackendError as error:
            self.close()
            return PlayerWatchStart(error=str(error))

        for bus_name in sorted(set(bus_names)):
            if bus_name.startswith(MPRIS_PREFIX):
                self._attach_player(bus_name, emit_appeared=False)
        return PlayerWatchStart(
            current_services=tuple(
                short_service_name(name) for name in sorted(self._known_players)
            )
        )

    def close(self) -> None:
        """Disconnect all watchers; safe to call repeatedly."""

        for subscription in tuple(self._player_subscriptions.values()):
            subscription.close()
        self._player_subscriptions.clear()
        self._known_players.clear()
        if self._service_subscription is not None:
            self._service_subscription.close()
            self._service_subscription = None
        self._handler = None

    def _emit(self, event: PlayerEvent) -> None:
        if self._handler is not None:
            self._handler(event)

    def _attach_player(self, bus_name: str, *, emit_appeared: bool) -> None:
        if bus_name in self._known_players:
            return
        self._known_players.add(bus_name)
        diagnostics: tuple[str, ...] = ()
        try:
            subscription = self._backend.subscribe_player_signals(
                bus_name,
                self._properties_changed,
                self._seeked,
            )
        except MprisBackendError as error:
            diagnostics = (f"could not attach player signals: {error}",)
        else:
            self._player_subscriptions[bus_name] = subscription
        if emit_appeared:
            self._emit(
                PlayerEvent(
                    PlayerEventKind.PLAYER_APPEARED,
                    short_service_name(bus_name),
                    diagnostics=diagnostics,
                )
            )

    def _service_registered(self, bus_name: str) -> None:
        if bus_name.startswith(MPRIS_PREFIX):
            self._attach_player(bus_name, emit_appeared=True)

    def _service_unregistered(self, bus_name: str) -> None:
        if bus_name not in self._known_players:
            return
        self._known_players.remove(bus_name)
        subscription = self._player_subscriptions.pop(bus_name, None)
        if subscription is not None:
            subscription.close()
        self._emit(
            PlayerEvent(
                PlayerEventKind.PLAYER_DISAPPEARED,
                short_service_name(bus_name),
            )
        )

    def _properties_changed(
        self,
        bus_name: str,
        interface: str,
        changed: Mapping[str, object],
        invalidated: tuple[str, ...],
        decode_error: str | None,
    ) -> None:
        if interface != MPRIS_PLAYER_INTERFACE:
            return
        service_name = short_service_name(bus_name)
        if decode_error is not None:
            self._emit(
                PlayerEvent(
                    PlayerEventKind.DIAGNOSTIC,
                    service_name,
                    diagnostics=(decode_error,),
                )
            )
            return

        if "PlaybackStatus" in changed or "PlaybackStatus" in invalidated:
            snapshot = map_player_snapshot(bus_name, {}, changed)
            self._emit(
                PlayerEvent(
                    PlayerEventKind.PLAYBACK_STATUS_CHANGED,
                    service_name,
                    playback_status=snapshot.playback_status,
                    diagnostics=snapshot.diagnostics,
                )
            )
        if "Metadata" in changed or "Metadata" in invalidated:
            snapshot = map_player_snapshot(bus_name, {}, changed)
            self._emit(
                PlayerEvent(
                    PlayerEventKind.METADATA_CHANGED,
                    service_name,
                    metadata=snapshot.metadata,
                    diagnostics=snapshot.diagnostics,
                )
            )

    def _seeked(self, bus_name: str, position: object) -> None:
        if isinstance(position, int) and not isinstance(position, bool):
            diagnostics = (
                (f"Seeked: negative microsecond value {position}",)
                if position < 0
                else ()
            )
            self._emit(
                PlayerEvent(
                    PlayerEventKind.SEEKED,
                    short_service_name(bus_name),
                    position_us=position,
                    diagnostics=diagnostics,
                )
            )
            return
        self._emit(
            PlayerEvent(
                PlayerEventKind.SEEKED,
                short_service_name(bus_name),
                diagnostics=(
                    f"Seeked: expected integer microseconds, got "
                    f"{type(position).__name__}",
                ),
            )
        )
