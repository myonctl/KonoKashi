"""Deterministic player selection and conservative duplicate suppression."""

from __future__ import annotations

from dataclasses import replace

from konokashi.application.player_selectors import player_selector_matches
from konokashi.application.resolve_track import TrackResolver
from konokashi.domain.identity import (
    GenericMprisIdentity,
    LocalFileIdentity,
    YouTubeIdentity,
)
from konokashi.domain.models import PlayerInspection, PlayerListResult, PlayerSnapshot
from konokashi.domain.normalization import comparison_key
from konokashi.domain.tracks import (
    PlayerAssessment,
    PlayerSelectionConfig,
    PlayerSelectionResult,
    SuppressedPlayer,
    semantic_duration_us,
)

_PLAYBACK_RANK = {"Playing": 3, "Paused": 2, "Stopped": 1}


def _preference(snapshot: PlayerSnapshot, configured: tuple[str, ...]) -> int:
    for index, configured_name in enumerate(configured):
        if player_selector_matches(snapshot, configured_name):
            return len(configured) - index
    return 0


def _quality(snapshot: PlayerSnapshot) -> tuple[int, tuple[str, ...]]:
    metadata = snapshot.metadata
    evidence = (
        (metadata.title is not None and bool(metadata.title.strip()), "usable title"),
        (
            any(artist.strip() for artist in metadata.artists or ()),
            "usable artist",
        ),
        (semantic_duration_us(metadata.duration_us) is not None, "usable duration"),
        (
            snapshot.position_us is not None and snapshot.position_us >= 0,
            "usable position",
        ),
        (metadata.url is not None and bool(metadata.url.strip()), "media URL"),
    )
    reasons = tuple(f"+ {label}" for available, label in evidence if available)
    return sum(available for available, _label in evidence), reasons


def _assessment(
    inspection: PlayerInspection,
    resolver: TrackResolver,
    config: PlayerSelectionConfig,
) -> PlayerAssessment:
    snapshot = inspection.snapshot
    if snapshot is None:
        raise ValueError("cannot assess an unavailable player")
    status_rank = _PLAYBACK_RANK.get(snapshot.playback_status or "", 0)
    preference = _preference(snapshot, config.preferred_players)
    quality, quality_reasons = _quality(snapshot)
    if snapshot.playback_status == "Playing":
        status_reason = "+ currently playing"
    elif snapshot.playback_status == "Paused":
        status_reason = "+ paused with a current track"
    elif snapshot.playback_status == "Stopped":
        status_reason = "+ stopped player"
    else:
        status_reason = "+ playback state unavailable"
    reasons = [status_reason]
    if preference:
        reasons.append("+ configured preferred player")
    reasons.extend(quality_reasons)
    reasons.append(
        "policy order: playback state, configured preference, metadata quality, "
        "service name"
    )
    return PlayerAssessment(
        track=resolver.resolve(snapshot),
        reasons=tuple(reasons),
        rank=(status_rank, int(preference > 0), preference, quality),
    )


def _compatible_duration(left: int | None, right: int | None) -> bool:
    if left is None or right is None:
        return True
    tolerance = max(2_000_000, int(max(left, right) * 0.02))
    return abs(left - right) <= tolerance


def _duplicate(left: PlayerAssessment, right: PlayerAssessment) -> bool:
    left_identity = left.track.source_identity
    right_identity = right.track.source_identity
    if isinstance(left_identity, YouTubeIdentity) and isinstance(
        right_identity, YouTubeIdentity
    ):
        return left_identity.video_id == right_identity.video_id
    if isinstance(left_identity, LocalFileIdentity) and isinstance(
        right_identity, LocalFileIdentity
    ):
        return left_identity.canonical_path == right_identity.canonical_path
    if not (
        isinstance(left_identity, GenericMprisIdentity)
        and isinstance(right_identity, GenericMprisIdentity)
    ):
        return False
    if (
        not left_identity.media_url
        or left_identity.media_url != right_identity.media_url
    ):
        return False
    left_title = left.track.candidate.title
    right_title = right.track.candidate.title
    if not left_title or not right_title:
        return False
    if comparison_key(left_title) != comparison_key(right_title):
        return False
    return _compatible_duration(
        left.track.candidate.duration_us,
        right.track.candidate.duration_us,
    )


def _winner(assessments: list[PlayerAssessment]) -> PlayerAssessment:
    highest_rank = max(assessment.rank for assessment in assessments)
    tied = [assessment for assessment in assessments if assessment.rank == highest_rank]
    return min(tied, key=lambda item: item.track.raw_snapshot.service_name.casefold())


