"""Conservative provider-candidate matching for exact recording safety."""

from __future__ import annotations

import re
from dataclasses import dataclass

from lyricflow.domain.lyrics import (
    LyricsMatchConfidence,
    LyricsProviderCandidate,
    LyricsQuery,
)
from lyricflow.domain.normalization import comparison_key

_VERSION_MARKERS = (
    "radio edit",
    "extended mix",
    "extended",
    "remix",
    "acoustic",
    "live",
    "instrumental",
    "vip",
    "nightcore",
)


@dataclass(frozen=True, slots=True)
class CandidateMatchAssessment:
    """One candidate's confidence and human-readable evidence."""

    candidate: LyricsProviderCandidate
    confidence: LyricsMatchConfidence
    evidence: tuple[str, ...]
    duration_difference_ms: int | None


def assess_candidate(
    query: LyricsQuery, candidate: LyricsProviderCandidate
) -> CandidateMatchAssessment:
    """Require strong title/artist evidence and compatible duration for High."""

    evidence: list[str] = []
    query_title = comparison_key(query.title)
    provider_title = comparison_key(candidate.track_name)
    title_equal = bool(query_title) and query_title == provider_title
    if title_equal:
        evidence.append("normalized title matches")
    else:
        evidence.append("normalized title differs")

    query_artist = comparison_key(query.artist_name)
    provider_artist = comparison_key(candidate.artist_name)
    artist_equal = bool(query_artist) and query_artist == provider_artist
    if artist_equal:
        evidence.append("normalized musical artist matches")
    else:
        evidence.append("normalized musical artist differs")

    query_versions = _version_markers(query.title)
    provider_versions = _version_markers(candidate.track_name)
    version_compatible = query_versions == provider_versions
    if version_compatible and query_versions:
        evidence.append("recording version markers match")
    elif not version_compatible:
        evidence.append("recording version markers conflict")

    duration_difference: int | None = None
    duration_compatible = False
    if query.duration_ms is not None and candidate.duration_ms is not None:
        duration_difference = abs(query.duration_ms - candidate.duration_ms)
        evidence.append(f"duration differs by {duration_difference} ms")
        duration_compatible = duration_difference <= 2_000
    else:
        evidence.append("duration comparison is unavailable")

    album_compatible = True
    if query.album and candidate.album_name:
        album_compatible = comparison_key(query.album) == comparison_key(
            candidate.album_name
        )
        evidence.append(
            "normalized album matches"
            if album_compatible
            else "normalized album differs"
        )

    if (
        title_equal
        and artist_equal
        and version_compatible
        and duration_compatible
        and album_compatible
    ):
        confidence = LyricsMatchConfidence.HIGH
    elif (
        title_equal
        and artist_equal
        and version_compatible
        and (duration_difference is None or duration_difference <= 5_000)
    ):
        confidence = LyricsMatchConfidence.MEDIUM
    else:
        confidence = LyricsMatchConfidence.LOW
    return CandidateMatchAssessment(
        candidate, confidence, tuple(evidence), duration_difference
    )


def _version_markers(value: str) -> frozenset[str]:
    key = comparison_key(value)
    return frozenset(
        marker
        for marker in _VERSION_MARKERS
        if re.search(rf"\b{re.escape(marker)}\b", key)
    )
