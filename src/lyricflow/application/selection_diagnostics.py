"""Detailed terminal rendering for Stage 2 selection and resolution."""

from __future__ import annotations

from lyricflow.domain.identity import source_identity_value
from lyricflow.domain.tracks import PlayerAssessment, PlayerSelectionResult

UNAVAILABLE = "<unavailable>"


def _value(value: object | None) -> str:
    return UNAVAILABLE if value is None else str(value)


def _artists(artists: tuple[str, ...] | None) -> str:
    if artists is None:
        return UNAVAILABLE
    rendered = ", ".join(artist for artist in artists if artist.strip())
    return rendered or "<empty>"


def _assessment_lines(assessment: PlayerAssessment) -> list[str]:
    track = assessment.track
    snapshot = track.raw_snapshot
    raw = snapshot.metadata
    candidate = track.candidate
    identity = track.source_identity
    lines = [
        f"selected player: {snapshot.service_name}",
        f"playback status: {_value(snapshot.playback_status)}",
        f"raw title: {_value(raw.title)}",
        f"raw artist/uploader: {_artists(raw.artists)}",
        f"source kind: {identity.kind.value}",
        f"source identity: {source_identity_value(identity)}",
        f"identity persistence: {identity.persistence_scope.value}",
        f"resolved artist: {_artists(candidate.artists)}",
        f"resolved title: {_value(candidate.title)}",
        f"duration: {_value(candidate.duration_us)} us"
        if candidate.duration_us is not None
        else "duration: <unavailable>",
        f"confidence: {track.confidence.value}",
        "selection reasons:",
    ]
    lines.extend(f"  {reason}" for reason in assessment.reasons)
    lines.append("resolution evidence:")
    lines.extend(f"  - {evidence}" for evidence in track.evidence)
    lines.extend(
        f"  - {evidence}"
        for evidence in candidate.evidence
        if evidence not in track.evidence
    )
    lines.append("transformations:")
    lines.extend(
        (f"  - {transformation}" for transformation in candidate.transformations)
        if candidate.transformations
        else ("  - none",)
    )
    lines.append("warnings:")
    lines.extend(
        (f"  - {warning}" for warning in track.warnings)
        if track.warnings
        else ("  - none",)
    )
    return lines


def render_player_selection(result: PlayerSelectionResult) -> str:
    """Render winner, independent alternatives, duplicates, and uncertainty."""

    if result.selected is None:
        lines = ["No selectable MPRIS track found."]
    else:
        lines = ["LyricFlow current-track resolution"]
        lines.extend(_assessment_lines(result.selected))

    if result.alternatives:
        lines.append("independent alternatives:")
        for alternative in result.alternatives:
            snapshot = alternative.track.raw_snapshot
            candidate = alternative.track.candidate
            lines.append(
                f"  - {snapshot.service_name}: {snapshot.playback_status}; "
                f"{_artists(candidate.artists)} - {_value(candidate.title)}; "
                f"confidence={alternative.track.confidence.value}"
            )
    if result.suppressed:
        lines.append("suppressed duplicates:")
        for suppressed in result.suppressed:
            service = suppressed.assessment.track.raw_snapshot.service_name
            lines.append(
                f"  - {service}: {suppressed.reason}; "
                f"winner={suppressed.winner_service_name}"
            )
    if result.unavailable_diagnostics:
        lines.append("unavailable/ignored player diagnostics:")
        lines.extend(f"  - {item}" for item in result.unavailable_diagnostics)
    if result.warnings:
        lines.append("selection warnings:")
        lines.extend(f"  - {warning}" for warning in result.warnings)
    return "\n".join(lines)
