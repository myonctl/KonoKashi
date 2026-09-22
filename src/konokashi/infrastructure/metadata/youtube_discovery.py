"""Bounded public-video discovery for URL-less browser playback."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Event, Lock
from time import monotonic

from konokashi.application.ports import (
    ProviderCacheRepositoryPort,
    YouTubeMetadataEnrichmentPort,
)
from konokashi.application.resolve_track import is_url_less_browser
from konokashi.domain.identity import GenericMprisIdentity, YouTubeIdentity
from konokashi.domain.lyrics import ProviderCacheEntry
from konokashi.domain.normalization import comparison_key, parse_topic_channel_label
from konokashi.domain.tracks import ResolvedTrack, TrackCandidate, semantic_duration_us
from konokashi.domain.youtube_metadata import YouTubeMetadataDiscoveryResult
from konokashi.infrastructure.metadata.youtube import (
    MetadataCommand,
    _CommandFailure,
    _run_bounded_command,
)

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SEARCH_LIMIT = 5
_MAX_STDOUT_BYTES = 16_000
_TIMEOUT_SECONDS = 10.0
_SESSION_CACHE_SECONDS = 600.0
_SESSION_CACHE_LIMIT = 16
_DISCOVERY_CACHE_PROVIDER = "YouTube public-video discovery"
_DISCOVERY_CACHE_TTL = timedelta(days=3)
_PRINT_FIELDS = ("id", "title", "channel", "uploader", "duration")
_PRINT_TEMPLATE = "\t".join(f"%({field})j" for field in _PRINT_FIELDS)
_CacheKey = tuple[GenericMprisIdentity, str, str, int]


def _hint(track: ResolvedTrack) -> tuple[str, str, int] | None:
    """Require browser provenance, one bounded channel label, and duration.

    A browser can report either a ``- Topic`` label or the plain name of an
    official artist channel.  The label is only a search hint: discovery still
    requires one unique public video whose full title, channel/uploader, and
    duration all exactly corroborate the MPRIS observation.
    """

    snapshot = track.raw_snapshot
    if not isinstance(
        track.source_identity, GenericMprisIdentity
    ) or not is_url_less_browser(snapshot):
        return None
    artists = snapshot.metadata.artists or ()
    title = (snapshot.metadata.title or "").strip()
    duration_us = semantic_duration_us(snapshot.metadata.duration_us)
    if len(artists) != 1 or not 3 <= len(title) <= 160 or duration_us is None:
        return None
    raw_channel = artists[0].strip()
    channel = parse_topic_channel_label(raw_channel) or raw_channel
    if (
        not 2 <= len(channel) <= 120
        or not comparison_key(title)
        or not comparison_key(channel)
    ):
        return None
    return title, channel, duration_us


def _search_matches(payload: bytes, hint: tuple[str, str, int]) -> tuple[str, ...]:
    if len(payload) > _MAX_STDOUT_BYTES:
        raise ValueError("public-video search exceeded its metadata limit")
    title, channel, duration_us = hint
    title_key = comparison_key(title)
    channel_key = comparison_key(channel)
    if not title_key or not channel_key:
        return ()
    matches: set[str] = set()
    lines = payload.decode("utf-8", "strict").splitlines()
    if len(lines) > _SEARCH_LIMIT:
        raise ValueError("public-video search returned more than five entries")
    for line in lines:
        fields = line.split("\t")
        if len(fields) != len(_PRINT_FIELDS):
            raise ValueError("public-video search returned malformed fields")
        video_id, found_title, found_channel, found_uploader, duration = (
            json.loads(field) if field != "NA" else None for field in fields
        )
        if (
            not isinstance(video_id, str)
            or not _VIDEO_ID.fullmatch(video_id)
            or not isinstance(found_title, str)
            or not isinstance(duration, (int, float))
            or isinstance(duration, bool)
        ):
            continue
        artist_labels = (found_channel, found_uploader)
        if (
            comparison_key(found_title) == title_key
            and any(
                isinstance(label, str) and comparison_key(label) == channel_key
                for label in artist_labels
            )
            and abs(round(duration * 1_000_000) - duration_us) <= 2_000_000
        ):
            matches.add(video_id)
    return tuple(sorted(matches))


def _cache_key(track: ResolvedTrack, hint: tuple[str, str, int]) -> _CacheKey | None:
    """Reuse only the identical session-scoped browser track observation."""

    identity = track.source_identity
    if not isinstance(identity, GenericMprisIdentity) or not identity.track_id:
        return None
    title, channel, duration_us = hint
    return (
        identity,
        comparison_key(title),
        comparison_key(channel),
        duration_us,
    )


def _durable_cache_key(hint: tuple[str, str, int]) -> str:
    """Hash the exact observation so the cache does not retain listening labels."""

    title, channel, duration_us = hint
    fingerprint = "\0".join(
        (
            "youtube-public-video-discovery:v1",
            comparison_key(title),
            comparison_key(channel),
            str(round(duration_us / 1_000_000)),
        )
    )
    return sha256(fingerprint.encode()).hexdigest()


def _cached_video_id(payload: bytes) -> str | None:
    """Parse only the bounded stable ID written by this adapter."""

    if len(payload) > 128:
        return None
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(value, dict) or set(value) != {"version", "video_id"}:
        return None
    video_id = value.get("video_id")
    if value.get("version") != 1 or not isinstance(video_id, str):
        return None
    return video_id if _VIDEO_ID.fullmatch(video_id) else None


def _cache_payload(video_id: str) -> bytes:
    return json.dumps(
        {"version": 1, "video_id": video_id}, separators=(",", ":")
    ).encode()


def _discovery_candidates(
    candidates: tuple[TrackCandidate, ...],
) -> tuple[TrackCandidate, ...]:
    """Keep only structured recording evidence and retain its discovery basis."""

    return tuple(
        replace(
            item,
            evidence=(
                *item.evidence,
                "unique metadata-only public-video search corroborated "
                "browser title, channel, and duration; URL unconfirmed",
            ),
            field_provenance=(
                *item.field_provenance,
                ("video-search", "mpris-title+channel+duration"),
            ),
        )
        for item in candidates
        if item.strategy
        in {
            "youtube-enrichment:structured-music-fields",
            "youtube-enrichment:labelled-description",
        }
    )


class YtDlpYouTubeMediaDiscoverer:
    """Find one exact public-video hint, then reuse the confirmed-ID enricher.

    Search output is never a source identity. The returned recording hypotheses
    stay attached to the original session-only browser observation.
    """

    def __init__(
        self,
        enricher: YouTubeMetadataEnrichmentPort,
        *,
        cache: ProviderCacheRepositoryPort | None = None,
        now: Callable[[], datetime] | None = None,
        command: MetadataCommand = _run_bounded_command,
        executable: str = "yt-dlp",
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._enricher = enricher
        self._cache = cache
        self._now = now or (lambda: datetime.now(UTC))
        self._command = command
        self._executable = executable
        self._clock = clock
        self._state_lock = Lock()
        self._pending: set[Event] = set()
        self._epoch = 0
        self._session_cache: OrderedDict[
            _CacheKey, tuple[float, tuple[TrackCandidate, ...]]
        ] = OrderedDict()

    def cancel_inflight(self) -> None:
        """Stop any active fields-only search after a source change."""

        with self._state_lock:
            self._epoch += 1
            pending = tuple(self._pending)
        for cancellation in pending:
            cancellation.set()

    def discover(
        self,
        track: ResolvedTrack,
        *,
        offline: bool = False,
        refresh: bool = False,
    ) -> YouTubeMetadataDiscoveryResult:
        hint = _hint(track)
        if hint is None:
            return YouTubeMetadataDiscoveryResult(
                diagnostics=(
                    "URL-less browser metadata lacks one bounded channel and "
                    "duration hint",
                )
            )
        key = _cache_key(track, hint)
        with self._state_lock:
            epoch = self._epoch
            if key is not None and refresh and not offline:
                self._session_cache.pop(key, None)
            if key is not None and (not refresh or offline):
                session_cached = self._session_cache.get(key)
                if session_cached is not None:
                    if self._clock() - session_cached[0] <= _SESSION_CACHE_SECONDS:
                        self._session_cache.move_to_end(key)
                        return YouTubeMetadataDiscoveryResult(
                            candidates=session_cached[1],
                            diagnostics=(
                                "session-only browser discovery cache hit; "
                                "source identity remains unconfirmed",
                            ),
                            cache_hit=True,
                        )
                    self._session_cache.pop(key, None)
        durable_key = _durable_cache_key(hint)
        if self._cache is not None and (not refresh or offline):
            durable_cached = self._cache.get(_DISCOVERY_CACHE_PROVIDER, durable_key)
            if durable_cached is not None and (
                offline
                or (
                    durable_cached.expires_at is not None
                    and durable_cached.expires_at > self._now()
                )
            ):
                video_id = _cached_video_id(durable_cached.payload)
                if video_id is not None:
                    enrichment = self._enricher.enrich(
                        replace(track, source_identity=YouTubeIdentity(video_id)),
                        offline=offline,
                    )
                    candidates = _discovery_candidates(enrichment.candidates)
                    with self._state_lock:
                        if epoch != self._epoch:
                            return YouTubeMetadataDiscoveryResult(
                                diagnostics=("public-video discovery was cancelled",),
                                network_used=enrichment.network_used,
                            )
                        if key is not None and candidates:
                            self._session_cache[key] = (self._clock(), candidates)
                            self._session_cache.move_to_end(key)
                            if len(self._session_cache) > _SESSION_CACHE_LIMIT:
                                self._session_cache.popitem(last=False)
                    if candidates:
                        stale = (
                            durable_cached.expires_at is None
                            or durable_cached.expires_at <= self._now()
                        )
                        return YouTubeMetadataDiscoveryResult(
                            candidates=candidates,
                            diagnostics=(
                                "offline mode: reused stale exact public-video "
                                "discovery"
                                if stale
                                else "reused current exact public-video discovery",
                                "source identity remains session-only",
                                *enrichment.diagnostics,
                            ),
                            cache_hit=True,
                            network_used=enrichment.network_used,
                        )
        if offline:
            return YouTubeMetadataDiscoveryResult(
                diagnostics=("offline mode: public-video discovery was not contacted",)
            )
        started = monotonic()
        title, channel, _duration_us = hint
        argv = (
            self._executable,
            "--ignore-config",
            "--flat-playlist",
            "--skip-download",
            "--print",
            _PRINT_TEMPLATE,
            "--no-warnings",
            "--no-progress",
            "--no-cookies",
            "--no-cookies-from-browser",
            f"ytsearch{_SEARCH_LIMIT}:{title} {channel}",
        )
        cancellation = Event()
        with self._state_lock:
            self._pending.add(cancellation)
        try:
            payload = self._command(
                argv, _TIMEOUT_SECONDS, _MAX_STDOUT_BYTES, cancellation
            )
            if cancellation.is_set():
                raise _CommandFailure("public-video search was cancelled")
            matches = _search_matches(payload, hint)
        except FileNotFoundError:
            return YouTubeMetadataDiscoveryResult(
                diagnostics=("optional yt-dlp executable is unavailable",)
            )
        except (OSError, ValueError, _CommandFailure, json.JSONDecodeError):
            return YouTubeMetadataDiscoveryResult(
                diagnostics=("public-video discovery failed safely",),
                network_used=True,
            )
        finally:
            with self._state_lock:
                self._pending.discard(cancellation)
        elapsed_ms = max(0, round((monotonic() - started) * 1000))
        if len(matches) != 1:
            if len(matches) > 1 and self._cache is not None:
                # A formerly unique observation is no longer safe to reuse.
                self._cache.delete(_DISCOVERY_CACHE_PROVIDER, durable_key)
            return YouTubeMetadataDiscoveryResult(
                diagnostics=(
                    "public-video search did not yield one unique exact "
                    f"title/channel/duration match; matches={len(matches)}; "
                    f"search={elapsed_ms} ms",
                ),
                network_used=True,
            )
        # The searched ID is an enrichment hint, never the playing source identity.
        enrichment_track = replace(track, source_identity=YouTubeIdentity(matches[0]))
        enrichment = self._enricher.enrich(
            enrichment_track, offline=False, refresh=refresh
        )
        candidates = _discovery_candidates(enrichment.candidates)
        with self._state_lock:
            if epoch != self._epoch:
                return YouTubeMetadataDiscoveryResult(
                    diagnostics=("public-video discovery was cancelled",),
                    network_used=True,
                )
            if key is not None and candidates:
                self._session_cache[key] = (self._clock(), candidates)
                self._session_cache.move_to_end(key)
                if len(self._session_cache) > _SESSION_CACHE_LIMIT:
                    self._session_cache.popitem(last=False)
        if self._cache is not None and candidates:
            retrieved_at = self._now()
            self._cache.put(
                ProviderCacheEntry(
                    _DISCOVERY_CACHE_PROVIDER,
                    durable_key,
                    _cache_payload(matches[0]),
                    retrieved_at,
                    retrieved_at + _DISCOVERY_CACHE_TTL,
                )
            )
        return YouTubeMetadataDiscoveryResult(
            candidates=candidates,
            diagnostics=(
                "public-video search found one exact metadata match; "
                "source identity remains session-only",
                f"public-video discovery search={elapsed_ms} ms",
                *enrichment.diagnostics,
            ),
            cache_hit=enrichment.cache_hit,
            network_used=True,
        )
