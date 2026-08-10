"""Provider-neutral models for raw MPRIS observations and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True, slots=True)
class RawTrackMetadata:
    """Track metadata exactly as exposed by well-typed MPRIS fields."""

    title: str | None = None
    artists: tuple[str, ...] | None = None
    album: str | None = None
    url: str | None = None
    duration_us: int | None = None
    art_url: str | None = None
    track_id: str | None = None


@dataclass(frozen=True, slots=True)
class PlayerCapabilities:
    """Optional MPRIS root and player capability properties."""

    can_quit: bool | None = None
    can_raise: bool | None = None
    has_track_list: bool | None = None
    can_go_next: bool | None = None
    can_go_previous: bool | None = None
    can_play: bool | None = None
    can_pause: bool | None = None
    can_seek: bool | None = None
    can_control: bool | None = None


@dataclass(frozen=True, slots=True)
class PlayerSnapshot:
    """Typed raw state read from one MPRIS service."""

    service_name: str
    bus_name: str
    identity: str | None
    desktop_entry: str | None
    playback_status: str | None
    metadata: RawTrackMetadata
    position_us: int | None
    capabilities: PlayerCapabilities
    loop_status: str | None = None
    rate: float | None = None
    shuffle: bool | None = None
    volume: float | None = None
    minimum_rate: float | None = None
    maximum_rate: float | None = None
    supported_uri_schemes: tuple[str, ...] | None = None
    supported_mime_types: tuple[str, ...] | None = None
    diagnostics: tuple[str, ...] = field(default_factory=tuple)


class InspectionFailure(Enum):
    """Expected failure categories at the player inspection boundary."""

    UNAVAILABLE = "unavailable"
    BUS_ERROR = "bus-error"


@dataclass(frozen=True, slots=True)
class PlayerInspection:
    """Success or expected failure while reading one player."""

    service_name: str
    bus_name: str
    snapshot: PlayerSnapshot | None = None
    failure: InspectionFailure | None = None
    message: str | None = None

    @property
    def succeeded(self) -> bool:
        """Return whether a complete typed snapshot is available."""

        return self.snapshot is not None and self.failure is None


@dataclass(frozen=True, slots=True)
class PlayerListResult:
    """Result of enumerating and inspecting every current MPRIS service."""

    players: tuple[PlayerInspection, ...] = field(default_factory=tuple)
    error: str | None = None


class PlayerEventKind(Enum):
    """Meaningful events emitted by the Stage 1 MPRIS monitor."""

    PLAYER_APPEARED = "player appeared"
    PLAYER_DISAPPEARED = "player disappeared"
    PLAYBACK_STATUS_CHANGED = "playback status changed"
    METADATA_CHANGED = "metadata changed"
    SEEKED = "seek occurred"
    DIAGNOSTIC = "diagnostic"


@dataclass(frozen=True, slots=True)
class PlayerEvent:
    """Provider-neutral event produced from MPRIS D-Bus signals."""

    kind: PlayerEventKind
    service_name: str
    playback_status: str | None = None
    metadata: RawTrackMetadata | None = None
    position_us: int | None = None
    diagnostics: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PlayerWatchStart:
    """Result of attaching signal watchers and reading the initial services."""

    current_services: tuple[str, ...] = field(default_factory=tuple)
    error: str | None = None
