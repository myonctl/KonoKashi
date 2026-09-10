"""Deterministic selection and duplicate-suppression policy tests."""

from __future__ import annotations

import json

import pytest

from konokashi.application.player_selectors import stable_player_suggestions
from konokashi.domain.models import PlayerListResult
from konokashi.domain.tracks import PlayerSelectionConfig
from tests.stage2_helpers import (
    FIXTURES,
    fixture_snapshot,
    player_list,
    selection_service,
    snapshot,
)


def selected_name(result: object) -> str | None:
    from konokashi.domain.tracks import PlayerSelectionResult

    assert isinstance(result, PlayerSelectionResult)
    if result.selected is None:
        return None
    return result.selected.track.raw_snapshot.service_name


def test_zero_players_has_no_selection() -> None:
    result = selection_service().select(PlayerListResult())

    assert result.selected is None
    assert result.alternatives == ()
    assert result.suppressed == ()


def test_discovered_player_suggestions_remove_transient_instance_suffixes() -> None:
    result = player_list(
        snapshot("org.mpris.MediaPlayer2.firefox.instance_123"),
        snapshot("org.mpris.MediaPlayer2.firefox.instance_456"),
        snapshot("org.mpris.MediaPlayer2.plasma-browser-integration"),
        snapshot("strawberry"),
    )

    assert stable_player_suggestions(result) == (
        "firefox",
        "plasma-browser-integration",
        "strawberry",
    )


def test_one_playing_player_is_selected_with_explanation() -> None:
    result = selection_service().select(player_list(snapshot("strawberry")))

    assert selected_name(result) == "strawberry"
    assert result.selected is not None
    assert "+ currently playing" in result.selected.reasons
    assert "+ usable title" in result.selected.reasons
    assert "+ usable duration" in result.selected.reasons
    assert "+ usable position" in result.selected.reasons
    assert "+ media URL" not in result.selected.reasons


def test_playing_player_beats_paused_player() -> None:
    result = selection_service().select(
        player_list(snapshot("paused", status="Paused"), snapshot("playing"))
    )

    assert selected_name(result) == "playing"
    assert [item.track.raw_snapshot.service_name for item in result.alternatives] == [
        "paused"
    ]


def test_two_unrelated_playing_players_remain_independent() -> None:
    local = snapshot("strawberry", url="file:///music/song.flac")
    youtube = snapshot(
        "plasma-browser-integration",
        title="Artist - Different Song",
        artists=("Uploader",),
        url="https://youtu.be/xa4WrgqI7q0",
    )

    result = selection_service().select(player_list(local, youtube))

    assert len(result.alternatives) == 1
    assert result.suppressed == ()
    assert any("multiple independent players" in warning for warning in result.warnings)


def test_weaker_firefox_duplicate_is_suppressed_naturally() -> None:
    firefox = fixture_snapshot("mpris/firefox_native.json")
    plasma = fixture_snapshot("mpris/plasma_browser_integration.json")

    result = selection_service().select(player_list(firefox, plasma))

    assert selected_name(result) == "plasma-browser-integration"
    assert [
        item.assessment.track.raw_snapshot.service_name for item in result.suppressed
    ] == ["firefox.instance_1_95"]
    assert "lower selection rank/metadata quality" in result.suppressed[0].reason
    assert result.selected is not None
    assert any("weaker duplicate" in reason for reason in result.selected.reasons)


def test_blank_firefox_artist_and_youtube_suffix_do_not_beat_richer_duplicate() -> None:
    fixture = json.loads(
        (FIXTURES / "stage2/youtube_non_music_duplicate.json").read_text(
            encoding="utf-8"
        )
    )
    url = f"https://www.youtube.com/watch?v={fixture['video_id']}"
    firefox_data = fixture["firefox"]
    plasma_data = fixture["plasma"]
    firefox = snapshot(
        firefox_data["service"],
        status="Paused",
        title=firefox_data["title"],
        artists=tuple(firefox_data["artists"]),
        url=url,
        duration_us=firefox_data["duration_us"],
        position_us=firefox_data["position_us"],
    )
    plasma = snapshot(
        plasma_data["service"],
        status="Paused",
        title=plasma_data["title"],
        artists=tuple(plasma_data["artists"]),
        url=url,
        duration_us=plasma_data["duration_us"],
        position_us=plasma_data["position_us"],
    )

    result = selection_service().select(player_list(firefox, plasma))

    assert selected_name(result) == "plasma-browser-integration"
    assert result.selected is not None
    assert result.selected.track.candidate.title == (
        "How China's Biggest Scammer Got Caught"
    )
    assert result.selected.track.candidate.artists == ()
    assert result.selected.track.confidence.value == "Low"
    assert [
        item.assessment.track.raw_snapshot.service_name for item in result.suppressed
    ] == ["firefox.instance_1_58"]
    assert "+ usable artist" not in result.suppressed[0].assessment.reasons


