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


def test_provider_duration_filter_is_explicit_independent_evidence() -> None:
    query = LyricsQuery(
        "Elevate (Radio Edit)", ("Little Sis Nora & S3RL",), "Elevate", 183_771
    )
    candidate = LyricsProviderCandidate(
        "Unison",
        "1",
        "Elevate (Radio Edit)",
        "Little Sis Nora & S3RL",
        "Elevate",
        None,
        False,
        "plain",
        None,
        provider_duration_matched=True,
    )

    assessment = assess_candidate(query, candidate)

    assert assessment.confidence is LyricsMatchConfidence.HIGH
    assert assessment.duration_difference_ms == 0
    assert any("duration filter" in item for item in assessment.evidence)


@pytest.mark.parametrize(
    "provider_title",
    [
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


def test_radio_edit_recognizes_unqualified_base_title_as_text_relation() -> None:
    query = LyricsQuery("Party With Us (Radio Edit)", ("S3RL",), None, 180_000)
    assessment = assess_candidate(
        query,
        _candidate(
            title="Party With Us",
            artist="S3RL",
            album=None,
            duration_ms=180_500,
        ),
    )

    assert assessment.title_relation == "base-title"
    assert assessment.text_confidence is LyricsMatchConfidence.HIGH
    assert assessment.timing_confidence is LyricsMatchConfidence.HIGH
    assert assessment.confidence is LyricsMatchConfidence.HIGH


def test_base_title_keeps_text_and_timing_confidence_independent() -> None:
    query = LyricsQuery("Party With Us (Radio Edit)", ("S3RL",), None, 180_000)
    assessment = assess_candidate(
        query,
        _candidate(
            title="Party With Us",
            artist="S3RL",
            album=None,
            duration_ms=240_000,
        ),
    )

    assert assessment.text_confidence is LyricsMatchConfidence.HIGH
    assert assessment.timing_confidence is LyricsMatchConfidence.LOW
    assert assessment.confidence is LyricsMatchConfidence.HIGH


def test_cross_script_phonetic_match_requires_artist_and_duration_corroboration() -> (
    None
):
    query = LyricsQuery("Android Girl", ("DECO*27",), None, 215_441)
    candidate = _candidate(
        title="アンドロイドガール",
        artist="DECO*27",
        album=None,
        duration_ms=215_200,
    )
    accepted = assess_candidate(
        query, candidate, provider_title_aliases=("andoroidogaru",)
    )
    wrong_artist = assess_candidate(
        query,
        _candidate(
            title="アンドロイドガール",
            artist="Another Artist",
            album=None,
            duration_ms=215_200,
        ),
        provider_title_aliases=("andoroidogaru",),
    )
    wrong_duration = assess_candidate(
        query,
        _candidate(
            title="アンドロイドガール",
            artist="DECO*27",
            album=None,
            duration_ms=260_000,
        ),
        provider_title_aliases=("andoroidogaru",),
    )

    assert accepted.title_relation == "phonetic-transliteration"
    assert accepted.confidence is LyricsMatchConfidence.HIGH
    assert "transliteration" in " ".join(accepted.evidence)
    assert wrong_artist.confidence is LyricsMatchConfidence.LOW
    assert wrong_duration.confidence is LyricsMatchConfidence.LOW


def test_realistic_cross_script_duration_variance_remains_bounded() -> None:
    query = LyricsQuery("Android Girl", ("DECO*27",), None, 215_441)
    candidate = _candidate(
        title="アンドロイドガール",
        artist="DECO*27",
        album=None,
        duration_ms=211_000,
    )

    assessment = assess_candidate(
        query, candidate, provider_title_aliases=("andoroidogaru",)
    )

    assert assessment.duration_difference_ms == 4_441
    assert assessment.confidence is LyricsMatchConfidence.HIGH
    assert assessment.timing_confidence is LyricsMatchConfidence.HIGH


def test_neighboring_native_title_and_weak_phonetic_coincidence_are_rejected() -> None:
    query = LyricsQuery("Android Girl", ("DECO*27",), None, 215_441)
    neighbor = assess_candidate(
        query,
        _candidate(
            title="シンセカイ案内所",
            artist="DECO*27",
            album=None,
            duration_ms=215_000,
        ),
        provider_title_aliases=("shinseikai annaijo",),
    )
    weak = assess_candidate(
        query,
        _candidate(
            title="アンドロメダ",
            artist="DECO*27",
            album=None,
            duration_ms=215_000,
        ),
        provider_title_aliases=("andoromeda",),
    )

    assert neighbor.confidence is LyricsMatchConfidence.LOW
    assert weak.confidence is LyricsMatchConfidence.LOW


def test_low_source_confidence_caps_automatic_candidate() -> None:
    query = LyricsQuery(
        "Song",
        ("Artist",),
        None,
        180_000,
        source_confidence="Low",
    )
    assessment = assess_candidate(
        query,
        _candidate(title="Song", artist="Artist", album=None, duration_ms=180_000),
    )

    assert assessment.confidence is LyricsMatchConfidence.MEDIUM
    assert "metadata confidence is Low" in " ".join(assessment.evidence)


@pytest.mark.parametrize(
    ("local_title", "provider_title"),
    [
        ("Song (Radio Edit)", "Song (Extended Mix)"),
        ("Song (Live)", "Song"),
        ("Song (Remix)", "Song"),
        ("Song (Instrumental)", "Song"),
    ],
)
def test_incompatible_or_content_changing_versions_do_not_inherit_automatically(
    local_title: str, provider_title: str
) -> None:
    assessment = assess_candidate(
        LyricsQuery(local_title, ("Artist",), None, 180_000),
        _candidate(
            title=provider_title,
            artist="Artist",
            album=None,
            duration_ms=180_000,
        ),
    )

    assert assessment.confidence is LyricsMatchConfidence.LOW


def test_raw_exact_title_relation_outranks_normalized_equivalence() -> None:
    query = LyricsQuery("Song (Radio Edit)", ("Artist",), None, 180_000)

    assert (
        assess_candidate(
            query,
            _candidate(
                title="Song (Radio Edit)",
                artist="Artist",
                album=None,
                duration_ms=180_000,
            ),
        ).title_relation
        == "exact-raw"
    )
    assert (
        assess_candidate(
            query,
            _candidate(
                title="song [radio edit]",
                artist="Artist",
                album=None,
                duration_ms=180_000,
            ),
        ).title_relation
        == "normalized"
    )


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


def test_structured_artist_credits_allow_omission_but_reject_wrong_contributor() -> (
    None
):
    query = LyricsQuery(
        "Android Girl",
        ("DECO*27 feat. Hatsune Miku",),
        None,
        215_441,
        main_artists=("DECO*27",),
        contributors=("Hatsune Miku",),
    )
    omitted = assess_candidate(
        query,
        _candidate(
            title="Android Girl",
            artist="DECO*27",
            album=None,
            duration_ms=215_200,
        ),
    )
    wrong = assess_candidate(
        query,
        _candidate(
            title="Android Girl",
            artist="DECO*27 feat. GUMI",
            album=None,
            duration_ms=215_200,
        ),
    )

    assert omitted.confidence is LyricsMatchConfidence.HIGH
    assert "omits reported contributor" in " ".join(omitted.evidence)
    assert wrong.confidence is LyricsMatchConfidence.MEDIUM
    assert wrong.confidence is not LyricsMatchConfidence.HIGH
    assert "contributor credits differ" in " ".join(wrong.evidence)


def test_reordered_or_incomplete_artist_sets_stay_below_automatic_high() -> None:
    query = LyricsQuery(
        "Али Ули",
        ("Lida, S3RL",),
        None,
        178_000,
        main_artists=("Lida", "S3RL"),
    )
    reordered = assess_candidate(
        query,
        _candidate(
            title="Али Ули",
            artist="S3RL & Lida",
            album=None,
            duration_ms=178_000,
        ),
    )
    incomplete = assess_candidate(
        query,
        _candidate(
            title="Али Ули",
            artist="Lida",
            album=None,
            duration_ms=178_000,
        ),
    )

    assert reordered.confidence is LyricsMatchConfidence.MEDIUM
    assert reordered.text_confidence is LyricsMatchConfidence.MEDIUM
    assert incomplete.confidence is LyricsMatchConfidence.LOW
