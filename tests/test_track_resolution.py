"""Fixture-driven Stage 2 normalization, confidence, and override tests."""

from __future__ import annotations

import pytest

from konokashi.application.source_identity import SourceIdentityResolver
from konokashi.domain.identity import YouTubeIdentity
from konokashi.domain.normalization import (
    comparison_key,
    normalize_artist,
    normalize_text,
    parse_title_version,
    parse_youtube_title,
)
from konokashi.domain.tracks import ApprovedTrackIdentity, Confidence
from tests.stage2_helpers import (
    PredictableLocalPaths,
    fixture_snapshot,
    resolver,
    snapshot,
)


@pytest.mark.parametrize(
    "raw_artist",
    ("S3RL Feat. JessKah", "S3RL ft JessKah", "S3RL featuring JessKah"),
)
def test_featuring_variants_normalize_without_losing_artist(raw_artist: str) -> None:
    normalized = normalize_artist(raw_artist)

    assert normalized.value == "S3RL feat. JessKah"
    assert "normalized feat./ft./featuring notation" in normalized.transformations


def test_unicode_quotes_dashes_and_whitespace_normalize_conservatively() -> None:
    normalized = normalize_text(
        "  \N{FULLWIDTH LATIN CAPITAL LETTER S}\N{FULLWIDTH DIGIT THREE}"
        "\N{FULLWIDTH LATIN CAPITAL LETTER R}\N{FULLWIDTH LATIN CAPITAL LETTER L}  "
        "\N{EM DASH}  \N{LEFT DOUBLE QUOTATION MARK}Song"
        "\N{RIGHT DOUBLE QUOTATION MARK}  "
    )

    assert normalized.value == 'S3RL - "Song"'
    assert "normalized Unicode with NFKC" in normalized.transformations
    assert "normalized quote/dash variants" in normalized.transformations
    assert "collapsed whitespace" in normalized.transformations


@pytest.mark.parametrize(
    ("raw_title", "expected_title"),
    (
        ("Artist - Song (Official Video)", "Song"),
        ("Artist - Song [Official Audio]", "Song"),
        ("Artist - Song Lyrics", "Song"),
        ("Artist - Song - OFFICIAL MUSIC VIDEO (Download Link)", "Song"),
        ("Artist - Song HD 4K", "Song"),
    ),
)
def test_presentation_suffixes_are_removed_only_from_candidate(
    raw_title: str, expected_title: str
) -> None:
    candidate = parse_youtube_title(raw_title, ("Uploader",))

    assert candidate is not None
    assert candidate.title == expected_title
    assert any(
        "removed presentation suffix" in item for item in candidate.transformations
    )


def test_firefox_youtube_decoration_is_not_misparsed_as_song_separator() -> None:
    ambiguous = parse_youtube_title(
        "(2) How China's Biggest Scammer Got Caught - YouTube", ("",)
    )
    musical = parse_youtube_title("Artist - Song - YouTube", ("Uploader",))

    assert ambiguous is None
    assert musical is not None
    assert musical.artists == ("Artist",)
    assert musical.title == "Song"
    assert "removed browser presentation suffix '- YouTube'" in (
        musical.transformations
    )


@pytest.mark.parametrize(
    "version_title",
    (
        "Rave In My Garage (S3RL Remix Radio Edit)",
        "Song (Radio Edit)",
        "Song (Extended Mix)",
        "Song (Acoustic)",
        "Song (Live)",
        "Song (Instrumental)",
        "Song (Nightcore)",
        "Song (VIP Edit)",
    ),
)
def test_musically_meaningful_version_markers_are_preserved(
    version_title: str,
) -> None:
    candidate = parse_youtube_title(f"Artist - {version_title}", None)

    assert candidate is not None
    assert candidate.title == version_title


@pytest.mark.parametrize(
    ("title", "base", "qualifier"),
    [
        ("Party With Us (Radio Edit)", "Party With Us", "Radio Edit"),
        ("Song [Extended Mix]", "Song", "Extended Mix"),
        ("Song (Producer Remix)", "Song", "Producer Remix"),
        ("Song (2024 Remastered)", "Song", "2024 Remastered"),
        ("Song (Live)", "Song", "Live"),
        ("Song (VIP)", "Song", "VIP"),
    ],
)
def test_structural_recording_qualifier_preserves_raw_base_and_qualifier(
    title: str, base: str, qualifier: str
) -> None:
    parsed = parse_title_version(title)

    assert parsed.raw_title == title
    assert parsed.base_title == base
    assert parsed.qualifier == qualifier


@pytest.mark.parametrize(
    "title", ["Song (Chapter One)", "Song [From the Film]", "(Parenthetical Song)"]
)
def test_arbitrary_parenthetical_title_is_not_stripped(title: str) -> None:
    parsed = parse_title_version(title)

    assert parsed.raw_title == title
    assert parsed.base_title == title
    assert parsed.qualifier is None


def test_comparison_key_tolerates_case_punctuation_and_artist_separator() -> None:
    assert comparison_key("S3RL & JessKah!") == comparison_key("s3rl and jesskah")


