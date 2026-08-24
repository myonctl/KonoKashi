"""Defensive mapping from plain D-Bus values into domain-facing models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from lyriflux.domain.models import (
    PlayerCapabilities,
    PlayerSnapshot,
    RawTrackMetadata,
)

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_ROOT_INTERFACE = "org.mpris.MediaPlayer2"
MPRIS_PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
MPRIS_OBJECT_PATH = "/org/mpris/MediaPlayer2"


def full_service_name(service_name: str) -> str:
    """Accept CLI-friendly suffixes while preserving full bus names."""

    if service_name.startswith(MPRIS_PREFIX):
        return service_name
    return f"{MPRIS_PREFIX}{service_name}"


def short_service_name(bus_name: str) -> str:
    """Return the diagnostic name following the standard MPRIS prefix."""

    if bus_name.startswith(MPRIS_PREFIX):
        return bus_name[len(MPRIS_PREFIX) :]
    return bus_name


def _type_name(value: object) -> str:
    return type(value).__name__


def _string(
    values: Mapping[str, object], key: str, diagnostics: list[str]
) -> str | None:
    value = values.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    diagnostics.append(f"{key}: expected string, got {_type_name(value)}")
    return None


def _boolean(
    values: Mapping[str, object], key: str, diagnostics: list[str]
) -> bool | None:
    value = values.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    diagnostics.append(f"{key}: expected boolean, got {_type_name(value)}")
    return None


def _float(
    values: Mapping[str, object], key: str, diagnostics: list[str]
) -> float | None:
    value = values.get(key)
    if value is None:
        return None
    if isinstance(value, float):
        return value
    diagnostics.append(f"{key}: expected float, got {_type_name(value)}")
    return None


def _microseconds(
    values: Mapping[str, object], key: str, diagnostics: list[str]
) -> int | None:
    value = values.get(key)
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 0:
            diagnostics.append(f"{key}: negative microsecond value {value}")
        return value
    diagnostics.append(f"{key}: expected integer microseconds, got {_type_name(value)}")
    return None


def _strings(
    values: Mapping[str, object], key: str, diagnostics: list[str]
) -> tuple[str, ...] | None:
    value = values.get(key)
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, Sequence):
        diagnostics.append(f"{key}: expected string array, got {_type_name(value)}")
        return None
    if not all(isinstance(item, str) for item in value):
        diagnostics.append(f"{key}: string array contains a non-string value")
        return None
    return tuple(value)


def map_raw_track_metadata(
    metadata_value: object | None,
    diagnostics: list[str],
) -> RawTrackMetadata:
    """Map the recognized MPRIS metadata keys without normalization."""

    if metadata_value is None:
        return RawTrackMetadata()
    if not isinstance(metadata_value, Mapping):
        diagnostics.append(
            f"Metadata: expected mapping, got {_type_name(metadata_value)}"
        )
        return RawTrackMetadata()
    if not all(isinstance(key, str) for key in metadata_value):
        diagnostics.append("Metadata: contains a non-string key")
        return RawTrackMetadata()

    metadata: Mapping[str, object] = metadata_value
    return RawTrackMetadata(
        title=_string(metadata, "xesam:title", diagnostics),
        artists=_strings(metadata, "xesam:artist", diagnostics),
        album=_string(metadata, "xesam:album", diagnostics),
        url=_string(metadata, "xesam:url", diagnostics),
        duration_us=_microseconds(metadata, "mpris:length", diagnostics),
        art_url=_string(metadata, "mpris:artUrl", diagnostics),
        track_id=_string(metadata, "mpris:trackid", diagnostics),
    )


def map_player_snapshot(
    bus_name: str,
    root_properties: Mapping[str, object],
    player_properties: Mapping[str, object],
    extra_diagnostics: Sequence[str] = (),
) -> PlayerSnapshot:
    """Map root/player property dictionaries into a typed raw snapshot."""

    diagnostics = list(extra_diagnostics)
    playback_status = _string(player_properties, "PlaybackStatus", diagnostics)
    if playback_status not in {None, "Playing", "Paused", "Stopped"}:
        diagnostics.append(
            f"PlaybackStatus: unexpected value {playback_status!r}; preserved raw"
        )

    return PlayerSnapshot(
        service_name=short_service_name(bus_name),
        bus_name=bus_name,
        identity=_string(root_properties, "Identity", diagnostics),
        desktop_entry=_string(root_properties, "DesktopEntry", diagnostics),
        playback_status=playback_status,
        metadata=map_raw_track_metadata(player_properties.get("Metadata"), diagnostics),
        position_us=_microseconds(player_properties, "Position", diagnostics),
        capabilities=PlayerCapabilities(
            can_quit=_boolean(root_properties, "CanQuit", diagnostics),
            can_raise=_boolean(root_properties, "CanRaise", diagnostics),
            has_track_list=_boolean(root_properties, "HasTrackList", diagnostics),
            can_go_next=_boolean(player_properties, "CanGoNext", diagnostics),
            can_go_previous=_boolean(player_properties, "CanGoPrevious", diagnostics),
            can_play=_boolean(player_properties, "CanPlay", diagnostics),
            can_pause=_boolean(player_properties, "CanPause", diagnostics),
            can_seek=_boolean(player_properties, "CanSeek", diagnostics),
            can_control=_boolean(player_properties, "CanControl", diagnostics),
        ),
        loop_status=_string(player_properties, "LoopStatus", diagnostics),
        rate=_float(player_properties, "Rate", diagnostics),
        shuffle=_boolean(player_properties, "Shuffle", diagnostics),
        volume=_float(player_properties, "Volume", diagnostics),
        minimum_rate=_float(player_properties, "MinimumRate", diagnostics),
        maximum_rate=_float(player_properties, "MaximumRate", diagnostics),
        supported_uri_schemes=_strings(
            root_properties, "SupportedUriSchemes", diagnostics
        ),
        supported_mime_types=_strings(
            root_properties, "SupportedMimeTypes", diagnostics
        ),
        diagnostics=tuple(diagnostics),
    )
