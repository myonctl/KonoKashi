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
    MprisPropertyRead,
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
            root_read = self._read_interface(
                bus_name, MPRIS_ROOT_INTERFACE, "root properties"
            )
            player_read = self._read_interface(
                bus_name, MPRIS_PLAYER_INTERFACE, "player properties"
            )
            root_properties = root_read.values
            player_properties = dict(player_read.values)
            diagnostics.extend(root_read.diagnostics)
            diagnostics.extend(player_read.diagnostics)
            if "Position" not in player_properties:
                try:
                    player_properties["Position"] = self._backend.get_property(
                        bus_name, MPRIS_PLAYER_INTERFACE, "Position"
                    )
                except MprisServiceUnavailable:
                    raise
                except MprisBackendError as error:
                    position_diagnostic = f"Position: unavailable ({error})"
                    if not any(
                        item.startswith("Position: unavailable") for item in diagnostics
                    ):
                        diagnostics.append(position_diagnostic)
        except MprisServiceUnavailable as error:
            return _failure(
                bus_name,
                InspectionFailure.UNAVAILABLE,
                f"player disappeared during inspection ({error})",
            )
        except MprisBackendError as error:
            return _failure(bus_name, InspectionFailure.BUS_ERROR, str(error))

        if not root_properties and not player_properties:
            message = "; ".join(diagnostics) or "no readable MPRIS properties"
            return _failure(bus_name, InspectionFailure.BUS_ERROR, message)

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

    def _read_interface(
        self,
        bus_name: str,
        interface: str,
        label: str,
    ) -> MprisPropertyRead:
        """Keep one failed interface read from erasing another useful result."""

        try:
            return self._backend.read_properties(bus_name, interface)
        except MprisServiceUnavailable:
            raise
        except MprisBackendError as error:
            return MprisPropertyRead(diagnostics=(f"{label}: unavailable ({error})",))


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
            message = str(error)
            try:
                self.close()
            except MprisBackendError as cleanup_error:
                message = f"{message}; {cleanup_error}"
            return PlayerWatchStart(error=message)

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

        errors: list[str] = []
        for subscription in tuple(self._player_subscriptions.values()):
            try:
                subscription.close()
            except MprisBackendError as error:
                errors.append(str(error))
        self._player_subscriptions.clear()
        self._known_players.clear()
        if self._service_subscription is not None:
            try:
                self._service_subscription.close()
            except MprisBackendError as error:
                errors.append(str(error))
            self._service_subscription = None
        self._handler = None
        if errors:
            raise MprisBackendError("watcher cleanup failed: " + "; ".join(errors))

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
            try:
                subscription.close()
            except MprisBackendError as error:
                self._emit(
                    PlayerEvent(
                        PlayerEventKind.DIAGNOSTIC,
                        short_service_name(bus_name),
                        diagnostics=(f"could not detach player signals: {error}",),
                    )
                )
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
        decode_diagnostics: tuple[str, ...],
    ) -> None:
        if interface not in {MPRIS_ROOT_INTERFACE, MPRIS_PLAYER_INTERFACE}:
            return
        service_name = short_service_name(bus_name)
        if not changed and decode_diagnostics:
            self._emit(
                PlayerEvent(
                    PlayerEventKind.DIAGNOSTIC,
                    service_name,
                    diagnostics=decode_diagnostics,
                )
            )
            return

        specialized = {"PlaybackStatus", "Metadata"}
        if interface == MPRIS_PLAYER_INTERFACE and (
            "PlaybackStatus" in changed or "PlaybackStatus" in invalidated
        ):
            snapshot = map_player_snapshot(bus_name, {}, changed, decode_diagnostics)
            self._emit(
                PlayerEvent(
                    PlayerEventKind.PLAYBACK_STATUS_CHANGED,
                    service_name,
                    playback_status=snapshot.playback_status,
                    diagnostics=snapshot.diagnostics,
                )
            )
        if interface == MPRIS_PLAYER_INTERFACE and (
            "Metadata" in changed or "Metadata" in invalidated
        ):
            snapshot = map_player_snapshot(bus_name, {}, changed, decode_diagnostics)
            self._emit(
                PlayerEvent(
                    PlayerEventKind.METADATA_CHANGED,
                    service_name,
                    metadata=snapshot.metadata,
                    diagnostics=snapshot.diagnostics,
                )
            )

        remaining_changed = tuple(
            name
            for name in changed
            if interface == MPRIS_ROOT_INTERFACE or name not in specialized
        )
        remaining_invalidated = tuple(
            name
            for name in invalidated
            if interface == MPRIS_ROOT_INTERFACE or name not in specialized
        )
        if remaining_changed or remaining_invalidated:
            snapshot = map_player_snapshot(
                bus_name,
                changed if interface == MPRIS_ROOT_INTERFACE else {},
                changed if interface == MPRIS_PLAYER_INTERFACE else {},
                decode_diagnostics,
            )
            self._emit(
                PlayerEvent(
                    PlayerEventKind.PROPERTIES_CHANGED,
                    service_name,
                    snapshot=snapshot,
                    changed_properties=remaining_changed,
                    invalidated_properties=remaining_invalidated,
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