def test_same_youtube_video_with_different_playlist_parameters_is_duplicate() -> None:
    first = snapshot(
        "first",
        title="Artist - Song",
        url="https://www.youtube.com/watch?v=xa4WrgqI7q0&list=ONE",
    )
    second = snapshot(
        "second",
        title="Artist - Song",
        url="https://youtu.be/xa4WrgqI7q0?list=TWO&index=9",
    )

    result = selection_service().select(player_list(first, second))

    assert len(result.suppressed) == 1
    assert result.alternatives == ()


def test_same_title_with_different_youtube_ids_is_not_duplicate() -> None:
    first = snapshot(
        "first",
        title="Artist - Song",
        url="https://youtu.be/xa4WrgqI7q0",
    )
    second = snapshot(
        "second",
        title="Artist - Song",
        url="https://youtu.be/kFqGyp60d8s",
    )

    result = selection_service().select(player_list(first, second))

    assert result.suppressed == ()
    assert len(result.alternatives) == 1


def test_same_generic_url_and_title_with_compatible_duration_is_duplicate() -> None:
    first = snapshot(
        "first",
        title="Artist - Stream Song",
        url="https://stream.example/current#first-view",
        duration_us=180_000_000,
    )
    second = snapshot(
        "second",
        title="Artist - Stream Song",
        url="https://stream.example/current#second-view",
        duration_us=181_000_000,
    )

    result = selection_service().select(player_list(first, second))

    assert len(result.suppressed) == 1


def test_generic_same_url_with_incompatible_duration_is_not_duplicate() -> None:
    first = snapshot(
        "first",
        title="Artist - Stream Song",
        url="https://stream.example/current",
        duration_us=180_000_000,
    )
    second = snapshot(
        "second",
        title="Artist - Stream Song",
        url="https://stream.example/current",
        duration_us=240_000_000,
    )

    result = selection_service().select(player_list(first, second))

    assert result.suppressed == ()
    assert len(result.alternatives) == 1


def test_preferred_player_wins_within_same_playback_state() -> None:
    complete = snapshot("complete")
    preferred = snapshot("preferred", artists=None, duration_us=None, position_us=None)

    result = selection_service().select(
        player_list(complete, preferred),
        PlayerSelectionConfig(preferred_players=("preferred",)),
    )

    assert selected_name(result) == "preferred"
    assert result.selected is not None
    assert "+ configured preferred player" in result.selected.reasons


def test_ignored_player_is_excluded_with_diagnostic() -> None:
    result = selection_service().select(
        player_list(snapshot("ignored"), snapshot("kept", status="Paused")),
        PlayerSelectionConfig(ignored_players=("ignored",)),
    )

    assert selected_name(result) == "kept"
    assert result.unavailable_diagnostics == (
        "ignored: ignored by player configuration",
    )


def test_bare_firefox_ignore_matches_native_family_not_plasma_metadata() -> None:
    firefox = snapshot("firefox.instance_1_58")
    plasma = fixture_snapshot("stage2/youtube_jesskah.json")

    result = selection_service().select(
        player_list(firefox, plasma),
        PlayerSelectionConfig(ignored_players=("firefox",)),
    )

    assert selected_name(result) == "plasma-browser-integration"
    assert result.unavailable_diagnostics == (
        "firefox.instance_1_58: ignored by player configuration",
    )


def test_preferred_plasma_is_selected_while_native_firefox_family_is_ignored() -> None:
    firefox = snapshot("firefox.instance_1_58")
    plasma = fixture_snapshot("stage2/youtube_jesskah.json")

    result = selection_service().select(
        player_list(firefox, plasma),
        PlayerSelectionConfig(
            preferred_players=("plasma-browser-integration",),
            ignored_players=("firefox",),
        ),
    )

    assert selected_name(result) == "plasma-browser-integration"
    assert result.selected is not None
    assert "+ configured preferred player" in result.selected.reasons


@pytest.mark.parametrize(
    "selector",
    (
        "plasma-browser-integration",
        "service:org.mpris.MediaPlayer2.plasma-browser-integration",
    ),
)
def test_exact_plasma_ignore_really_excludes_plasma(selector: str) -> None:
    plasma = fixture_snapshot("stage2/youtube_jesskah.json")

    result = selection_service().select(
        player_list(plasma),
        PlayerSelectionConfig(ignored_players=(selector,)),
    )

    assert result.selected is None
    assert result.unavailable_diagnostics == (
        "plasma-browser-integration: ignored by player configuration",
    )


