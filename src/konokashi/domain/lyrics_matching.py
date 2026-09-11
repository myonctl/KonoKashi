"""Conservative provider-candidate matching for exact recording safety."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from konokashi.domain.lyrics import (
    LyricsMatchConfidence,
    LyricsProviderCandidate,
    LyricsQuery,
)
from konokashi.domain.normalization import (
    comparison_key,
    parse_artist_credits,
    parse_title_version,
)

_VERSION_MARKERS = (
    "radio edit",
    "radio version",
    "edit",
    "extended mix",
    "original mix",
    "remix",
    "remaster",
    "remastered",
    "acoustic",
    "live",
    "instrumental",
    "demo",
    "vip",
    "cover",
    "club mix",
    "single version",
    "album version",
    "nightcore",
)
_BASE_FALLBACK_QUALIFIERS = frozenset(
    {"radio edit", "radio version", "edit", "single version", "album version"}
)


@dataclass(frozen=True, slots=True)
class CandidateMatchAssessment:
    """One candidate's confidence and human-readable evidence."""

    candidate: LyricsProviderCandidate
    confidence: LyricsMatchConfidence
    evidence: tuple[str, ...]
    duration_difference_ms: int | None
    title_relation: str
    text_confidence: LyricsMatchConfidence
    timing_confidence: LyricsMatchConfidence
    query_strategy: str = "resolved-track"


