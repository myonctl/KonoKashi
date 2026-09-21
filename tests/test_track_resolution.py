"""Fixture-driven Stage 2 normalization, confidence, and override tests."""

from __future__ import annotations

import pytest

from konokashi.application.resolve_track import with_youtube_metadata_candidates
from konokashi.application.source_identity import SourceIdentityResolver
from konokashi.domain.identity import (
    GenericMprisIdentity,
    PersistenceScope,
    YouTubeIdentity,
)
from konokashi.domain.normalization import (
    comparison_key,
    normalize_artist,
    normalize_text,
    parse_artist_credits,
    parse_title_version,
    parse_youtube_title,
    parse_youtube_title_candidates,
    parse_youtube_title_hypotheses,
)
from konokashi.domain.tracks import ApprovedTrackIdentity, Confidence, TrackCandidate
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
        ("Artist - Song (Video)", "Song"),
        ("Artist - Song (Official Video Remastered)", "Song"),
        ("Artist - Song (Official Video - Upscaled)", "Song"),
        ("Artist - Song (Official HD Music Video)", "Song"),
        ("Artist - Song (Official 4K Video)", "Song"),
        ("Artist - Song (Official Video) (4K Remaster)", "Song"),
        ("Artist - Song (Clip Officiel)", "Song"),
        ("Artist - Song (Video Ufficiale)", "Song"),
        ("Artist - Song (Videoclip Oficial)", "Song"),
        ("Artist - Song (Oficiální videoklip)", "Song"),
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


def test_localized_artist_and_quoted_mv_title_are_parsed_structurally() -> None:
    candidate = parse_youtube_title("X1 (엑스원) 'FLASH' MV", ("Uploader",))

    assert candidate is not None
    assert candidate.artists == ("X1",)
    assert candidate.title == "FLASH"
    assert candidate.strategy == "youtube-title:localized-artist quoted-video"
    assert "parsed explicit artist, localized alias, and quoted video title" in (
        candidate.transformations
    )


def test_decorated_parenthetical_lyric_title_is_parsed_structurally() -> None:
    candidate = parse_youtube_title(
        "★☆★Laura Pausini (La soledad letra)★☆★",
        ("Uploader",),
    )

    assert candidate is not None
    assert candidate.artists == ("Laura Pausini",)
    assert candidate.title == "La soledad"
    assert candidate.strategy == "youtube-title:decorated parenthetical-lyrics"
    assert "removed bounded decorative edge stars" in candidate.transformations


@pytest.mark.parametrize(
    "raw_title",
    (
        "Artist (Song without a presentation label)",
        "★Artist (Song lyrics)",
        "Artist (Song lyrics)★",
        "★☆★Artist (Song chapter)★☆★",
    ),
)
def test_parenthetical_title_patterns_require_complete_explicit_structure(
    raw_title: str,
) -> None:
    assert parse_youtube_title(raw_title, ("Uploader",)) is None


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


def test_song_named_video_is_not_erased_as_a_presentation_suffix() -> None:
    candidate = parse_youtube_title("Artist - Video (live)", None)

    assert candidate is not None
    assert candidate.title == "Video (live)"


def test_presentation_group_before_feature_credit_is_removed_after_credit_parse() -> (
    None
):
    candidate = parse_youtube_title(
        "Artist - Song (Official HD Music Video) ft. Guest",
        None,
    )

    assert candidate is not None
    assert candidate.title == "Song"
    assert candidate.artist_credit is not None
    assert candidate.artist_credit.contributors == ("Guest",)


