"""Bounded public-video discovery for URL-less Topic-channel browser playback."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from threading import Event, Lock
from time import monotonic

from konokashi.application.resolve_track import is_url_less_browser
from konokashi.domain.identity import GenericMprisIdentity, YouTubeIdentity
from konokashi.domain.normalization import comparison_key, parse_topic_channel_label
from konokashi.domain.tracks import ResolvedTrack, TrackCandidate, semantic_duration_us
from konokashi.domain.youtube_metadata import YouTubeMetadataDiscoveryResult
from konokashi.infrastructure.metadata.youtube import (
    MetadataCommand,
    YtDlpYouTubeMetadataEnricher,
    _CommandFailure,
    _run_bounded_command,
)

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SEARCH_LIMIT = 5
_MAX_STDOUT_BYTES = 16_000
_TIMEOUT_SECONDS = 10.0
_PRINT_FIELDS = ("id", "title", "channel", "uploader", "duration")
_PRINT_TEMPLATE = "\t".join(f"%({field})j" for field in _PRINT_FIELDS)


def _hint(track: ResolvedTrack) -> tuple[str, str, int] | None:
    """Require browser provenance, a Topic label, and usable duration."""

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
    channel = parse_topic_channel_label(artists[0])
    if (
        channel is None
        or not 2 <= len(channel) <= 120
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


class YtDlpYouTubeMediaDiscoverer:
    """Find one exact public-video hint, then reuse the confirmed-ID enricher.

    Search output is never a source identity. The returned recording hypotheses
    stay attached to the original session-only browser observation.
    """

    def __init__(
        self,
        enricher: YtDlpYouTubeMetadataEnricher,
        *,
        command: MetadataCommand = _run_bounded_command,
        executable: str = "yt-dlp",
    ) -> None:
        self._enricher = enricher
        self._command = command
        self._executable = executable
        self._pending_lock = Lock()
        self._pending: set[Event] = set()

    def cancel_inflight(self) -> None:
        """Stop any active fields-only search after a source change."""

        with self._pending_lock:
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
                diagnostics=("URL-less browser metadata lacks a bounded Topic hint",)
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
        with self._pending_lock:
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
            with self._pending_lock:
                self._pending.discard(cancellation)
        elapsed_ms = max(0, round((monotonic() - started) * 1000))
        if len(matches) != 1:
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
        candidates: tuple[TrackCandidate, ...] = tuple(
            replace(
                item,
                evidence=(
                    *item.evidence,
                    "unique metadata-only public-video search corroborated "
                    "browser title, Topic channel, and duration; URL unconfirmed",
                ),
                field_provenance=(
                    *item.field_provenance,
                    ("video-search", "mpris-title+topic-channel+duration"),
                ),
            )
            for item in enrichment.candidates
            if item.strategy
            in {
                "youtube-enrichment:structured-music-fields",
                "youtube-enrichment:labelled-description",
            }
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
