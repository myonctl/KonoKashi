"""Official LRCLIB retrieval contract and failure-state regressions."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event, Thread

import httpx
import pytest

from lyricflow.domain.lyrics import LyricsProviderStatus, LyricsQuery
from lyricflow.infrastructure.lyrics.lrclib import LrclibLyricsProvider


def _record(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": 3396226,
        "trackName": "Elevate (Radio Edit)",
        "artistName": "Little Sis Nora & S3RL",
        "albumName": "Elevate",
        "duration": 183.771,
        "instrumental": False,
        "plainLyrics": "君の声\nSecond",
        "syncedLyrics": "[00:01.12]君の声\n[00:02.345]Second",
    }
    value.update(overrides)
    return value


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
) -> LrclibLyricsProvider:
    return LrclibLyricsProvider(
        base_url="https://lyrics.test/root",
        connect_timeout=1.25,
        read_timeout=2.5,
        transport=httpx.MockTransport(handler),
    )


def test_exact_request_uses_official_fields_duration_headers_and_injected_url() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_record())

    provider = _provider(handler)
    result = provider.exact(
        LyricsQuery(
            "Elevate (Radio Edit)",
            ("Little Sis Nora", "S3RL"),
            "Elevate & More",
            183_771,
        )
    )

    assert result.status is LyricsProviderStatus.RESULTS
    request = requests[0]
    assert request.url.path == "/root/api/get"
    assert dict(request.url.params) == {
        "track_name": "Elevate (Radio Edit)",
        "artist_name": "Little Sis Nora & S3RL",
        "album_name": "Elevate & More",
        "duration": "183.771",
    }
    assert request.headers["user-agent"].startswith("LyricFlow/1.0.0")
    assert request.headers["accept"] == "application/json"
    assert "authorization" not in request.headers
    assert provider.timeout.connect == 1.25
    assert provider.timeout.read == 2.5


@pytest.mark.parametrize(
    ("duration_ms", "seconds"),
    [(999, "0.999"), (1000, "1"), (1499, "1.499"), (1500, "1.5")],
)
def test_duration_conversion_preserves_millisecond_boundaries(
    duration_ms: int, seconds: str
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["duration"])
        return httpx.Response(404, json={"message": "not found"})

    _provider(handler).exact(LyricsQuery("Song", ("Artist",), "Album", duration_ms))
    assert seen == [seconds]


def test_exact_is_skipped_when_album_or_duration_is_missing() -> None:
    def unexpected(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("network request should have been skipped")

    provider = _provider(unexpected)

    no_album = provider.exact(LyricsQuery("Song", ("Artist",), None, 1000))
    no_duration = provider.exact(LyricsQuery("Song", ("Artist",), "Album", None))

    assert no_album.status is LyricsProviderStatus.NO_RESULT
    assert "album" in no_album.diagnostics[0]
    assert no_duration.status is LyricsProviderStatus.NO_RESULT
    assert "duration" in no_duration.diagnostics[0]


def test_search_uses_field_params_without_fabricating_album() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[_record(albumName=None)])

    result = _provider(handler).search(
        LyricsQuery("Every Single Day", ("S3RL feat. JessKah",), None, 180_000)
    )

    assert result.status is LyricsProviderStatus.RESULTS
    assert dict(requests[0].url.params) == {
        "track_name": "Every Single Day",
        "artist_name": "S3RL feat. JessKah",
    }


@pytest.mark.parametrize(
    ("overrides", "instrumental", "plain", "synced"),
    [
        ({}, False, True, True),
        ({"plainLyrics": None}, False, False, True),
        ({"syncedLyrics": None}, False, True, False),
        (
            {"instrumental": True, "plainLyrics": None, "syncedLyrics": None},
            True,
            False,
            False,
        ),
    ],
)
def test_valid_response_forms_are_typed(
    overrides: dict[str, object], instrumental: bool, plain: bool, synced: bool
) -> None:
    provider = _provider(
        lambda _request: httpx.Response(200, json=_record(**overrides))
    )

    result = provider.exact(LyricsQuery("Song", ("Artist",), "Album", 1000))

    assert result.status is LyricsProviderStatus.RESULTS
    candidate = result.candidates[0]
    assert candidate.instrumental is instrumental
    assert (candidate.plain_lyrics is not None) is plain
    assert (candidate.synced_lyrics is not None) is synced
    assert candidate.duration_ms == 183_771
    assert candidate.plain_lyrics is None or "君の声" in candidate.plain_lyrics


@pytest.mark.parametrize(
    ("response", "status"),
    [
        (
            httpx.Response(404, json={"message": "missing"}),
            LyricsProviderStatus.NO_RESULT,
        ),
        (
            httpx.Response(500, json={"message": "down"}),
            LyricsProviderStatus.UNAVAILABLE,
        ),
        (
            httpx.Response(302, headers={"location": "/elsewhere"}),
            LyricsProviderStatus.INVALID_RESPONSE,
        ),
        (
            httpx.Response(
                200, content=b"not json", headers={"content-type": "application/json"}
            ),
            LyricsProviderStatus.INVALID_RESPONSE,
        ),
        (
            httpx.Response(200, text="<html>bad</html>"),
            LyricsProviderStatus.INVALID_RESPONSE,
        ),
        (httpx.Response(200, json={"id": 1}), LyricsProviderStatus.INVALID_RESPONSE),
        (
            httpx.Response(200, json=_record(id="")),
            LyricsProviderStatus.INVALID_RESPONSE,
        ),
        (
            httpx.Response(200, json=_record(duration="1e999999")),
            LyricsProviderStatus.INVALID_RESPONSE,
        ),
        (
            httpx.Response(
                200,
                content=(
                    '{"id":1,"trackName":"Song","artistName":"Artist",'
                    '"albumName":"Album","duration":'
                    + "9"
                    * 5_000
                    + ',"instrumental":true,"plainLyrics":null,'
                    '"syncedLyrics":null}'
                ).encode(),
                headers={"content-type": "application/json"},
            ),
            LyricsProviderStatus.INVALID_RESPONSE,
        ),
    ],
)
def test_http_and_schema_failures_are_typed(
    response: httpx.Response, status: LyricsProviderStatus
) -> None:
    result = _provider(lambda _request: response).exact(
        LyricsQuery("Song", ("Artist",), "Album", 1000)
    )

    assert result.status is status


def test_rate_limit_parses_only_bounded_integer_retry_after() -> None:
    good = _provider(
        lambda _request: httpx.Response(429, headers={"retry-after": "42"})
    ).search(LyricsQuery("Song", ("Artist",), None, None))
    malformed = _provider(
        lambda _request: httpx.Response(429, headers={"retry-after": "tomorrow"})
    ).search(LyricsQuery("Song", ("Artist",), None, None))
    absurd = _provider(
        lambda _request: httpx.Response(429, headers={"retry-after": "999999"})
    ).search(LyricsQuery("Song", ("Artist",), None, None))

    assert good.status is LyricsProviderStatus.RATE_LIMITED
    assert good.retry_after_seconds == 42
    assert malformed.retry_after_seconds is None
    assert absurd.retry_after_seconds is None
    assert any("malformed" in item for item in malformed.diagnostics)


@pytest.mark.parametrize(
    "error_type", [httpx.ReadTimeout, httpx.ConnectError, httpx.ReadError]
)
def test_timeout_and_connect_failures_are_unavailable(
    error_type: type[httpx.TransportError],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("network failure", request=request)

    result = _provider(handler).search(LyricsQuery("Song", ("Artist",), None, None))
    assert result.status is LyricsProviderStatus.UNAVAILABLE


def test_oversized_response_is_rejected_without_json_parsing() -> None:
    payload = b"[" + b" " * 2_000_001 + b"]"
    result = _provider(
        lambda _request: httpx.Response(
            200, content=payload, headers={"content-type": "application/json"}
        )
    ).search(LyricsQuery("Song", ("Artist",), None, None))

    assert result.status is LyricsProviderStatus.INVALID_RESPONSE
    assert any("size limit" in item for item in result.diagnostics)


def test_unexpectedly_large_search_result_set_is_rejected() -> None:
    result = _provider(
        lambda _request: httpx.Response(
            200,
            json=[_record(id=position) for position in range(101)],
        )
    ).search(LyricsQuery("Song", ("Artist",), None, None))

    assert result.status is LyricsProviderStatus.INVALID_RESPONSE
    assert any("large result set" in item for item in result.diagnostics)


def test_private_source_details_never_enter_provider_request() -> None:
    private_path = "/home/example/Music/private/song.flac"
    raw_mpris_url = "https://www.youtube.com/watch?v=xa4WrgqI7q0&private=token"

    def handler(request: httpx.Request) -> httpx.Response:
        serialized = str(request.url)
        assert private_path not in serialized
        assert raw_mpris_url not in serialized
        assert "private%2Fsong" not in serialized
        assert "private%3Dtoken" not in serialized
        return httpx.Response(200, json=[])

    result = _provider(handler).search(
        LyricsQuery("Every Single Day", ("S3RL feat. JessKah",), None, 180_000)
    )

    assert result.status is LyricsProviderStatus.NO_RESULT


def test_cached_payload_round_trips_without_request() -> None:
    provider = _provider(
        lambda _request: (_ for _ in ()).throw(AssertionError("no network"))
    )
    payload = httpx.Response(200, json=[_record()]).content

    result = provider.parse_cached(payload, search=True)
    miss = provider.parse_cached(b"", search=False)

    assert result.status is LyricsProviderStatus.RESULTS
    assert result.candidates[0].record_id == "3396226"
    assert miss.status is LyricsProviderStatus.NO_RESULT


def test_inflight_request_can_be_cancelled_after_frontend_source_change() -> None:
    started = Event()
    released = Event()

    class BlockingStream(httpx.SyncByteStream):
        def __iter__(self):  # type: ignore[no-untyped-def]
            started.set()
            released.wait(2)
            yield b"[]"

        def close(self) -> None:
            released.set()

    class BlockingTransport(httpx.BaseTransport):
        def __init__(self) -> None:
            self.stream = BlockingStream()

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                stream=self.stream,
                request=request,
            )

        def close(self) -> None:
            self.stream.close()

    provider = LrclibLyricsProvider(transport=BlockingTransport())
    results = []
    worker = Thread(
        target=lambda: results.append(
            provider.search(LyricsQuery("Song", ("Artist",), None, None))
        )
    )
    worker.start()
    assert started.wait(1)

    provider.cancel_inflight()
    worker.join(1)

    assert not worker.is_alive()
    assert released.is_set()
    assert results