def test_real_strawberry_equivalent_prefers_tags_and_preserves_radio_edit() -> None:
    track_resolver, _repository = resolver()
    raw = fixture_snapshot("stage2/strawberry_local_flac.json")

    resolved = track_resolver.resolve(raw)

    assert resolved.raw_snapshot is raw
    assert resolved.candidate.title == "Elevate (Radio Edit)"
    assert resolved.candidate.artists == ("Little Sis Nora & S3RL",)
    assert resolved.candidate.album == "Elevate"
    assert resolved.candidate.duration_us == 183_771_000
    assert resolved.confidence is Confidence.HIGH
    assert "preferred high-quality local MPRIS tags" in resolved.evidence


def test_jesskah_youtube_fixture_uses_title_artist_and_preserves_uploader() -> None:
    track_resolver, _repository = resolver()
    raw = fixture_snapshot("stage2/youtube_jesskah.json")

    resolved = track_resolver.resolve(raw)

    assert resolved.raw_snapshot.metadata.title == (
        "S3RL Feat. JessKah - Every Single Day (Lyrics)"
    )
    assert resolved.source_identity == YouTubeIdentity("xa4WrgqI7q0")
    assert resolved.candidate.artists == ("S3RL feat. JessKah",)
    assert resolved.candidate.title == "Every Single Day"
    assert resolved.confidence is Confidence.HIGH
    assert any("HappyCore Raver" in item for item in resolved.evidence)
    assert any(
        "presentation suffix" in item for item in resolved.candidate.transformations
    )


def test_existing_megacorp_fixture_keeps_removed_suffix_as_evidence() -> None:
    track_resolver, _repository = resolver()
    raw = fixture_snapshot("mpris/plasma_browser_integration.json")

    resolved = track_resolver.resolve(raw)

    assert resolved.candidate.artists == ("S3RL feat. sara",)
    assert resolved.candidate.title == "Will to be"
    transformation_text = "\n".join(resolved.candidate.transformations)
    assert "Megacorp Theme Song" in transformation_text
    assert "Full MTV" in transformation_text
    assert resolved.raw_snapshot.metadata.title is not None
    assert "Megacorp Theme Song (Full MTV)" in resolved.raw_snapshot.metadata.title


def test_missing_artist_and_ambiguous_youtube_title_are_low_confidence() -> None:
    track_resolver, _repository = resolver()
    raw = snapshot(
        "browser",
        title="An ambiguous upload title",
        artists=("Uploader Channel",),
        url="https://youtu.be/xa4WrgqI7q0",
    )

    resolved = track_resolver.resolve(raw)

    assert resolved.candidate.artists == ()
    assert resolved.confidence is Confidence.LOW
    assert "resolved musical artist is missing" in resolved.warnings
    assert any("uploader evidence" in item for item in resolved.evidence)


def test_conflicting_generic_title_and_artist_is_low_confidence() -> None:
    track_resolver, _repository = resolver()

    resolved = track_resolver.resolve(
        snapshot(
            "generic",
            title="Different Artist - Song",
            artists=("Reported Artist",),
            url="https://stream.example/song",
        )
    )

    assert resolved.confidence is Confidence.LOW
    assert any("conflicts" in warning for warning in resolved.warnings)


def test_user_approved_override_wins_for_same_stable_source() -> None:
    track_resolver, repository = resolver()
    raw = fixture_snapshot("stage2/youtube_jesskah.json")
    identity = SourceIdentityResolver(PredictableLocalPaths()).resolve(raw).identity
    repository.put(
        identity,
        ApprovedTrackIdentity("Every Single Day", ("S3RL feat. JessKah",)),
    )

    resolved = track_resolver.resolve(raw)

    assert resolved.confidence is Confidence.APPROVED
    assert resolved.user_approved is True
    assert resolved.automatic_candidate is not None
    assert resolved.automatic_candidate.title == "Every Single Day"
    assert resolved.automatic_candidate.artists == ("S3RL feat. JessKah",)
    assert resolved.automatic_confidence is Confidence.HIGH
    assert "user-approved correction" in resolved.evidence[-1]


@pytest.mark.parametrize(
    "raw_title",
    [
        "DECO*27 - Android Girl feat. Hatsune Miku",
        "DECO*27 - Android Girl ft. Hatsune Miku",
        "DECO*27 — Android Girl featuring Hatsune Miku",
        "Android Girl - DECO*27",
        "Android Girl feat. Hatsune Miku",
        "DECO*27「Android Girl」feat. Hatsune Miku",
    ],
)
def test_browser_credit_patterns_preserve_artist_and_title(raw_title: str) -> None:
    candidate = parse_youtube_title(raw_title, ("DECO*27",))
    assert candidate is not None
    assert candidate.title == "Android Girl"
    assert candidate.artists == ("DECO*27",)
    assert candidate.transformations


def test_browser_parser_preserves_internal_title_hyphen_and_recording_version() -> None:
    candidate = parse_youtube_title("Artist - Long-Term Song (live)", ("Artist",))
    assert candidate is not None
    assert candidate.title == "Long-Term Song (live)"
    assert parse_youtube_title("Long-Term Song", ("Uploader",)) is None
    assert parse_youtube_title("Artist「Song」 live version", ("Artist",)) is None


@pytest.mark.parametrize("version", ["live", "remix", "cover", "instrumental"])
def test_feature_credit_cleanup_does_not_erase_recording_versions(version: str) -> None:
    candidate = parse_youtube_title(
        f"Artist - Song feat. Guest ({version})", ("Artist",)
    )
    assert candidate is not None
    assert version in candidate.title