@pytest.mark.parametrize("selector", ("firefox", "family:firefox"))
@pytest.mark.parametrize("service", ("firefox.instance_1_58", "firefox.instance_9_204"))
def test_native_firefox_family_ignore_survives_instance_suffix_changes(
    service: str,
    selector: str,
) -> None:
    result = selection_service().select(
        player_list(snapshot(service)),
        PlayerSelectionConfig(ignored_players=(selector,)),
    )

    assert result.selected is None
    assert result.unavailable_diagnostics == (
        f"{service}: ignored by player configuration",
    )


def test_exact_service_selector_does_not_include_instance_family() -> None:
    result = selection_service().select(
        player_list(snapshot("firefox.instance_1_58")),
        PlayerSelectionConfig(ignored_players=("service:firefox",)),
    )

    assert selected_name(result) == "firefox.instance_1_58"


def test_bare_selector_does_not_search_descriptive_or_track_metadata() -> None:
    unrelated = snapshot(
        "unrelated-player",
        identity="firefox",
        desktop_entry="firefox",
        title="A Firefox documentary",
        artists=("Firefox Ensemble",),
    )

    result = selection_service().select(
        player_list(unrelated),
        PlayerSelectionConfig(ignored_players=("firefox",)),
    )

    assert selected_name(result) == "unrelated-player"


def test_explicitly_ignored_player_wins_over_preference() -> None:
    result = selection_service().select(
        player_list(snapshot("plasma-browser-integration")),
        PlayerSelectionConfig(
            preferred_players=("plasma-browser-integration",),
            ignored_players=("plasma-browser-integration",),
        ),
    )

    assert result.selected is None
    assert result.unavailable_diagnostics == (
        "plasma-browser-integration: ignored by player configuration",
    )


def test_unknown_ignored_selector_is_harmless() -> None:
    result = selection_service().select(
        player_list(snapshot("strawberry")),
        PlayerSelectionConfig(ignored_players=("unknown-player",)),
    )

    assert selected_name(result) == "strawberry"


@pytest.mark.parametrize("selector", ("identity:firefox", "desktop-entry:firefox"))
def test_descriptive_fields_require_explicit_exact_selector(selector: str) -> None:
    unrelated = snapshot(
        "unrelated-player", identity="firefox", desktop_entry="firefox"
    )

    result = selection_service().select(
        player_list(unrelated),
        PlayerSelectionConfig(ignored_players=(selector,)),
    )

    assert result.selected is None


def test_equal_rank_uses_service_name_as_deterministic_tie_break() -> None:
    result = selection_service().select(
        player_list(
            snapshot("zeta", url="https://stream.example/zeta"),
            snapshot("alpha", url="https://stream.example/alpha"),
        )
    )

    assert selected_name(result) == "alpha"
    assert any("final tie-break" in warning for warning in result.warnings)


def test_duplicate_disappearance_promotes_remaining_observation() -> None:
    service = selection_service()
    firefox = fixture_snapshot("mpris/firefox_native.json")
    plasma = fixture_snapshot("mpris/plasma_browser_integration.json")
    initial = service.select(player_list(firefox, plasma))

    after_disappearance = service.select(player_list(firefox))

    assert selected_name(initial) == "plasma-browser-integration"
    assert selected_name(after_disappearance) == "firefox.instance_1_95"
    assert after_disappearance.suppressed == ()


def test_strawberry_and_youtube_tracks_are_never_cross_source_duplicates() -> None:
    strawberry = fixture_snapshot("stage2/strawberry_local_flac.json")
    youtube = fixture_snapshot("stage2/youtube_jesskah.json")

    result = selection_service().select(player_list(strawberry, youtube))

    assert result.suppressed == ()
    assert len(result.alternatives) == 1


def test_temporary_override_selects_existing_player_over_persistent_preference() -> (
    None
):
    service = selection_service()
    players = player_list(snapshot("strawberry"), snapshot("vlc"))
    config = PlayerSelectionConfig(
        preferred_players=("vlc",), ignored_players=("strawberry",)
    )

    overridden = service.select(players, config, player_override="family:strawberry")
    normal_next_launch = service.select(players, config)

    assert selected_name(overridden) == "strawberry"
    assert overridden.warnings[0] == (
        "temporary player override active: family:strawberry"
    )
    assert selected_name(normal_next_launch) == "vlc"
    assert config == PlayerSelectionConfig(
        preferred_players=("vlc",), ignored_players=("strawberry",)
    )


def test_temporary_override_uses_normalized_service_family_names() -> None:
    result = selection_service().select(
        player_list(snapshot("org.mpris.MediaPlayer2.firefox.instance_42")),
        player_override="firefox",
    )

    assert selected_name(result) == "firefox.instance_42"


def test_unavailable_temporary_override_returns_actionable_diagnostic() -> None:
    result = selection_service().select(
        player_list(snapshot("vlc")), player_override="strawberry"
    )

    assert result.selected is None
    assert result.unavailable_diagnostics == (
        "temporary player override 'strawberry' did not match any available "
        "MPRIS player",
    )