class PlayerSelectionService:
    """Select one primary player while retaining independent and duplicate peers."""

    def __init__(self, resolver: TrackResolver) -> None:
        self._resolver = resolver

    def select(
        self,
        result: PlayerListResult,
        config: PlayerSelectionConfig | None = None,
        *,
        player_override: str | None = None,
    ) -> PlayerSelectionResult:
        """Apply one temporary override, then durable and automatic policy."""

        config = config or PlayerSelectionConfig()
        override = None if player_override is None else player_override.strip()
        if player_override is not None and not override:
            return PlayerSelectionResult(
                unavailable_diagnostics=("temporary player override is empty",)
            )
        if override is not None:
            matching = tuple(
                inspection
                for inspection in result.players
                if inspection.snapshot is not None
                and player_selector_matches(inspection.snapshot, override)
            )
            if not matching:
                return PlayerSelectionResult(
                    unavailable_diagnostics=(
                        f"temporary player override {override!r} did not match any "
                        "available MPRIS player",
                    ),
                    warnings=((result.error,) if result.error else ()),
                )
            result = PlayerListResult(matching, result.error)
            # An explicit per-process choice outranks both durable preference and
            # durable ignore lists without changing either persisted value.
            config = PlayerSelectionConfig(preferred_players=(override,))
        unavailable: list[str] = []
        assessments: list[PlayerAssessment] = []
        for inspection in result.players:
            snapshot = inspection.snapshot
            if snapshot is None:
                unavailable.append(
                    f"{inspection.service_name}: {inspection.message or 'unavailable'}"
                )
                continue
            if any(
                player_selector_matches(snapshot, ignored)
                for ignored in config.ignored_players
            ):
                unavailable.append(
                    f"{inspection.service_name}: ignored by player configuration"
                )
                continue
            assessments.append(_assessment(inspection, self._resolver, config))

        groups: list[list[PlayerAssessment]] = []
        for assessment in assessments:
            matching_groups = [
                group
                for group in groups
                if any(_duplicate(assessment, member) for member in group)
            ]
            if not matching_groups:
                groups.append([assessment])
                continue
            first = matching_groups[0]
            first.append(assessment)
            for extra in matching_groups[1:]:
                first.extend(extra)
                groups.remove(extra)

        retained: list[PlayerAssessment] = []
        suppressed: list[SuppressedPlayer] = []
        for group in groups:
            winner = _winner(group)
            if len(group) > 1:
                winner = replace(
                    winner,
                    reasons=(
                        *winner.reasons,
                        "+ same source as weaker duplicate representation",
                    ),
                )
            retained.append(winner)
            for assessment in group:
                if assessment.track.raw_snapshot.service_name == (
                    winner.track.raw_snapshot.service_name
                ):
                    continue
                suppressed.append(
                    SuppressedPlayer(
                        assessment,
                        winner.track.raw_snapshot.service_name,
                        "duplicate source with lower selection rank/metadata quality",
                    )
                )

        if not retained:
            unavailable_warnings = (result.error,) if result.error else ()
            return PlayerSelectionResult(
                unavailable_diagnostics=tuple(unavailable),
                warnings=unavailable_warnings,
            )

        selected = _winner(retained)
        alternatives = tuple(
            sorted(
                (item for item in retained if item is not selected),
                key=lambda item: item.track.raw_snapshot.service_name.casefold(),
            )
        )
        warnings: list[str] = []
        playing = [
            item
            for item in retained
            if item.track.raw_snapshot.playback_status == "Playing"
        ]
        if len(playing) > 1:
            warnings.append(
                "multiple independent players are playing; deterministic policy "
                "selected "
                f"{selected.track.raw_snapshot.service_name}"
            )
        if any(item.rank == selected.rank for item in alternatives):
            warnings.append(
                "selection rank tied; case-insensitive service name was the final "
                "tie-break"
            )
        result_warnings = list(warnings)
        if override is not None:
            result_warnings.insert(0, f"temporary player override active: {override}")
        return PlayerSelectionResult(
            selected=selected,
            alternatives=alternatives,
            suppressed=tuple(
                sorted(
                    suppressed,
                    key=lambda item: (
                        item.assessment.track.raw_snapshot.service_name.casefold()
                    ),
                )
            ),
            unavailable_diagnostics=tuple(unavailable),
            warnings=tuple(result_warnings),
        )
