"""Real-shaped Stage 4 metadata and provider-contract fixture checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from lyricflow.domain.lyrics import LyricsMatchConfidence, LyricsQuery
from lyricflow.domain.lyrics_matching import assess_candidate
from lyricflow.infrastructure.lyrics.lrclib import LrclibLyricsProvider

FIXTURES = Path(__file__).parent / "fixtures" / "stage4"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_real_shaped_strawberry_query_matches_sanitized_lrclib_contract() -> None:
    query_data = _fixture("strawberry_elevate_query.json")
    response_data = _fixture("lrclib_elevate_response.json")
    provider = LrclibLyricsProvider(
        base_url="https://lyrics.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=response_data)
        ),
    )
    query = LyricsQuery(
        query_data["title"],
        tuple(query_data["artists"]),
        query_data["album"],
        query_data["duration_ms"],
    )

    result = provider.exact(query)

    assert result.candidates
    assert assess_candidate(query, result.candidates[0]).confidence is (
        LyricsMatchConfidence.HIGH
    )
    assert query_data["private_path_omitted"] is True


def test_youtube_fixture_keeps_uploader_out_of_musical_artist_query() -> None:
    data = _fixture("youtube_every_single_day_query.json")
    query = LyricsQuery(
        data["resolved_title"], tuple(data["resolved_artists"]), None, None
    )

    assert data["uploader"] not in query.artist_name
    assert data["raw_url_omitted"] is True


def test_black_screen_fixture_forbids_provider_query_without_artist_evidence() -> None:
    data = _fixture("youtube_black_screen_safety.json")
    query = LyricsQuery(
        data["resolved_title"], tuple(data["resolved_artists"]), None, None
    )

    assert query.artist_name == ""
    assert data["provider_query_allowed"] is False
