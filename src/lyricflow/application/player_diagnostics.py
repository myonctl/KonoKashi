"""Terminal rendering policy for typed MPRIS snapshots and events."""

from __future__ import annotations

from lyricflow.domain.models import (
    PlayerEvent,
    PlayerEventKind,
    PlayerInspection,
    PlayerListResult,
    PlayerSnapshot,
    RawTrackMetadata,
)

UNAVAILABLE = "<unavailable>"


def _value(value: object | None) -> str:
    return UNAVAILABLE if value is None else str(value)


def _artists(artists: tuple[str, ...] | None) -> str:
    if artists is None:
        return UNAVAILABLE
    if not artists:
        return "<empty>"
    return ", ".join(artists)


def _microseconds(value: int | None) -> str:
    return UNAVAILABLE if value is None else f"{value} us"


def _summary_lines(snapshot: PlayerSnapshot) -> list[str]:
    metadata = snapshot.metadata
    return [
        f"  identity: {_value(snapshot.identity)}",
        f"  playback status: {_value(snapshot.playback_status)}",
        f"  title: {_value(metadata.title)}",
        f"  artists: {_artists(metadata.artists)}",
        f"  album: {_value(metadata.album)}",
        f"  URL: {_value(metadata.url)}",
        f"  duration: {_microseconds(metadata.duration_us)}",
        f"  position: {_microseconds(snapshot.position_us)}",
    ]


def render_player_list(result: PlayerListResult) -> str:
    """Render every discovery result without selecting or suppressing players."""

    if result.error is not None:
        return f"Unable to enumerate MPRIS players: {result.error}"
    if not result.players:
        return "No MPRIS players found."

    lines = [f"MPRIS players: {len(result.players)}"]
    for inspection in result.players:
        lines.append("")
        lines.append(inspection.service_name)
        if inspection.snapshot is None:
            lines.append(f"  unavailable: {_value(inspection.message)}")
            continue
        lines.extend(_summary_lines(inspection.snapshot))
        lines.extend(
            f"  diagnostic: {diagnostic}"
            for diagnostic in inspection.snapshot.diagnostics
        )
    return "\n".join(lines)


def _capability_lines(snapshot: PlayerSnapshot) -> list[str]:
    capabilities = snapshot.capabilities
    values = (
        ("can quit", capabilities.can_quit),
        ("can raise", capabilities.can_raise),
        ("has track list", capabilities.has_track_list),
        ("can go next", capabilities.can_go_next),
        ("can go previous", capabilities.can_go_previous),
        ("can play", capabilities.can_play),
        ("can pause", capabilities.can_pause),
        ("can seek", capabilities.can_seek),
        ("can control", capabilities.can_control),
    )
    return [f"  {name}: {_value(value)}" for name, value in values]


def render_player_inspection(inspection: PlayerInspection) -> str:
    """Render a raw-enough but provider-neutral view of one player."""

    lines = ["MPRIS player inspection", f"service: {inspection.service_name}"]
    if inspection.snapshot is None:
        failure = inspection.failure.value if inspection.failure else None
        lines.extend(
            (
                f"bus name: {inspection.bus_name}",
                f"status: {_value(failure)}",
                f"error: {_value(inspection.message)}",
            )
        )
        return "\n".join(lines)

    snapshot = inspection.snapshot
    metadata = snapshot.metadata
    lines.extend(
        (
            f"bus name: {snapshot.bus_name}",
            f"identity: {_value(snapshot.identity)}",
            f"desktop entry: {_value(snapshot.desktop_entry)}",
            f"playback status: {_value(snapshot.playback_status)}",
            f"loop status: {_value(snapshot.loop_status)}",
            f"rate: {_value(snapshot.rate)}",
            f"shuffle: {_value(snapshot.shuffle)}",
            f"volume: {_value(snapshot.volume)}",
            f"minimum rate: {_value(snapshot.minimum_rate)}",
            f"maximum rate: {_value(snapshot.maximum_rate)}",
            f"position: {_microseconds(snapshot.position_us)}",
            f"title: {_value(metadata.title)}",
            f"artists: {_artists(metadata.artists)}",
            f"album: {_value(metadata.album)}",
            f"URL: {_value(metadata.url)}",
            f"duration: {_microseconds(metadata.duration_us)}",
            f"art URL: {_value(metadata.art_url)}",
            f"track ID: {_value(metadata.track_id)}",
            "capabilities:",
        )
    )
    lines.extend(_capability_lines(snapshot))
    lines.extend(
        (
            f"supported URI schemes: {_artists(snapshot.supported_uri_schemes)}",
            f"supported MIME types: {_artists(snapshot.supported_mime_types)}",
        )
    )
    if snapshot.diagnostics:
        lines.append("diagnostics:")
        lines.extend(f"  - {diagnostic}" for diagnostic in snapshot.diagnostics)
    return "\n".join(lines)


def _metadata_event_details(metadata: RawTrackMetadata | None) -> str:
    if metadata is None:
        return "metadata unavailable"
    return f"title={_value(metadata.title)}; artists={_artists(metadata.artists)}"


def render_player_event(event: PlayerEvent) -> str:
    """Render one meaningful watch event on a single terminal line."""

    prefix = f"[{event.kind.value}] {event.service_name}"
    if event.kind is PlayerEventKind.PLAYBACK_STATUS_CHANGED:
        detail = _value(event.playback_status)
    elif event.kind is PlayerEventKind.METADATA_CHANGED:
        detail = _metadata_event_details(event.metadata)
    elif event.kind is PlayerEventKind.SEEKED:
        detail = f"position={_microseconds(event.position_us)}"
    elif event.diagnostics:
        detail = "; ".join(event.diagnostics)
    else:
        return prefix
    if event.diagnostics and event.kind not in {
        PlayerEventKind.DIAGNOSTIC,
        PlayerEventKind.PLAYER_APPEARED,
        PlayerEventKind.PLAYER_DISAPPEARED,
    }:
        detail = f"{detail}; diagnostics={'; '.join(event.diagnostics)}"
    return f"{prefix}: {detail}"
