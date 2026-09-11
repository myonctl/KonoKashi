"""Bounded read-only adapter for the public Better Lyrics Unison API."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from threading import Lock
from typing import Any

import httpx

from konokashi import __version__
from konokashi.domain.lyrics import (
    MAX_SEMANTIC_LYRICS_DURATION_MS,
    LyricsMatchConfidence,
    LyricsProviderCandidate,
    LyricsProviderResult,
    LyricsProviderStatus,
    LyricsQuery,
    LyricsTextParseStatus,
)
from konokashi.infrastructure.lyrics.ttml import parse_ttml_text

DEFAULT_BASE_URL = "https://unison.boidu.dev"
DEFAULT_USER_AGENT = f"KonoKashi/{__version__} (https://github.com/myonctl/KonoKashi)"
MAX_PROVIDER_RESPONSE_BYTES = 2_000_000
MAX_PROVIDER_RECORDS = 100
MAX_HYDRATED_RECORDS = 10
MAX_RETRY_AFTER_SECONDS = 3_600


@dataclass(frozen=True, slots=True)
class _HttpResponse:
    status: int
    content_type: str
    retry_after: str | None
    payload: bytes


class UnisonLyricsProvider:
    """Translate public Unison search results into provider-neutral candidates.

    Read operations need no account or secret. Unison publishes its corpus under
    ODbL-1.0 with required attribution; product documentation and source labels
    retain that attribution.
    """

    name = "Unison"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        connect_timeout: float = 5.0,
        read_timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(
            timeout=read_timeout,
            connect=connect_timeout,
            read=read_timeout,
            write=connect_timeout,
            pool=connect_timeout,
        )
        self._transport = transport
        self._user_agent = user_agent
        self._active_clients: set[httpx.Client] = set()
        self._active_clients_lock = Lock()

    @property
    def timeout(self) -> httpx.Timeout:
        """Expose immutable timeout configuration for diagnostics/tests."""

        return self._timeout

    def cancel_inflight(self) -> None:
        """Close active read-only requests after a frontend source change."""

        with self._active_clients_lock:
            clients = tuple(self._active_clients)
        for client in clients:
            client.close()

    def exact(self, query: LyricsQuery) -> LyricsProviderResult:
        """Collect candidates using every available recording signature field."""

        missing: list[str] = []
        if not query.title.strip():
            missing.append("track title")
        if not query.artist_name.strip():
            missing.append("musical artist")
        if query.album is None or not query.album.strip():
            missing.append("album")
        if query.duration_ms is None:
            missing.append("duration")
        if missing:
            return LyricsProviderResult(
                LyricsProviderStatus.NO_RESULT,
                diagnostics=(
                    "exact Unison lookup skipped; missing " + ", ".join(missing),
                ),
            )
        assert query.album is not None
        assert query.duration_ms is not None
        return self._request(
            {
                "song": query.title,
                "artist": query.artist_name,
                "album": query.album,
                "duration": _duration_query(query.duration_ms),
            }
        )

    def search(self, query: LyricsQuery) -> LyricsProviderResult:
        """Use public field search, or explicit broad search for manual recovery."""

        if not query.title.strip() or (
            not query.broad and not query.artist_name.strip()
        ):
            return LyricsProviderResult(
                LyricsProviderStatus.NO_RESULT,
                diagnostics=(
                    "Unison search skipped; a title is required and field-based "
                    "search also requires a musical artist",
                ),
            )
        if query.broad:
            params = {"q": f"{query.title} {query.artist_name}".strip()}
        else:
            params = {"song": query.title, "artist": query.artist_name}
            if query.album and query.album.strip():
                params["album"] = query.album
            if query.duration_ms is not None:
                params["duration"] = _duration_query(query.duration_ms)
        return self._request(params)

    def parse_cached(self, payload: bytes, *, search: bool) -> LyricsProviderResult:
        """Parse adapter-owned raw bytes without performing network access."""

        del search
        if len(payload) > MAX_PROVIDER_RESPONSE_BYTES:
            return LyricsProviderResult(
                LyricsProviderStatus.INVALID_RESPONSE,
                diagnostics=("cached Unison response exceeded the safe size limit",),
            )
        if not payload:
            return LyricsProviderResult(
                LyricsProviderStatus.NO_RESULT,
                raw_payload=payload,
                diagnostics=("provider cache records a previous no-result response",),
            )
        return self._parse_payload(payload)

    def _request(self, params: dict[str, str]) -> LyricsProviderResult:
        headers = {"User-Agent": self._user_agent, "Accept": "application/json"}
        client = httpx.Client(
            timeout=self._timeout,
            follow_redirects=False,
            transport=self._transport,
            headers=headers,
        )
        with self._active_clients_lock:
            self._active_clients.add(client)
        try:
            with client:
                response = self._fetch(client, "/lyrics/search", params)
                terminal = _http_result(response, context="search")
                if terminal is not None:
                    return terminal
                hydrated, hydration_diagnostics, hydration_failure = self._hydrate(
                    client, response.payload, duration_filtered="duration" in params
                )
                if hydration_failure is not None:
                    return replace(
                        hydration_failure,
                        diagnostics=(
                            *hydration_diagnostics,
                            *hydration_failure.diagnostics,
                        ),
                    )
        except httpx.TimeoutException:
            return LyricsProviderResult(
                LyricsProviderStatus.UNAVAILABLE,
                diagnostics=("Unison request timed out",),
            )
        except httpx.TransportError:
            return LyricsProviderResult(
                LyricsProviderStatus.UNAVAILABLE,
                diagnostics=("Unison network request failed",),
            )
        except ValueError as error:
            return LyricsProviderResult(
                LyricsProviderStatus.INVALID_RESPONSE,
                diagnostics=(str(error),),
            )
        finally:
            with self._active_clients_lock:
                self._active_clients.discard(client)
        result = self._parse_payload(hydrated)
        return replace(
            result,
            diagnostics=(*hydration_diagnostics, *result.diagnostics),
        )

    def _fetch(
        self, client: httpx.Client, path: str, params: dict[str, str] | None = None
    ) -> _HttpResponse:
        with client.stream("GET", f"{self._base_url}{path}", params=params) as response:
            payload = bytearray()
            for chunk in response.iter_bytes():
                payload.extend(chunk)
                if len(payload) > MAX_PROVIDER_RESPONSE_BYTES:
                    raise ValueError("Unison response exceeded the safe size limit")
            return _HttpResponse(
                response.status_code,
                response.headers.get("content-type", ""),
                response.headers.get("retry-after"),
                bytes(payload),
            )

    def _hydrate(
        self,
        client: httpx.Client,
        payload: bytes,
        *,
        duration_filtered: bool,
    ) -> tuple[bytes, tuple[str, ...], LyricsProviderResult | None]:
        """Fetch bounded selected records because search intentionally omits text."""

        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, ValueError, RecursionError):
            return payload, (), None
        if not isinstance(decoded, dict) or not isinstance(decoded.get("data"), list):
            return payload, (), None
        records = decoded["data"]
        if len(records) > MAX_PROVIDER_RECORDS:
            return payload, (), None
        hydrated: list[object] = []
        diagnostics: list[str] = []
        failures: list[LyricsProviderResult] = []
        for position, record in enumerate(records[:MAX_HYDRATED_RECORDS]):
            if not isinstance(record, dict) or isinstance(record.get("lyrics"), str):
                hydrated.append(
                    _with_duration_filter(record, duration_filtered=duration_filtered)
                )
                continue
            try:
                record_id = _required_id(record.get("id"))
                detail_response = self._fetch(client, f"/lyrics/{record_id}")
            except ValueError as error:
                diagnostics.append(
                    f"Unison candidate {position + 1} could not be hydrated: {error}"
                )
                failures.append(
                    LyricsProviderResult(LyricsProviderStatus.INVALID_RESPONSE)
                )
                continue
            terminal = _http_result(detail_response, context="candidate detail")
            if terminal is not None:
                diagnostics.extend(terminal.diagnostics)
                failures.append(terminal)
                continue
            try:
                detail_envelope = json.loads(detail_response.payload)
            except (UnicodeDecodeError, ValueError, RecursionError):
                diagnostics.append(
                    f"Unison candidate {position + 1} detail contained malformed JSON"
                )
                failures.append(
                    LyricsProviderResult(LyricsProviderStatus.INVALID_RESPONSE)
                )
                continue
            detail = (
                detail_envelope.get("data")
                if isinstance(detail_envelope, dict)
                else None
            )
            if not isinstance(detail, dict):
                diagnostics.append(
                    f"Unison candidate {position + 1} detail envelope was invalid"
                )
                failures.append(
                    LyricsProviderResult(LyricsProviderStatus.INVALID_RESPONSE)
                )
                continue
            hydrated.append(
                _with_duration_filter(
                    {**record, **detail}, duration_filtered=duration_filtered
                )
            )
        if len(records) > MAX_HYDRATED_RECORDS:
            diagnostics.append(
                "Unison candidate hydration was capped at "
                f"{MAX_HYDRATED_RECORDS} records"
            )
        combined = json.dumps(
            {"success": decoded.get("success"), "data": hydrated},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        if len(combined) > MAX_PROVIDER_RESPONSE_BYTES:
            raise ValueError("Unison hydrated response exceeded the safe size limit")
        failure = _hydration_failure(failures) if records and not hydrated else None
        return combined, tuple(diagnostics), failure

    def _parse_payload(self, payload: bytes) -> LyricsProviderResult:
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, ValueError, RecursionError):
            return LyricsProviderResult(
                LyricsProviderStatus.INVALID_RESPONSE,
                diagnostics=("Unison returned malformed JSON",),
            )
        if not isinstance(decoded, dict) or decoded.get("success") is not True:
            return LyricsProviderResult(
                LyricsProviderStatus.INVALID_RESPONSE,
                diagnostics=("Unison response envelope was invalid",),
            )
        records = decoded.get("data")
        if not isinstance(records, list):
            return LyricsProviderResult(
                LyricsProviderStatus.INVALID_RESPONSE,
                diagnostics=("Unison search response was not a result list",),
            )
        if len(records) > MAX_PROVIDER_RECORDS:
            return LyricsProviderResult(
                LyricsProviderStatus.INVALID_RESPONSE,
                diagnostics=("Unison returned an unexpectedly large result set",),
            )
        if not records:
            return LyricsProviderResult(
                LyricsProviderStatus.NO_RESULT,
                diagnostics=("Unison search returned no candidates",),
                raw_payload=payload,
            )
        candidates: list[LyricsProviderCandidate] = []
        diagnostics: list[str] = []
        for position, record in enumerate(records):
            try:
                candidates.append(_candidate(record))
            except ValueError as error:
                diagnostics.append(f"Unison candidate {position + 1} ignored: {error}")
        if not candidates:
            return LyricsProviderResult(
                LyricsProviderStatus.INVALID_RESPONSE,
                diagnostics=tuple(diagnostics)
                or ("Unison response had no supported record",),
            )
        return LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            tuple(candidates),
            tuple(diagnostics),
            raw_payload=payload,
        )


def _duration_query(duration_ms: int) -> str:
    seconds = Decimal(duration_ms) / Decimal(1000)
    return format(seconds.normalize(), "f")


def _retry_after_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        seconds = int(value.strip())
    except ValueError:
        return None
    if 0 <= seconds <= MAX_RETRY_AFTER_SECONDS:
        return seconds
    return None


def _http_result(
    response: _HttpResponse, *, context: str
) -> LyricsProviderResult | None:
    if response.status == 404:
        return LyricsProviderResult(
            LyricsProviderStatus.NO_RESULT,
            diagnostics=(f"Unison {context} reported no matching record",),
            raw_payload=b"",
        )
    if response.status == 429:
        seconds = _retry_after_seconds(response.retry_after)
        diagnostic = f"Unison {context} rate limit reached"
        if response.retry_after is not None and seconds is None:
            diagnostic += "; Retry-After was malformed or outside the safe bound"
        return LyricsProviderResult(
            LyricsProviderStatus.RATE_LIMITED,
            diagnostics=(diagnostic,),
            retry_after_seconds=seconds,
        )
    if 500 <= response.status <= 599:
        return LyricsProviderResult(
            LyricsProviderStatus.UNAVAILABLE,
            diagnostics=(f"Unison {context} server returned HTTP {response.status}",),
        )
    if response.status != 200:
        return LyricsProviderResult(
            LyricsProviderStatus.INVALID_RESPONSE,
            diagnostics=(
                f"Unison {context} returned unexpected HTTP {response.status}",
            ),
        )
    if not response.content_type.casefold().startswith("application/json"):
        return LyricsProviderResult(
            LyricsProviderStatus.INVALID_RESPONSE,
            diagnostics=(f"Unison {context} returned unexpected non-JSON content",),
        )
    return None


def _hydration_failure(
    failures: list[LyricsProviderResult],
) -> LyricsProviderResult:
    if any(item.status is LyricsProviderStatus.RATE_LIMITED for item in failures):
        retries = tuple(
            item.retry_after_seconds
            for item in failures
            if item.status is LyricsProviderStatus.RATE_LIMITED
            and item.retry_after_seconds is not None
        )
        return LyricsProviderResult(
            LyricsProviderStatus.RATE_LIMITED,
            retry_after_seconds=min(retries, default=None),
        )
    if any(item.status is LyricsProviderStatus.UNAVAILABLE for item in failures):
        return LyricsProviderResult(LyricsProviderStatus.UNAVAILABLE)
    if any(item.status is LyricsProviderStatus.INVALID_RESPONSE for item in failures):
        return LyricsProviderResult(LyricsProviderStatus.INVALID_RESPONSE)
    return LyricsProviderResult(LyricsProviderStatus.NO_RESULT, raw_payload=b"")


def _candidate(value: object) -> LyricsProviderCandidate:
    if not isinstance(value, dict):
        raise ValueError("record is not an object")
    record_id = _required_id(value.get("id"))
    track_name = _required_text(value, "song")
    artist_name = _required_text(value, "artist")
    album_name = _optional_text(value, "album")
    duration_ms = _duration_ms(value.get("duration"))
    lyrics = _required_text(value, "lyrics")
    lyric_format = _required_text(value, "format").casefold()
    sync_type = _optional_text(value, "syncType")
    parsed_lyrics = None
    if lyric_format == "plain" or sync_type == "plain":
        plain_lyrics = lyrics
        synced_lyrics = None
    elif lyric_format == "lrc":
        plain_lyrics = None
        synced_lyrics = lyrics
    elif lyric_format == "ttml":
        parsed_lyrics = parse_ttml_text(lyrics)
        if parsed_lyrics.status is LyricsTextParseStatus.INVALID:
            diagnostic = next(
                iter(parsed_lyrics.diagnostics), "TTML content was invalid"
            )
            raise ValueError(diagnostic)
        plain_lyrics = None
        synced_lyrics = lyrics
    else:
        raise ValueError(f"unsupported lyrics format {lyric_format!r}")
    return LyricsProviderCandidate(
        provider="Unison",
        record_id=record_id,
        track_name=track_name,
        artist_name=artist_name,
        album_name=album_name,
        duration_ms=duration_ms,
        instrumental=False,
        plain_lyrics=plain_lyrics,
        synced_lyrics=synced_lyrics,
        provider_confidence=_confidence(value.get("confidence")),
        parsed_lyrics=parsed_lyrics,
        language=_optional_text(value, "language"),
        provider_duration_matched=_duration_filter(value),
    )


def _required_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("record ID is missing or invalid")
    result = str(value).strip()
    if not result:
        raise ValueError("record ID is missing or invalid")
    return result


def _required_text(value: dict[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{field} is missing or invalid")
    return item


def _optional_text(value: dict[str, Any], field: str) -> str | None:
    item = value.get(field)
    if item is None:
        return None
    if not isinstance(item, str):
        raise ValueError(f"{field} is invalid")
    return item if item.strip() else None


def _duration_ms(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("duration is invalid")
    try:
        duration = Decimal(str(value))
        result = int((duration * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (DecimalException, OverflowError, ValueError) as error:
        raise ValueError("duration is invalid") from error
    if (
        not duration.is_finite()
        or duration <= 0
        or result > MAX_SEMANTIC_LYRICS_DURATION_MS
    ):
        raise ValueError("duration is invalid")
    return result


def _confidence(value: object) -> LyricsMatchConfidence:
    if not isinstance(value, str):
        raise ValueError("provider confidence is missing or invalid")
    try:
        return {
            "low": LyricsMatchConfidence.LOW,
            "medium": LyricsMatchConfidence.MEDIUM,
            "high": LyricsMatchConfidence.HIGH,
        }[value.casefold()]
    except KeyError as error:
        raise ValueError("provider confidence is invalid") from error


def _with_duration_filter(value: object, *, duration_filtered: bool) -> object:
    if not duration_filtered or not isinstance(value, dict):
        return value
    return {**value, "_konokashi_duration_filtered": True}


def _duration_filter(value: dict[str, Any]) -> bool:
    marker = value.get("_konokashi_duration_filtered", False)
    if type(marker) is not bool:
        raise ValueError("adapter duration-filter evidence is invalid")
    return marker