@pytest.mark.parametrize(
    ("title", "base", "qualifier"),
    [
        ("Party With Us (Radio Edit)", "Party With Us", "Radio Edit"),
        ("Song [Extended Mix]", "Song", "Extended Mix"),
        ("Song (Producer Remix)", "Song", "Producer Remix"),
        ("Song (2024 Remastered)", "Song", "2024 Remastered"),
        ("Song (Live)", "Song", "Live"),
        ("Song (VIP)", "Song", "VIP"),
        ("Song (Original)", "Song", "Original"),
        ("Song (Remastered 2025)", "Song", "Remastered 2025"),
        ("Song (Karaoke)", "Song", "Karaoke"),
        ("Song (Sped Up)", "Song", "Sped Up"),
        ("Song (Slowed + Reverb)", "Song", "Slowed + Reverb"),
        ("Song (Nightcore)", "Song", "Nightcore"),
        ("Song (Japanese Version)", "Song", "Japanese Version"),
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


def test_url_less_chromium_title_is_parsed_without_trusting_uploader_or_url() -> None:
    track_resolver, _repository = resolver()
    raw = fixture_snapshot("mpris/chromium_url_less.json")

    resolved = track_resolver.resolve(raw)

    assert isinstance(resolved.source_identity, GenericMprisIdentity)
    assert resolved.source_identity.persistence_scope is PersistenceScope.SESSION_ONLY
    assert resolved.raw_snapshot.metadata.url is None
    assert resolved.raw_snapshot.metadata.artists == ("AsterValeChannel",)
    assert resolved.candidate.title == "Glass Horizon"
    assert resolved.candidate.artists == ("Aster Vale",)
    assert resolved.candidate.duration_us == 180_000_000
    assert resolved.confidence is Confidence.MEDIUM
    assert resolved.candidate.strategy == "browser-title:spaced dash"
    assert all(
        candidate.identity_confidence is Confidence.LOW
        for candidate in resolved.interpretation_candidates[1:]
    )
    assert resolved.interpretation_candidates[-1].title == (
        "Aster Vale - Glass Horizon (Official Video)"
    )
    assert resolved.interpretation_candidates[-1].artists == ("AsterValeChannel",)


def test_url_less_browser_without_clear_title_does_not_promote_uploader() -> None:
    track_resolver, _repository = resolver()
    raw = snapshot(
        "chromium.instance_2",
        title="An unclear public upload",
        artists=("Example Upload Channel",),
        url=None,
    )

    resolved = track_resolver.resolve(raw)

    assert isinstance(resolved.source_identity, GenericMprisIdentity)
    assert resolved.candidate.title == "An unclear public upload"
    assert resolved.candidate.artists == ("Example Upload Channel",)
    assert resolved.candidate.identity_confidence is Confidence.LOW
    assert resolved.confidence is Confidence.LOW
    assert len(resolved.interpretation_candidates) == 1


def test_url_less_topic_channel_conflict_cannot_auto_confirm_dash_artist() -> None:
    track_resolver, _repository = resolver()
    raw = snapshot(
        "chromium.instance_topic",
        title="FABLE - Glass Horizon",
        artists=("Example Maker - Topic",),
        url=None,
        duration_us=133_641_000,
    )

    resolved = track_resolver.resolve(raw)

    assert resolved.candidate.title == "Glass Horizon"
    assert resolved.candidate.artists == ("FABLE",)
    assert resolved.candidate.identity_confidence is Confidence.LOW
    assert resolved.confidence is Confidence.LOW
    assert any("Topic channel conflicts" in item for item in resolved.evidence)


def test_url_less_nonbrowser_keeps_reported_metadata_policy() -> None:
    track_resolver, _repository = resolver()
    raw = snapshot(
        "radio",
        title="Artist - Song",
        artists=("Station Channel",),
        url=None,
    )

    resolved = track_resolver.resolve(raw)

    assert resolved.candidate.title == "Artist - Song"
    assert resolved.candidate.artists == ("Station Channel",)
    assert resolved.candidate.strategy == "reported-mpris"
    assert len(resolved.interpretation_candidates) == 1


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


@pytest.mark.parametrize("raw_duration", (-1, 0, 8 * 24 * 60 * 60 * 1_000_000))
def test_raw_invalid_duration_is_retained_only_in_snapshot_audit(
    raw_duration: int,
) -> None:
    track_resolver, _repository = resolver()
    raw = snapshot("generic", duration_us=raw_duration)

    resolved = track_resolver.resolve(raw)

    assert resolved.raw_snapshot.metadata.duration_us == raw_duration
    assert resolved.candidate.duration_us is None
    assert "duration is missing" in resolved.warnings
    assert ("duration", "mpris-duration") not in resolved.candidate.field_provenance


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


def test_parenthesized_feature_credit_remains_part_of_provider_title() -> None:
    candidate = parse_youtube_title(
        "Maretu/Hatsune Miku - NYAN (feat. HATSUNE MIKU) (Official Music Video)",
        (),
    )

    assert candidate is not None
    assert candidate.title == "NYAN (feat. HATSUNE MIKU)"
    assert candidate.artists == ("Maretu/Hatsune Miku",)
    assert candidate.artist_credit is not None
    assert candidate.artist_credit.contributors == ("HATSUNE MIKU",)


def test_repeated_title_disambiguates_one_internal_spaced_dash_boundary() -> None:
    candidates = parse_youtube_title_candidates(
        "Yung Kai - blue - blue (Official Music Video)",
        (),
    )

    assert [(item.title, item.artists) for item in candidates] == [
        ("blue - blue", ("Yung Kai",)),
        ("blue", ("Yung Kai - blue",)),
    ]
    assert candidates[1].strategy.endswith("optional-repeated-title")


def test_unrelated_internal_spaced_dash_does_not_add_an_interpretation() -> None:
    candidates = parse_youtube_title_candidates(
        "Artist - Long-Term Song - Live at Home",
        (),
    )

    assert len(candidates) == 1
    assert candidates[0].title == "Long-Term Song - Live at Home"


@pytest.mark.parametrize(
    "raw_title",
    (
        "DECO*27 / Android Girl feat. Hatsune Miku",
        "DECO*27 | Android Girl feat. Hatsune Miku",
        "DECO*27 : Android Girl feat. Hatsune Miku",
        "DECO*27 · Android Girl feat. Hatsune Miku",
    ),
)
def test_ambiguous_separators_require_and_retain_artist_corroboration(
    raw_title: str,
) -> None:
    candidate = parse_youtube_title(raw_title, ("DECO*27",))

    assert candidate is not None
    assert candidate.title == "Android Girl"
    assert candidate.artist_credit is not None
    assert candidate.artist_credit.main_artists == ("DECO*27",)
    assert candidate.artist_credit.contributors == ("Hatsune Miku",)
    assert candidate.strategy.startswith("youtube-title:")
    assert parse_youtube_title(raw_title, ("Unrelated uploader",)) is None


def test_artist_arrays_and_credit_separators_preserve_order_and_names() -> None:
    credit = parse_artist_credits(("Lida • S3RL", "Guest A feat. Guest B & Guest C"))

    assert credit.main_artists == ("Lida", "S3RL", "Guest A")
    assert credit.contributors == ("Guest B", "Guest C")


def test_arbitrary_trailing_group_is_an_optional_candidate_variant() -> None:
    candidates = parse_youtube_title_candidates(
        "Lida, S3RL - Али Ули [Премьера альбома]",
        ("Lida",),
    )

    assert [item.title for item in candidates] == [
        "Али Ули [Премьера альбома]",
        "Али Ули",
    ]
    assert candidates[1].strategy.endswith("optional-trailing-group")


@pytest.mark.parametrize(
    "raw_title",
    (
        "Song - Artist",
        "Song — Artist",
        "Song - Artist (Official Video)",
        "Song | Artist",
        "Song : Artist",
        "Song · Artist",
    ),
)
def test_uncorroborated_video_separator_retains_both_bounded_orientations(
    raw_title: str,
) -> None:
    hypotheses = parse_youtube_title_hypotheses(raw_title, ("Uploader Channel",))

    assert len(hypotheses) <= 2
    assert any(
        item.title == "Song" and item.artists == ("Artist",) for item in hypotheses
    )
    assert all(item.identity_confidence is Confidence.LOW for item in hypotheses)
    assert all("uncorroborated" in item.evidence[0] for item in hypotheses)
    assert all(
        ("artists", "mpris-title") in item.field_provenance for item in hypotheses
    )


def test_ambiguous_video_title_keeps_raw_evidence_and_review_level_hypotheses() -> None:
    track_resolver, _repository = resolver()
    raw = snapshot(
        "generic",
        title="Song | Artist (Official Video)",
        artists=("Unrelated Uploader",),
        url="https://youtu.be/xa4WrgqI7q0",
    )

    resolved = track_resolver.resolve(raw)

    assert resolved.raw_snapshot.metadata.title == "Song | Artist (Official Video)"
    assert resolved.candidate.strategy == "youtube-unstructured-title"
    assert resolved.candidate.artists == ()
    assert len(resolved.interpretation_candidates) <= 3
    assert any(
        item.title == "Song" and item.artists == ("Artist",)
        for item in resolved.interpretation_candidates
    )
    assert all(
        item.identity_confidence is Confidence.LOW
        for item in resolved.interpretation_candidates[1:]
    )
    assert all(
        any("uploader evidence" in evidence for evidence in item.evidence)
        for item in resolved.interpretation_candidates
    )


def test_structured_youtube_metadata_upgrades_matching_low_confidence_hypothesis() -> (
    None
):
    track_resolver, _repository = resolver()
    raw = snapshot(
        "browser",
        title="Song | Artist (Official Video)",
        artists=("Uploader Channel",),
        url="https://youtu.be/xa4WrgqI7q0",
        duration_us=180_000_000,
    )
    track = track_resolver.resolve(raw)
    initial_count = len(track.interpretation_candidates)
    metadata = TrackCandidate(
        "Song",
        ("Artist",),
        None,
        180_000_000,
        evidence=("explicit public music-credit fields",),
        strategy="youtube-enrichment:music-fields",
        field_provenance=(
            ("title", "youtube_track"),
            ("artists", "youtube_artists"),
        ),
    )

    enriched = with_youtube_metadata_candidates(track, (metadata,))

    assert len(enriched.interpretation_candidates) == initial_count
    assert enriched.raw_snapshot == track.raw_snapshot
    promoted = next(
        item
        for item in enriched.interpretation_candidates
        if item.title == "Song" and item.artists == ("Artist",)
    )
    assert promoted.identity_confidence is Confidence.MEDIUM
    assert promoted.strategy == "youtube-enrichment:music-fields"
    assert "explicit public music-credit fields" in promoted.evidence
    assert any("uncorroborated" in item for item in promoted.evidence)
    assert ("artists", "youtube_artists") in promoted.field_provenance
