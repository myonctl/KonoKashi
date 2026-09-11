"""Public Unison read contract, attribution metadata, and bounded failures."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from konokashi.domain.lyrics import (
    LyricsMatchConfidence,
    LyricsProviderStatus,
    LyricsQuery,
    LyricTimingLevel,
)
from konokashi.infrastructure.lyrics.provider_documents import (
    ProviderLyricDocumentBuilder,
)
from konokashi.infrastructure.lyrics.unison import UnisonLyricsProvider


def _record(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": 71,
        "videoId": "fixture-video",
        "song": "Synthetic Song (Radio Edit)",
        "artist": "Fixture Artist",
        "album": "Synthetic Album",
        "duration": 183.771,
        "lyrics": "[00:01.12]Fixture one\n[00:02.34]Fixture two",
        "format": "lrc",
        "language": "en",
        "syncType": "linesync",
        "confidence": "high",
    }
    value.update(overrides)
    return value


def _response(records: list[object]) -> dict[str, object]:
    return {"success": True, "data": records}


def _ttml() -> str:
    return (
        '<tt xmlns="http://www.w3.org/ns/ttml" '
        'xmlns:ttp="http://www.w3.org/ns/ttml#parameter" ttp:timeBase="media">'
        '<body><div><p xml:id="line-1" begin="0:01.000" end="0:03.000">'
        '<span begin="0:01.000" end="0:02.000">Fixture</span> '
        '<span begin="0:02.000" end="0:03.000">words</span>'
        "</p></div></body></tt>"
    )


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
) -> UnisonLyricsProvider:
    return UnisonLyricsProvider(
        base_url="https://unison.test/root",
        connect_timeout=1.25,
        read_timeout=2.5,
        transport=httpx.MockTransport(handler),
    )


def test_exact_uses_public_signature_without_authentication() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_response([_record()]))

    provider = _provider(handler)
    result = provider.exact(
        LyricsQuery(
            "Synthetic Song (Radio Edit)",
            ("Fixture Artist",),
            "Synthetic Album",
            183_771,
        )
    )

    assert result.status is LyricsProviderStatus.RESULTS
    assert requests[0].url.path == "/root/lyrics/search"
    assert dict(requests[0].url.params) == {
        "song": "Synthetic Song (Radio Edit)",
        "artist": "Fixture Artist",
        "album": "Synthetic Album",
        "duration": "183.771",
    }
    assert "authorization" not in requests[0].headers
    assert provider.timeout.connect == 1.25
    assert provider.timeout.read == 2.5
    assert result.candidates[0].provider_duration_matched


def test_exact_duration_filter_is_explicit_evidence_not_fabricated_metadata() -> None:
    result = _provider(
        lambda _request: httpx.Response(200, json=_response([_record(duration=None)]))
    ).exact(
        LyricsQuery(
            "Synthetic Song (Radio Edit)",
            ("Fixture Artist",),
            "Synthetic Album",
            183_771,
        )
    )

    candidate = result.candidates[0]
    assert candidate.duration_ms is None
    assert candidate.provider_duration_matched


def test_field_and_broad_search_keep_distinct_provider_contracts() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_response([]))

    provider = _provider(handler)
    provider.search(LyricsQuery("Synthetic Song", ("Fixture Artist",), None, 1000))
    provider.search(
        LyricsQuery("Localized title", ("Fixture Artist",), None, None, broad=True)
    )

    assert dict(requests[0].url.params) == {
        "song": "Synthetic Song",
        "artist": "Fixture Artist",
        "duration": "1",
    }
    assert dict(requests[1].url.params) == {"q": "Localized title Fixture Artist"}


def test_lrc_and_plain_records_retain_community_confidence() -> None:
    result = _provider(
        lambda _request: httpx.Response(
            200,
            json=_response(
                [
                    _record(),
                    _record(
                        id=72,
                        lyrics="Fixture plain text",
                        format="plain",
                        syncType="plain",
                        confidence="medium",
                    ),
                ]
            ),
        )
    ).search(LyricsQuery("Synthetic Song", ("Fixture Artist",), None, None))

    assert result.status is LyricsProviderStatus.RESULTS
    assert result.candidates[0].provider == "Unison"
    assert result.candidates[0].synced_lyrics is not None
    assert result.candidates[0].provider_confidence is LyricsMatchConfidence.HIGH
    assert result.candidates[1].plain_lyrics == "Fixture plain text"
    assert result.candidates[1].provider_confidence is LyricsMatchConfidence.MEDIUM


def test_invalid_ttml_is_ignored_without_hiding_supported_records() -> None:
    result = _provider(
        lambda _request: httpx.Response(
            200,
            json=_response(
                [
                    _record(format="ttml", syncType="richsync", lyrics="<tt/>"),
                    _record(id=72),
                ]
            ),
        )
    ).search(LyricsQuery("Synthetic Song", ("Fixture Artist",), None, None))

    assert result.status is LyricsProviderStatus.RESULTS
    assert [item.record_id for item in result.candidates] == ["72"]
    assert any("timed lines" in item for item in result.diagnostics)


def test_search_metadata_is_hydrated_with_rich_ttml_detail() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/lyrics/search"):
            record = _record(format="ttml", syncType="richsync", lyrics=_ttml())
            record.pop("lyrics")
            return httpx.Response(200, json=_response([record]))
        assert request.url.path.endswith("/lyrics/71")
        return httpx.Response(
            200,
            json=_response([_record()])
            | {"data": _record(format="ttml", syncType="richsync", lyrics=_ttml())},
        )

    result = _provider(handler).search(
        LyricsQuery("Synthetic Song", ("Fixture Artist",), None, None)
    )

    assert result.status is LyricsProviderStatus.RESULTS
    assert len(requests) == 2
    candidate = result.candidates[0]
    assert candidate.duration_ms == 183_771
    assert candidate.parsed_lyrics is not None
    assert candidate.parsed_lyrics.timing_level.value == "word"
    assert candidate.language == "en"

    document, diagnostics = ProviderLyricDocumentBuilder().build(
        candidate, datetime(2026, 9, 11, tzinfo=UTC)
    )
    assert document is not None
    assert document.source_name == "Unison"
    assert document.language == "en"
    assert document.timing_level is LyricTimingLevel.WORD
    assert diagnostics == ()


def test_detail_rate_limit_is_not_misclassified_or_negative_cached() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/lyrics/search"):
            record = _record()
            record.pop("lyrics")
            return httpx.Response(200, json=_response([record]))
        return httpx.Response(429, headers={"retry-after": "17"})

    result = _provider(handler).search(
        LyricsQuery("Synthetic Song", ("Fixture Artist",), None, None)
    )

    assert result.status is LyricsProviderStatus.RATE_LIMITED
    assert result.retry_after_seconds == 17
    assert result.raw_payload is None


@pytest.mark.parametrize(
    ("response", "status"),
    [
        (httpx.Response(404), LyricsProviderStatus.NO_RESULT),
        (
            httpx.Response(429, headers={"retry-after": "42"}),
            LyricsProviderStatus.RATE_LIMITED,
        ),
        (httpx.Response(503), LyricsProviderStatus.UNAVAILABLE),
        (httpx.Response(302), LyricsProviderStatus.INVALID_RESPONSE),
        (
            httpx.Response(200, json={"success": False}),
            LyricsProviderStatus.INVALID_RESPONSE,
        ),
        (
            httpx.Response(200, json={"success": True, "data": {}}),
            LyricsProviderStatus.INVALID_RESPONSE,
        ),
    ],
)
def test_http_and_envelope_failures_are_typed(
    response: httpx.Response, status: LyricsProviderStatus
) -> None:
    result = _provider(lambda _request: response).search(
        LyricsQuery("Synthetic Song", ("Fixture Artist",), None, None)
    )

    assert result.status is status
    if status is LyricsProviderStatus.RATE_LIMITED:
        assert result.retry_after_seconds == 42


@pytest.mark.parametrize("error_type", (httpx.ReadTimeout, httpx.ConnectError))
def test_transport_failures_are_unavailable(
    error_type: type[httpx.TransportError],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("private transport detail", request=request)

    result = _provider(handler).search(
        LyricsQuery("Synthetic Song", ("Fixture Artist",), None, None)
    )

    assert result.status is LyricsProviderStatus.UNAVAILABLE
    assert all("private transport detail" not in item for item in result.diagnostics)


def test_malformed_and_oversized_payloads_are_rejected() -> None:
    malformed = _provider(
        lambda _request: httpx.Response(
            200,
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
    ).search(LyricsQuery("Song", ("Artist",), None, None))
    oversized = _provider(
        lambda _request: httpx.Response(
            200,
            content=b"{" + b" " * 2_000_001 + b"}",
            headers={"content-type": "application/json"},
        )
    ).search(LyricsQuery("Song", ("Artist",), None, None))

    assert malformed.status is LyricsProviderStatus.INVALID_RESPONSE
    assert oversized.status is LyricsProviderStatus.INVALID_RESPONSE
    assert any("size limit" in item for item in oversized.diagnostics)


def test_cached_payload_round_trips_without_network() -> None:
    provider = _provider(
        lambda _request: (_ for _ in ()).throw(AssertionError("no network"))
    )
    payload = httpx.Response(200, json=_response([_record()])).content

    result = provider.parse_cached(payload, search=True)

    assert result.status is LyricsProviderStatus.RESULTS
    assert result.candidates[0].record_id == "71"
    assert (
        provider.parse_cached(b" " * 2_000_001, search=True).status
        is LyricsProviderStatus.INVALID_RESPONSE
    )
