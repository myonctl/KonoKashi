"""Metadata-only yt-dlp enrichment for confirmed YouTube video identities."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from tempfile import TemporaryFile
from threading import Event, Lock
from time import monotonic
from typing import Any

from konokashi.application.ports import ProviderCacheRepositoryPort
from konokashi.domain.identity import YouTubeIdentity
from konokashi.domain.lyrics import ProviderCacheEntry
from konokashi.domain.normalization import (
    parse_artist_credits,
    parse_youtube_title_candidates,
)
from konokashi.domain.tracks import ArtistCredit, ResolvedTrack, TrackCandidate
from konokashi.domain.youtube_metadata import YouTubeMetadataEnrichmentResult

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_LABEL = re.compile(
    r"^(?P<label>artist|title|song|track|music|music\s*&\s*lyrics)\s*:\s*"
    r"(?P<value>\S.+)$",
    re.IGNORECASE,
)
_COMBINED = re.compile(r"\s(?:-|/|\||:|·)\s")
_CACHE_PROVIDER = "YouTube metadata"
_CACHE_TTL = timedelta(days=3)
_MAX_STDOUT_BYTES = 512 * 1024
_MAX_DESCRIPTION_CHARS = 64 * 1024
_MAX_DESCRIPTION_LINES = 200
_MAX_FIELD_CHARS = 300
_MAX_CACHED_TEXT_CHARS = 512
_TIMEOUT_SECONDS = 12.0

MetadataCommand = Callable[[tuple[str, ...], float, int, Event], bytes]


class _CommandFailure(RuntimeError):
    pass


def _run_bounded_command(
    argv: tuple[str, ...],
    timeout: float,
    max_stdout_bytes: int,
    cancelled: Event,
) -> bytes:
    """Run one argv-only command with bounded accepted stdout and no shell."""

    with TemporaryFile() as stdout_file, TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            shell=False,
            close_fds=True,
        )
        deadline = monotonic() + timeout
        while True:
            if cancelled.is_set():
                process.kill()
                process.wait()
                raise _CommandFailure("yt-dlp metadata request was cancelled")
            remaining = deadline - monotonic()
            if remaining <= 0:
                process.kill()
                process.wait()
                raise _CommandFailure(
                    f"yt-dlp metadata request exceeded {timeout:g} seconds"
                )
            try:
                return_code = process.wait(timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        stdout_file.seek(0, 2)
        size = stdout_file.tell()
        if size > max_stdout_bytes:
            raise _CommandFailure(
                f"yt-dlp metadata response exceeded {max_stdout_bytes} bytes"
            )
        if return_code != 0:
            stderr_file.seek(0)
            detail = stderr_file.read(2_048).decode("utf-8", "replace").strip()
            raise _CommandFailure(
                f"yt-dlp metadata request failed with exit {return_code}"
                + (f": {detail}" if detail else "")
            )
        stdout_file.seek(0)
        return stdout_file.read(max_stdout_bytes + 1)


class YtDlpYouTubeMetadataEnricher:
    """Extract conservative recording fields without downloading YouTube media."""

    def __init__(
        self,
        cache: ProviderCacheRepositoryPort,
        *,
        now: Callable[[], datetime] | None = None,
        command: MetadataCommand = _run_bounded_command,
        executable: str = "yt-dlp",
    ) -> None:
        self._cache = cache
        self._now = now or (lambda: datetime.now(UTC))
        self._command = command
        self._executable = executable
        self._pending_lock = Lock()
        self._pending: set[Event] = set()

    def cancel_inflight(self) -> None:
        """Request cancellation of every owned metadata-only subprocess."""

        with self._pending_lock:
            pending = tuple(self._pending)
        for cancellation in pending:
            cancellation.set()

    def enrich(
        self,
        track: ResolvedTrack,
        *,
        offline: bool = False,
        refresh: bool = False,
    ) -> YouTubeMetadataEnrichmentResult:
        """Return sanitized candidates for the exact confirmed video identity."""

        identity = track.source_identity
        if not isinstance(identity, YouTubeIdentity) or not _VIDEO_ID.fullmatch(
            identity.video_id
        ):
            raise ValueError("YouTube enrichment requires a valid stable video ID")
        cache_key = _cache_key(identity.video_id)
        if not refresh:
            cached = self._cache.get(_CACHE_PROVIDER, cache_key)
            if (
                cached is not None
                and cached.expires_at is not None
                and cached.expires_at > self._now()
            ):
                try:
                    candidates = _parse_sanitized_cache(cached.payload)
                except (json.JSONDecodeError, ValueError, TypeError):
                    candidates = ()
                if candidates:
                    return YouTubeMetadataEnrichmentResult(
                        identity,
                        candidates,
                        ("used current sanitized YouTube metadata cache",),
                        cache_hit=True,
                    )
        if offline:
            return YouTubeMetadataEnrichmentResult(
                identity,
                diagnostics=(
                    "offline mode: YouTube metadata enrichment was not contacted",
                ),
            )

        url = f"https://www.youtube.com/watch?v={identity.video_id}"
        argv = (
            self._executable,
            "--ignore-config",
            "--skip-download",
            "--dump-single-json",
            "--no-playlist",
            "--no-warnings",
            "--no-progress",
            "--no-netrc",
            url,
        )
        cancellation = Event()
        with self._pending_lock:
            self._pending.add(cancellation)
        try:
            payload = self._command(
                argv,
                _TIMEOUT_SECONDS,
                _MAX_STDOUT_BYTES,
                cancellation,
            )
            if cancellation.is_set():
                raise _CommandFailure("yt-dlp metadata request was cancelled")
            candidates = _extract_payload(payload, track, identity)
        except FileNotFoundError:
            return YouTubeMetadataEnrichmentResult(
                identity,
                diagnostics=(
                    "optional yt-dlp executable is unavailable; normal metadata "
                    "resolution remains active",
                ),
                network_used=False,
            )
        except (OSError, ValueError, _CommandFailure, json.JSONDecodeError) as error:
            return YouTubeMetadataEnrichmentResult(
                identity,
                diagnostics=(f"YouTube metadata enrichment failed safely: {error}",),
                network_used=True,
            )
        finally:
            with self._pending_lock:
                self._pending.discard(cancellation)
        if not candidates:
            return YouTubeMetadataEnrichmentResult(
                identity,
                diagnostics=(
                    "YouTube metadata contained no conservative recording-shaped "
                    "evidence",
                ),
                network_used=True,
            )
        retrieved_at = self._now()
        self._cache.put(
            ProviderCacheEntry(
                _CACHE_PROVIDER,
                cache_key,
                _sanitized_payload(candidates),
                retrieved_at,
                retrieved_at + _CACHE_TTL,
            )
        )
        return YouTubeMetadataEnrichmentResult(
            identity,
            candidates,
            (
                "contacted YouTube for metadata of the current public video only",
                "description was used only for bounded recording-field extraction "
                "and was not retained",
            ),
            network_used=True,
        )


def _extract_payload(
    payload: bytes,
    track: ResolvedTrack,
    identity: YouTubeIdentity,
) -> tuple[TrackCandidate, ...]:
    if len(payload) > _MAX_STDOUT_BYTES:
        raise ValueError("yt-dlp metadata response exceeds the accepted size")
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("id") != identity.video_id:
        raise ValueError("yt-dlp metadata identity does not match the requested video")
    title = _bounded_string(value.get("title"), _MAX_FIELD_CHARS)
    uploader = _bounded_string(value.get("uploader"), _MAX_FIELD_CHARS)
    description = _bounded_string(value.get("description"), _MAX_DESCRIPTION_CHARS)
    duration_us = _duration_us(value.get("duration"), track.candidate.duration_us)
    corroboration = tuple(
        item
        for item in (
            *(track.raw_snapshot.metadata.artists or ()),
            *(
                track.candidate.artist_credit.main_artists
                if track.candidate.artist_credit
                else ()
            ),
        )
        if item.strip()
    )
    candidates: list[TrackCandidate] = []
    if title is not None:
        candidates.extend(
            _with_enrichment_context(
                item,
                duration_us,
                "youtube_title",
                uploader,
            )
            for item in parse_youtube_title_candidates(title, corroboration)
        )
    if description is not None:
        candidates.extend(
            _description_candidates(
                description,
                corroboration,
                duration_us,
                uploader,
            )
        )
    return _deduplicate_candidates(candidates)


def _description_candidates(
    description: str,
    corroboration: tuple[str, ...],
    duration_us: int | None,
    uploader: str | None,
) -> tuple[TrackCandidate, ...]:
    artist_values: list[str] = []
    title_values: list[str] = []
    combined_lines: list[str] = []
    for raw_line in description.splitlines()[:_MAX_DESCRIPTION_LINES]:
        line = raw_line.strip()
        if not line or len(line) > _MAX_FIELD_CHARS:
            continue
        labelled = _LABEL.fullmatch(line)
        if labelled is not None:
            label = re.sub(r"\s+", " ", labelled.group("label").casefold())
            value = labelled.group("value").strip()
            if label in {"title", "song", "track"}:
                title_values.append(value)
            else:
                artist_values.append(value)
            continue
        if _COMBINED.search(line):
            combined_lines.append(line)

    candidates: list[TrackCandidate] = []
    evidence_artists = tuple((*corroboration, *artist_values))
    for line in combined_lines:
        candidates.extend(
            _with_enrichment_context(
                item,
                duration_us,
                "youtube_description",
                uploader,
            )
            for item in parse_youtube_title_candidates(line, evidence_artists)
        )
    credit = parse_artist_credits(tuple(artist_values))
    for title in title_values:
        if not credit.main_artists:
            continue
        candidates.append(
            TrackCandidate(
                title=title,
                artists=(" & ".join(credit.main_artists),),
                album=None,
                duration_us=duration_us,
                evidence=(
                    "paired explicit artist/credit and title fields from "
                    "YouTube description",
                    *(
                        (f"preserved YouTube uploader evidence: {uploader}",)
                        if uploader
                        else ()
                    ),
                ),
                strategy="youtube-enrichment:labelled-description",
                artist_credit=credit,
                field_provenance=(
                    ("title", "youtube_description"),
                    ("artists", "youtube_credit"),
                    *(
                        (("contributors", "youtube_credit"),)
                        if credit.contributors
                        else ()
                    ),
                    *((("uploader", "youtube_uploader"),) if uploader else ()),
                    *((("duration", "youtube_metadata"),) if duration_us else ()),
                ),
            )
        )
    return _deduplicate_candidates(candidates)


def _with_enrichment_context(
    candidate: TrackCandidate,
    duration_us: int | None,
    source: str,
    uploader: str | None,
) -> TrackCandidate:
    provenance = tuple(
        (field, source if origin == "mpris-title" else origin)
        for field, origin in candidate.field_provenance
    )
    return TrackCandidate(
        candidate.title,
        candidate.artists,
        None,
        duration_us,
        (
            *candidate.evidence,
            *(
                (f"preserved YouTube uploader evidence: {uploader}",)
                if uploader
                else ()
            ),
        ),
        candidate.transformations,
        f"youtube-enrichment:{source}:{candidate.strategy}",
        candidate.artist_credit,
        (
            *provenance,
            *((("uploader", "youtube_uploader"),) if uploader else ()),
            *((("duration", "youtube_metadata"),) if duration_us else ()),
        ),
    )


def _duration_us(value: Any, fallback: int | None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    if not 0 < value <= 24 * 60 * 60:
        return fallback
    return round(float(value) * 1_000_000)


def _bounded_string(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:limit] if cleaned else None


def _deduplicate_candidates(
    candidates: list[TrackCandidate],
) -> tuple[TrackCandidate, ...]:
    result: list[TrackCandidate] = []
    seen: set[tuple[object, ...]] = set()
    for candidate in candidates:
        credit = candidate.artist_credit or ArtistCredit()
        key = (
            candidate.title,
            credit.main_artists,
            credit.contributors,
            candidate.duration_us,
        )
        if candidate.title and credit.main_artists and key not in seen:
            seen.add(key)
            result.append(candidate)
    return tuple(result[:12])


def _cache_key(video_id: str) -> str:
    return sha256(f"youtube-metadata:v1:{video_id}".encode()).hexdigest()


def _sanitized_payload(candidates: tuple[TrackCandidate, ...]) -> bytes:
    value = [
        {
            "title": item.title,
            "artists": item.artists,
            "duration_us": item.duration_us,
            "evidence": item.evidence,
            "transformations": item.transformations,
            "strategy": item.strategy,
            "main_artists": (
                () if item.artist_credit is None else item.artist_credit.main_artists
            ),
            "contributors": (
                () if item.artist_credit is None else item.artist_credit.contributors
            ),
            "field_provenance": item.field_provenance,
        }
        for item in candidates
    ]
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _parse_sanitized_cache(payload: bytes) -> tuple[TrackCandidate, ...]:
    if len(payload) > _MAX_STDOUT_BYTES:
        raise ValueError("invalid oversized YouTube metadata cache")
    value = json.loads(payload)
    if not isinstance(value, list) or len(value) > 12:
        raise ValueError("invalid sanitized YouTube metadata cache")
    candidates: list[TrackCandidate] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("invalid sanitized YouTube metadata cache")
        title = item.get("title")
        artists = item.get("artists")
        duration_us = item.get("duration_us")
        if (
            not isinstance(title, str)
            or len(title) > _MAX_FIELD_CHARS
            or not isinstance(duration_us, int | type(None))
            or isinstance(duration_us, bool)
        ):
            raise ValueError("invalid sanitized YouTube metadata candidate")
        artist_values = _cached_strings(artists)
        evidence = _cached_strings(item.get("evidence", ()))
        transformations = _cached_strings(item.get("transformations", ()))
        main_artists = _cached_strings(item.get("main_artists", ()))
        contributors = _cached_strings(item.get("contributors", ()))
        strategy = item.get("strategy", "youtube-enrichment:cache")
        if not isinstance(strategy, str) or len(strategy) > _MAX_CACHED_TEXT_CHARS:
            raise ValueError("invalid sanitized YouTube metadata strategy")
        provenance_value = item.get("field_provenance", ())
        if not isinstance(provenance_value, list):
            raise ValueError("invalid sanitized YouTube metadata provenance")
        provenance: list[tuple[str, str]] = []
        for pair in provenance_value:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not all(isinstance(part, str) for part in pair)
            ):
                raise ValueError("invalid sanitized YouTube metadata provenance")
            provenance.append((pair[0], pair[1]))
        candidates.append(
            TrackCandidate(
                title,
                artist_values,
                None,
                duration_us,
                evidence,
                transformations,
                strategy,
                ArtistCredit(main_artists, contributors),
                tuple(provenance),
            )
        )
    return _deduplicate_candidates(candidates)


def _cached_strings(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list | tuple)
        or len(value) > 32
        or not all(
            isinstance(item, str) and len(item) <= _MAX_CACHED_TEXT_CHARS
            for item in value
        )
    ):
        raise ValueError("invalid sanitized YouTube metadata text values")
    return tuple(value)