def assess_candidate(
    query: LyricsQuery,
    candidate: LyricsProviderCandidate,
    *,
    provider_title_aliases: tuple[str, ...] = (),
) -> CandidateMatchAssessment:
    """Assess lyric text and recording timing as independent identities."""

    evidence: list[str] = []
    query_version = parse_title_version(query.title)
    provider_version = parse_title_version(candidate.track_name)
    query_title = comparison_key(query_version.raw_title)
    provider_title = comparison_key(provider_version.raw_title)
    title_equal = bool(query_title) and query_title == provider_title
    base_equal = bool(comparison_key(query_version.base_title)) and comparison_key(
        query_version.base_title
    ) == comparison_key(provider_version.base_title)
    if candidate.synced_lyrics:
        evidence.append("synchronized lyrics are available")
    elif candidate.plain_lyrics:
        evidence.append("only plain lyrics are available")
    if query.source_confidence is not None:
        evidence.append(f"recording metadata confidence is {query.source_confidence}")

    query_credit = parse_artist_credits(query.artists)
    if query.main_artists:
        query_credit = type(query_credit)(query.main_artists, query.contributors)
    provider_credit = parse_artist_credits((candidate.artist_name,))
    query_main = tuple(comparison_key(item) for item in query_credit.main_artists)
    provider_main = tuple(comparison_key(item) for item in provider_credit.main_artists)
    main_order_equal = bool(query_main) and query_main == provider_main
    main_set_equal = (
        bool(query_main)
        and len(query_main) == len(provider_main)
        and set(query_main) == set(provider_main)
    )
    query_contributors = tuple(
        comparison_key(item) for item in query_credit.contributors
    )
    provider_contributors = tuple(
        comparison_key(item) for item in provider_credit.contributors
    )
    contributor_order_equal = query_contributors == provider_contributors
    contributor_set_equal = (
        bool(query_contributors)
        and len(query_contributors) == len(provider_contributors)
        and set(query_contributors) == set(provider_contributors)
    )
    provider_omits_contributors = bool(query_contributors) and not provider_contributors
    artist_high_eligible = main_order_equal and (
        contributor_order_equal or provider_omits_contributors
    )
    artist_correlated = main_order_equal or main_set_equal
    if main_order_equal:
        evidence.append("ordered main-artist credits match")
    elif main_set_equal:
        evidence.append("complete main-artist set matches but order differs")
    else:
        evidence.append("main-artist credits differ or are incomplete")
    if contributor_order_equal and query_contributors:
        evidence.append("ordered contributor credits match")
    elif contributor_set_equal:
        evidence.append("complete contributor set matches but order differs")
        artist_high_eligible = False
    elif provider_omits_contributors:
        evidence.append(
            "provider omits reported contributor credit; main artist remains usable"
        )
    elif query_contributors or provider_contributors:
        evidence.append("contributor credits differ or are incomplete")
        artist_high_eligible = False
    if title_equal:
        evidence.append("normalized title matches")
    else:
        evidence.append("normalized title differs")

    query_versions = _version_markers(query.title)
    provider_versions = _version_markers(candidate.track_name)
    version_compatible = query_versions == provider_versions
    duration_difference: int | None = None
    duration_compatible = False
    if query.duration_ms is not None and candidate.duration_ms is not None:
        duration_difference = abs(query.duration_ms - candidate.duration_ms)
        evidence.append(f"duration differs by {duration_difference} ms")
        duration_compatible = duration_difference <= 2_000
    elif query.duration_ms is not None and candidate.provider_duration_matched:
        duration_difference = 0
        duration_compatible = True
        evidence.append("provider confirmed its bounded recording-duration filter")
    else:
        evidence.append("duration comparison is unavailable")
    duration_near = duration_difference is not None and duration_difference <= 5_000

    query_qualifier = (
        None
        if query_version.qualifier is None
        else comparison_key(query_version.qualifier)
    )
    provider_qualifier = (
        None
        if provider_version.qualifier is None
        else comparison_key(provider_version.qualifier)
    )
    safe_base_fallback = (
        base_equal
        and query_qualifier in _BASE_FALLBACK_QUALIFIERS
        and provider_qualifier is None
    )
    if safe_base_fallback:
        evidence.append(
            f"base title matches; local qualifier {query_version.qualifier!r} retained"
        )
    if version_compatible and query_versions:
        evidence.append("recording version markers match")
    elif safe_base_fallback:
        evidence.append(
            "provider title is unqualified; recording timing is evaluated separately"
        )
    elif not version_compatible:
        evidence.append("recording version markers conflict")

    phonetic_similarity = max(
        (
            SequenceMatcher(
                None,
                comparison_key(query_version.base_title).replace(" ", ""),
                comparison_key(alias).replace(" ", ""),
            ).ratio()
            for alias in provider_title_aliases
            if comparison_key(alias)
        ),
        default=0.0,
    )
    phonetic_match = (
        not base_equal
        and artist_high_eligible
        and duration_near
        and version_compatible
        and phonetic_similarity >= 0.72
    )
    if phonetic_match:
        evidence.append(
            "offline transliteration supports title similarity "
            f"{phonetic_similarity:.2f}"
        )

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

    instrumental_compatible = (
        not candidate.instrumental or query_qualifier == "instrumental" or title_equal
    )
    if not instrumental_compatible:
        evidence.append("instrumental candidate cannot inherit a vocal-track match")

    if candidate.track_name == query.title:
        title_relation = "exact-raw"
    elif title_equal:
        title_relation = "normalized"
    elif safe_base_fallback:
        title_relation = "base-title"
    elif phonetic_match:
        title_relation = "phonetic-transliteration"
    else:
        title_relation = "different"

    if (
        artist_high_eligible
        and instrumental_compatible
        and (
            (title_equal and version_compatible) or safe_base_fallback or phonetic_match
        )
    ):
        text_confidence = LyricsMatchConfidence.HIGH
    elif (
        artist_correlated
        and instrumental_compatible
        and ((title_equal and version_compatible) or safe_base_fallback)
    ):
        text_confidence = LyricsMatchConfidence.MEDIUM
    else:
        text_confidence = LyricsMatchConfidence.LOW

    if text_confidence is LyricsMatchConfidence.HIGH and (
        duration_compatible or (phonetic_match and duration_near)
    ):
        timing_confidence = LyricsMatchConfidence.HIGH
    elif (
        text_confidence is LyricsMatchConfidence.HIGH
        and duration_difference is None
        and title_equal
        and version_compatible
    ):
        timing_confidence = LyricsMatchConfidence.MEDIUM
    else:
        timing_confidence = LyricsMatchConfidence.LOW

    if (
        title_equal
        and artist_high_eligible
        and version_compatible
        and duration_compatible
        and album_compatible
        and instrumental_compatible
    ):
        confidence = LyricsMatchConfidence.HIGH
    elif (
        title_equal
        and artist_high_eligible
        and version_compatible
        and (duration_difference is None or duration_difference <= 5_000)
        and instrumental_compatible
    ):
        confidence = LyricsMatchConfidence.MEDIUM
    elif (
        text_confidence is LyricsMatchConfidence.HIGH
        and album_compatible
        and (safe_base_fallback or phonetic_match)
    ):
        confidence = LyricsMatchConfidence.HIGH
    else:
        confidence = LyricsMatchConfidence.LOW
    if (
        confidence is LyricsMatchConfidence.LOW
        and text_confidence is LyricsMatchConfidence.MEDIUM
        and (duration_difference is None or duration_near)
    ):
        confidence = LyricsMatchConfidence.MEDIUM
    if query.source_confidence == "Low" and confidence is LyricsMatchConfidence.HIGH:
        evidence.append("low-confidence recording metadata prevents automatic match")
        confidence = LyricsMatchConfidence.MEDIUM
    if candidate.provider_confidence is not None:
        evidence.append(
            "provider reports "
            f"{candidate.provider_confidence.value} community confidence"
        )
        if (
            candidate.provider_confidence is LyricsMatchConfidence.LOW
            and confidence is LyricsMatchConfidence.HIGH
        ):
            evidence.append(
                "low provider confidence prevents automatic match without review"
            )
            confidence = LyricsMatchConfidence.MEDIUM
    return CandidateMatchAssessment(
        candidate,
        confidence,
        tuple(evidence),
        duration_difference,
        title_relation,
        text_confidence,
        timing_confidence,
        query.strategy,
    )


def _version_markers(value: str) -> frozenset[str]:
    key = comparison_key(value)
    return frozenset(
        marker
        for marker in _VERSION_MARKERS
        if re.search(rf"\b{re.escape(marker)}\b", key)
    )
