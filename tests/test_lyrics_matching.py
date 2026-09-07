"""Provider match confidence and recording-version safety regressions."""

from __future__ import annotations

import pytest

from konokashi.domain.lyrics import (
    LyricsMatchConfidence,
    LyricsProviderCandidate,
    LyricsQuery,
)
from konokashi.domain.lyrics_matching import assess_candidate


def _candidate(
    *,
    title: str = "Elevate (Radio Edit)",
    artist: str = "Little Sis Nora & S3RL",
    album: str | None = "Elevate",
    duration_ms: int | None = 183_771,
) -> LyricsProviderCandidate:
    return LyricsProviderCandidate(
        "LRCLIB",
        "1",
        title,
        artist,
        album,
        duration_ms,
        False,
        "plain",
        None,
    )


@pytest.mark.parametrize(
    ("title", "artist"),
    [
        ("Elevate (Radio Edit)", "Little Sis Nora & S3RL"),
        ("elevate [radio edit]", "little sis nora and s3rl"),
        ("Elevate \N{EN DASH} Radio Edit", "Little Sis Nora & S3RL"),
    ],
)
def test_case_punctuation_and_dash_variants_match(title: str, artist: str) -> None:
    query = LyricsQuery(title, (artist,), "Elevate", 183_771)

    assert assess_candidate(query, _candidate()).confidence is (
        LyricsMatchConfidence.HIGH
    )


def test_feat_notation_normalizes_without_using_uploader_as_artist() -> None:
    query = LyricsQuery("Every Single Day", ("S3RL feat. JessKah",), None, 180_000)
    candidate = _candidate(
        title="Every Single Day",
        artist="S3RL ft. JessKah",
        album=None,
        duration_ms=180_400,
    )

    assert assess_candidate(query, candidate).confidence is LyricsMatchConfidence.HIGH
    uploader = _candidate(
        title="Every Single Day",
        artist="HappyCore Raver",
        album=None,
        duration_ms=180_400,
    )
    assert assess_candidate(query, uploader).confidence is LyricsMatchConfidence.LOW


@pytest.mark.parametrize(
    "provider_title",
    [
        "Elevate",
        "Elevate (Live)",
        "Elevate (Extended Mix)",
        "Elevate (Remix)",
        "Elevate (Nightcore)",
    ],
)
def test_version_marker_mismatch_is_low(provider_title: str) -> None:
    query = LyricsQuery(
        "Elevate (Radio Edit)", ("Little Sis Nora & S3RL",), "Elevate", 183_771
    )

    assessment = assess_candidate(query, _candidate(title=provider_title))

    assert assessment.confidence is LyricsMatchConfidence.LOW
    assert any("version markers conflict" in item for item in assessment.evidence)


@pytest.mark.parametrize(
    ("difference", "confidence"),
    [
        (0, LyricsMatchConfidence.HIGH),
        (999, LyricsMatchConfidence.HIGH),
        (2_000, LyricsMatchConfidence.HIGH),
        (2_001, LyricsMatchConfidence.MEDIUM),
        (5_000, LyricsMatchConfidence.MEDIUM),
        (5_001, LyricsMatchConfidence.LOW),
    ],
)
def test_duration_neighborhood_is_deliberate(
    difference: int, confidence: LyricsMatchConfidence
) -> None:
    query = LyricsQuery(
        "Elevate (Radio Edit)", ("Little Sis Nora & S3RL",), "Elevate", 183_771
    )

    assessment = assess_candidate(query, _candidate(duration_ms=183_771 + difference))

    assert assessment.confidence is confidence
    assert assessment.duration_difference_ms == difference


def test_missing_duration_is_medium_and_missing_provider_album_can_still_be_high() -> (
    None
):
    no_duration = LyricsQuery(
        "Elevate (Radio Edit)", ("Little Sis Nora & S3RL",), "Elevate", None
    )
    assert assess_candidate(no_duration, _candidate()).confidence is (
        LyricsMatchConfidence.MEDIUM
    )

    known_duration = LyricsQuery(
        "Elevate (Radio Edit)", ("Little Sis Nora & S3RL",), "Elevate", 183_771
    )
    assert assess_candidate(known_duration, _candidate(album=None)).confidence is (
        LyricsMatchConfidence.HIGH
    )


def test_same_title_different_artist_and_same_artist_different_title_are_low() -> None:
    query = LyricsQuery(
        "Elevate (Radio Edit)", ("Little Sis Nora & S3RL",), "Elevate", 183_771
    )

    assert (
        assess_candidate(query, _candidate(artist="Someone Else")).confidence
        is LyricsMatchConfidence.LOW
    )
    assert (
        assess_candidate(query, _candidate(title="Different (Radio Edit)")).confidence
        is LyricsMatchConfidence.LOW
    )


def test_album_conflict_prevents_high_without_overriding_track_identity() -> None:
    query = LyricsQuery(
        "Elevate (Radio Edit)", ("Little Sis Nora & S3RL",), "Elevate", 183_771
    )
    assessment = assess_candidate(query, _candidate(album="Other Album"))

    assert assessment.confidence is LyricsMatchConfidence.MEDIUM
    assert any("album differs" in item for item in assessment.evidence)
