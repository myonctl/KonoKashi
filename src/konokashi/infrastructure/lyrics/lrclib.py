"""Bounded read-only LRCLIB adapter using the official retrieval API contract."""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from threading import Lock
from typing import Any

import httpx

from konokashi import __version__
from konokashi.domain.lyrics import (
    MAX_SEMANTIC_LYRICS_DURATION_MS,
    LyricsProviderCandidate,
    LyricsProviderResult,
    LyricsProviderStatus,
    LyricsQuery,
)

DEFAULT_BASE_URL = "https://lrclib.net"
DEFAULT_USER_AGENT = f"KonoKashi/{__version__} (https://github.com/myonctl/KonoKashi)"
MAX_PROVIDER_RESPONSE_BYTES = 2_000_000
MAX_PROVIDER_RECORDS = 100
MAX_PROVIDER_DURATION_MS = MAX_SEMANTIC_LYRICS_DURATION_MS
MAX_RETRY_AFTER_SECONDS = 3_600


class LrclibLyricsProvider:
    """Translate LRCLIB exact/search responses into provider-neutral candidates."""

    name = "LRCLIB"

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
        """Use `/api/get` only when every official signature field is available."""

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
                    "exact LRCLIB lookup skipped; missing " + ", ".join(missing),
                ),
            )
        assert query.album is not None
        assert query.duration_ms is not None
        params: dict[str, str] = {
            "track_name": query.title,
            "artist_name": query.artist_name,
            "album_name": query.album,
            "duration": _duration_query(query.duration_ms),
        }
        return self._request("/api/get", params, search=False)

    def search(self, query: LyricsQuery) -> LyricsProviderResult:
        """Use field-based search only with a title and real musical artist."""

        if not query.title.strip() or (
            not query.broad and not query.artist_name.strip()
        ):
            return LyricsProviderResult(
                LyricsProviderStatus.NO_RESULT,
                diagnostics=(
                    "LRCLIB search skipped; a title is required and field-based "
                    "search also requires a musical artist",
                ),
            )
        if query.broad:
            # LRCLIB full-text search can surface a provider-native title from a
            # localized title plus artist. The application still scores every
            # result and requires independent artist, phonetic, and duration
            # evidence before accepting it.
            params = {"q": f"{query.title} {query.artist_name}".strip()}
        else:
            params = {"track_name": query.title, "artist_name": query.artist_name}
        if query.album and query.album.strip():
            params["album_name"] = query.album
        return self._request("/api/search", params, search=True)

    def parse_cached(self, payload: bytes, *, search: bool) -> LyricsProviderResult:
        """Parse adapter-owned raw bytes without performing any network access."""

        if not payload:
            return LyricsProviderResult(
                LyricsProviderStatus.NO_RESULT,
                raw_payload=payload,
                diagnostics=("provider cache records a previous no-result response",),
            )
        return self._parse_payload(payload, search=search)

    def _request(
        self, path: str, params: dict[str, str], *, search: bool
    ) -> LyricsProviderResult:
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
            with (
                client,
                client.stream(
                    "GET", f"{self._base_url}{path}", params=params
                ) as response,
            ):
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > MAX_PROVIDER_RESPONSE_BYTES:
                        return LyricsProviderResult(
                            LyricsProviderStatus.INVALID_RESPONSE,
                            diagnostics=(
                                "LRCLIB response exceeded the safe size limit",
                            ),
                        )
                raw = bytes(payload)
                status = response.status_code
                content_type = response.headers.get("content-type", "")
                retry_after = response.headers.get("retry-after")
        except httpx.TimeoutException:
            return LyricsProviderResult(
                LyricsProviderStatus.UNAVAILABLE,
                diagnostics=("LRCLIB request timed out",),
            )
        except httpx.TransportError as error:
            return LyricsProviderResult(
                LyricsProviderStatus.UNAVAILABLE,
                diagnostics=(f"LRCLIB network request failed: {error}",),
            )
        finally:
            with self._active_clients_lock:
                self._active_clients.discard(client)

        if status == 404:
            return LyricsProviderResult(
                LyricsProviderStatus.NO_RESULT,
                diagnostics=("LRCLIB reported no matching record",),
                raw_payload=b"",
            )
        if status == 429:
            seconds = _retry_after_seconds(retry_after)
            diagnostic = "LRCLIB rate limit reached"
            if retry_after is not None and seconds is None:
                diagnostic += "; Retry-After was malformed or outside the safe bound"
            return LyricsProviderResult(
                LyricsProviderStatus.RATE_LIMITED,
                diagnostics=(diagnostic,),
                retry_after_seconds=seconds,
            )
        if 500 <= status <= 599:
            return LyricsProviderResult(
                LyricsProviderStatus.UNAVAILABLE,
                diagnostics=(f"LRCLIB server returned HTTP {status}",),
            )
        if status != 200:
            return LyricsProviderResult(
                LyricsProviderStatus.INVALID_RESPONSE,
                diagnostics=(f"LRCLIB returned unexpected HTTP {status}",),
            )
        if not content_type.casefold().startswith("application/json"):
            return LyricsProviderResult(
                LyricsProviderStatus.INVALID_RESPONSE,
                diagnostics=("LRCLIB returned unexpected non-JSON content",),
            )
        return self._parse_payload(raw, search=search)

    def _parse_payload(self, payload: bytes, *, search: bool) -> LyricsProviderResult:
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, ValueError, RecursionError):
            return LyricsProviderResult(
                LyricsProviderStatus.INVALID_RESPONSE,
                diagnostics=("LRCLIB returned malformed JSON",),
            )
        records: list[object]
        if search:
            if not isinstance(decoded, list):
                return LyricsProviderResult(
                    LyricsProviderStatus.INVALID_RESPONSE,
                    diagnostics=("LRCLIB search response was not a result list",),
                )
            records = decoded
        else:
            if not isinstance(decoded, dict):
                return LyricsProviderResult(
                    LyricsProviderStatus.INVALID_RESPONSE,
                    diagnostics=("LRCLIB exact response was not a result object",),
                )
            records = [decoded]
        if len(records) > MAX_PROVIDER_RECORDS:
            return LyricsProviderResult(
                LyricsProviderStatus.INVALID_RESPONSE,
                diagnostics=("LRCLIB returned an unexpectedly large result set",),
            )
        if not records:
            return LyricsProviderResult(
                LyricsProviderStatus.NO_RESULT,
                diagnostics=("LRCLIB search returned no candidates",),
                raw_payload=payload,
            )
        candidates: list[LyricsProviderCandidate] = []
        diagnostics: list[str] = []
        for position, record in enumerate(records):
            try:
                candidates.append(_candidate(record))
            except ValueError as error:
                diagnostics.append(f"LRCLIB candidate {position + 1} ignored: {error}")
        if not candidates:
            return LyricsProviderResult(
                LyricsProviderStatus.INVALID_RESPONSE,
                diagnostics=tuple(diagnostics)
                or ("LRCLIB response had no valid record",),
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


def _candidate(value: object) -> LyricsProviderCandidate:
    if not isinstance(value, dict):
        raise ValueError("record is not an object")
    record_id_value = value.get("id")
    if isinstance(record_id_value, bool) or not isinstance(record_id_value, (int, str)):
        raise ValueError("record ID is missing or invalid")
    record_id = str(record_id_value).strip()
    if not record_id:
        raise ValueError("record ID is missing or invalid")
    track_name = _required_text(value, "trackName")
    artist_name = _required_text(value, "artistName")
    album_name = _optional_text(value, "albumName")
    instrumental = value.get("instrumental")
    if not isinstance(instrumental, bool):
        raise ValueError("instrumental flag is missing or invalid")
    duration_ms = _duration_ms(value.get("duration"))
    plain_lyrics = _optional_text(value, "plainLyrics")
    synced_lyrics = _optional_text(value, "syncedLyrics")
    if not instrumental and plain_lyrics is None and synced_lyrics is None:
        raise ValueError("record has neither lyrics nor instrumental state")
    return LyricsProviderCandidate(
        provider="LRCLIB",
        record_id=record_id,
        track_name=track_name,
        artist_name=artist_name,
        album_name=album_name,
        duration_ms=duration_ms,
        instrumental=instrumental,
        plain_lyrics=plain_lyrics,
        synced_lyrics=synced_lyrics,
    )


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
        milliseconds = duration * 1000
        result = int(milliseconds.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (DecimalException, OverflowError, ValueError) as error:
        raise ValueError("duration is invalid") from error
    if not duration.is_finite() or duration <= 0:
        raise ValueError("duration is invalid")
    if result > MAX_PROVIDER_DURATION_MS:
        raise ValueError("duration is invalid")
    return result
