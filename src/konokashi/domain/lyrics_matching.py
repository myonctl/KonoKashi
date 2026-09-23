"""Conservative provider-candidate matching for exact recording safety."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from konokashi.domain.lyrics import (
    LyricsMatchConfidence,
    LyricsProviderCandidate,
    LyricsQuery,
    RetrievalConfidence,
)
from konokashi.domain.normalization import (
    comparison_key,
    parse_artist_credits,
    parse_title_version,
)
from konokashi.domain.tracks import Confidence

_VERSION_MARKERS = (
    "radio edit",
    "radio version",
    "edit",
    "extended mix",
    "original",
    "original mix",
    "remix",
    "remaster",
    "remastered",
    "acoustic",
    "live",
    "instrumental",
    "karaoke",
    "demo",
    "vip",
    "cover",
    "club mix",
    "single version",
    "album version",
    "nightcore",
    "sped up",
    "speed up",
    "slowed",
    "slowed down",
    "english version",
    "japanese version",
    "korean version",
    "chinese version",
    "mandarin version",
    "cantonese version",
    "spanish version",
    "french version",
    "german version",
    "italian version",
    "portuguese version",
    "alternate language version",
)
_BASE_FALLBACK_QUALIFIERS = frozenset(
    {"radio edit", "radio version", "edit", "single version", "album version"}
)
_VERSION_TOKEN_CONNECTORS = frozenset({"and", "x"})


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
    retrieval_confidence: RetrievalConfidence = RetrievalConfidence.UNKNOWN
    recording_identity_confidence: Confidence | None = None


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
    album_version_corroborates = _album_version_corroborates(
        query.title,
        candidate.track_name,
        candidate.album_name,
    )
    markers_compatible = (
        query_versions == provider_versions or album_version_corroborates
    )
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
    qualifiers_conflict = (
        query_qualifier is not None
        and provider_qualifier is not None
        and query_qualifier != provider_qualifier
    )
    version_compatible = markers_compatible and not qualifiers_conflict
    safe_base_fallback = (
        base_equal
        and query_qualifier in _BASE_FALLBACK_QUALIFIERS
        and provider_qualifier is None
    )
    versioned_base_match = base_equal and album_version_corroborates
    if safe_base_fallback:
        evidence.append(
            f"base title matches; local qualifier {query_version.qualifier!r} retained"
        )
    if version_compatible and query_versions:
        evidence.append(
            "provider album corroborates source version qualifier"
            if album_version_corroborates
            else "recording version markers match"
        )
    elif safe_base_fallback:
        evidence.append(
            "provider title is unqualified; recording timing is evaluated separately"
        )
    if not safe_base_fallback and not markers_compatible:
        evidence.append("recording version markers conflict")
    if qualifiers_conflict:
        evidence.append("recording version qualifiers conflict")

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

    source_label_album_match = (
        query.album is None
        and bool(candidate.album_name)
        and title_equal
        and artist_high_eligible
        and duration_compatible
        and comparison_key(candidate.album_name or "")
        in {
            comparison_key(label)
            for label in query.source_labels
            if comparison_key(label)
        }
        and comparison_key(candidate.album_name or "")
        not in {*query_main, *query_contributors}
    )
    if source_label_album_match:
        evidence.append("provider album matches retained source label")

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
    elif versioned_base_match:
        title_relation = "base-title-version-corroborated"
    elif phonetic_match:
        title_relation = "phonetic-transliteration"
    else:
        title_relation = "different"

    if (
        artist_high_eligible
        and instrumental_compatible
        and (
            (title_equal and version_compatible)
            or safe_base_fallback
            or versioned_base_match
            or phonetic_match
        )
    ):
        text_confidence = LyricsMatchConfidence.HIGH
    elif (
        artist_correlated
        and instrumental_compatible
        and (
            (title_equal and version_compatible)
            or safe_base_fallback
            or versioned_base_match
        )
    ):
        text_confidence = LyricsMatchConfidence.MEDIUM
    else:
        text_confidence = LyricsMatchConfidence.LOW

    if (
        text_confidence is LyricsMatchConfidence.HIGH
        and album_compatible
        and (duration_compatible or (phonetic_match and duration_near))
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
        (title_equal or versioned_base_match)
        and artist_high_eligible
        and version_compatible
        and duration_compatible
        and instrumental_compatible
    ):
        confidence = LyricsMatchConfidence.HIGH
    elif (
        (title_equal or versioned_base_match)
        and artist_high_eligible
        and version_compatible
        and (duration_difference is None or duration_difference <= 5_000)
        and instrumental_compatible
    ):
        confidence = LyricsMatchConfidence.MEDIUM
    elif (
        text_confidence is LyricsMatchConfidence.HIGH
        and album_compatible
        and (safe_base_fallback or versioned_base_match or phonetic_match)
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
        recording_identity_confidence=next(
            (level for level in Confidence if level.value == query.source_confidence),
            None,
        ),
    )


def _version_markers(value: str) -> frozenset[str]:
    key = comparison_key(value)
    return frozenset(
        marker
        for marker in _VERSION_MARKERS
        if re.search(rf"\b{re.escape(marker)}\b", key)
    )


def _album_version_corroborates(
    query_title: str,
    provider_title: str,
    provider_album: str | None,
) -> bool:
    """Use a title-shaped provider album only as explicit version evidence.

    Some catalogues retain a recording qualifier in the release title while
    exposing only the base track title. Require the exact base, the same marker
    class, and substantial overlap between specific qualifier tokens. A generic
    ``remix`` or ``live`` label alone is never enough.
    """

    if not provider_album:
        return False
    query = parse_title_version(query_title)
    provider = parse_title_version(provider_title)
    album = parse_title_version(provider_album)
    if query.qualifier is None or provider.qualifier is not None:
        return False
    query_base = comparison_key(query.base_title)
    if not query_base or query_base != comparison_key(provider.base_title):
        return False
    if query_base != comparison_key(album.base_title) or album.qualifier is None:
        return False
    query_markers = _version_markers(query.qualifier)
    album_markers = _version_markers(album.qualifier)
    if not query_markers or query_markers != album_markers:
        return False
    query_tokens = _specific_version_tokens(query.qualifier, query_markers)
    album_tokens = _specific_version_tokens(album.qualifier, album_markers)
    if len(query_tokens) < 2 or len(album_tokens) < 2:
        return False
    overlap = query_tokens & album_tokens
    return (
        len(overlap) >= 2
        and len(overlap) / min(len(query_tokens), len(album_tokens)) >= 0.75
    )


def _specific_version_tokens(qualifier: str, markers: frozenset[str]) -> frozenset[str]:
    key = comparison_key(qualifier)
    for marker in sorted(markers, key=len, reverse=True):
        key = re.sub(rf"\b{re.escape(marker)}\b", " ", key)
    return frozenset(
        token
        for token in key.split()
        if token not in _VERSION_TOKEN_CONNECTORS and len(token) > 1
    )
