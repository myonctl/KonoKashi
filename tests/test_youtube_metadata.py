"""Metadata-only YouTube enrichment safety and extraction regressions."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from threading import Event, Thread

import pytest

from konokashi.domain.lyrics import ProviderCacheEntry
from konokashi.infrastructure.metadata.youtube import (
    _PRINT_TEMPLATE,
    YtDlpYouTubeMetadataEnricher,
)
from tests.stage2_helpers import resolver, snapshot

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
VIDEO_ID = "dQw4w9WgXcQ"


class _Cache:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], ProviderCacheEntry] = {}
        self.puts: list[ProviderCacheEntry] = []

    def get(self, provider: str, cache_key: str) -> ProviderCacheEntry | None:
        return self.values.get((provider, cache_key))

    def put(self, entry: ProviderCacheEntry) -> None:
        self.values[(entry.provider, entry.cache_key)] = entry
        self.puts.append(entry)


def _android_track():  # type: ignore[no-untyped-def]
    track_resolver, _repository = resolver()
    return track_resolver.resolve(
        snapshot(
            "browser",
            title=("DECO*27 / Android Girl feat. Hatsune Miku【Official MV】"),
            artists=("DECO*27",),
            url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
            duration_us=215_441_000,
        )
    )


def test_metadata_only_command_and_native_description_extraction() -> None:
    cache = _Cache()
    calls: list[tuple[tuple[str, ...], float, int]] = []

    def command(
        argv: tuple[str, ...],
        timeout: float,
        limit: int,
        _cancelled: Event,
    ) -> bytes:
        calls.append((argv, timeout, limit))
        return json.dumps(
            {
                "id": VIDEO_ID,
                "title": ("DECO*27 / Android Girl feat. Hatsune Miku【Official MV】"),
                "uploader": "OTOIRO / DECO*27",
                "duration": 215.2,
                "description": (
                    "A normal sentence that must not become a candidate.\n"
                    "DECO*27 - アンドロイドガール feat. 初音ミク\n"
                    "Unrelated prose remains ephemeral."
                ),
                "thumbnail": "https://example.invalid/secret-thumbnail",
            },
            ensure_ascii=False,
        ).encode()

    result = YtDlpYouTubeMetadataEnricher(
        cache, now=lambda: NOW, command=command
    ).enrich(_android_track())

    assert result.network_used is True
    assert result.cache_hit is False
    assert calls
    argv, timeout, stdout_limit = calls[0]
    assert argv[0] == "yt-dlp"
    assert "--ignore-config" in argv
    assert "--skip-download" in argv
    assert "--print" in argv
    assert argv[argv.index("--print") + 1] == _PRINT_TEMPLATE
    assert "--no-cookies" in argv
    assert "--no-cookies-from-browser" in argv
    assert "--no-netrc" not in argv
    assert argv[-1] == f"https://www.youtube.com/watch?v={VIDEO_ID}"
    assert timeout <= 12
    assert stdout_limit <= 512 * 1024
    native = next(
        item for item in result.candidates if item.title == "アンドロイドガール"
    )
    assert native.artist_credit is not None
    assert native.artist_credit.main_artists == ("DECO*27",)
    assert native.artist_credit.contributors == ("初音ミク",)
    assert ("title", "youtube_description") in native.field_provenance
    assert native.duration_us == 215_200_000
    assert cache.puts[0].expires_at == NOW + timedelta(days=3)
    persisted = cache.puts[0].payload.decode()
    assert "Unrelated prose remains ephemeral" not in persisted
    assert "secret-thumbnail" not in persisted


def test_installed_yt_dlp_accepts_the_privacy_safe_metadata_flags() -> None:
    executable = shutil.which("yt-dlp")
    if executable is None:
        pytest.skip("optional yt-dlp executable is not installed")
    completed = subprocess.run(
        (
            executable,
            "--ignore-config",
            "--skip-download",
            "--print",
            _PRINT_TEMPLATE,
            "--no-playlist",
            "--no-warnings",
            "--no-progress",
            "--no-cookies",
            "--no-cookies-from-browser",
            "--help",
        ),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_fields_only_yt_dlp_output_parses_missing_values_without_full_json() -> None:
    cache = _Cache()

    def command(
        _argv: tuple[str, ...],
        _timeout: float,
        _limit: int,
        _cancelled: Event,
    ) -> bytes:
        fields = (
            json.dumps(VIDEO_ID),
            json.dumps("Luce sul mare (Official Video)"),
            json.dumps("Example Records"),
            "NA",
            json.dumps(198.0),
            json.dumps("Luce sul mare"),
            json.dumps("Cantante Fittizia"),
            "NA",
            "NA",
            json.dumps("Artist: Cantante Fittizia"),
        )
        return "\t".join(fields).encode()

    result = YtDlpYouTubeMetadataEnricher(
        cache, now=lambda: NOW, command=command
    ).enrich(_android_track())

    assert result.candidates
    assert result.candidates[0].title == "Luce sul mare"
    assert result.candidates[0].artists == ("Cantante Fittizia",)
    assert result.candidates[0].duration_us == 198_000_000
    assert result.network_used is True


def test_enrichment_reports_latency_without_exposing_metadata_values() -> None:
    clock = iter((10.0, 10.123))

    def command(
        _argv: tuple[str, ...],
        _timeout: float,
        _limit: int,
        _cancelled: Event,
    ) -> bytes:
        return json.dumps(
            {
                "id": VIDEO_ID,
                "track": "Private-looking title",
                "artist": "Private-looking artist",
            }
        ).encode()

    result = YtDlpYouTubeMetadataEnricher(
        _Cache(),
        now=lambda: NOW,
        command=command,
        monotonic_clock=lambda: next(clock),
    ).enrich(_android_track())

    assert result.candidates
    assert result.diagnostics[-1] == (
        "YouTube metadata: final decision in 123 ms; "
        "cache_hit=False; network_used=True; candidates=1"
    )
    assert "Private-looking" not in result.diagnostics[-1]


def test_cached_sanitized_metadata_avoids_second_command() -> None:
    cache = _Cache()
    calls = 0

    def command(
        _argv: tuple[str, ...],
        _timeout: float,
        _limit: int,
        _cancelled: Event,
    ) -> bytes:
        nonlocal calls
        calls += 1
        return json.dumps(
            {
                "id": VIDEO_ID,
                "description": "DECO*27 - アンドロイドガール feat. 初音ミク",
                "duration": 215.2,
            },
            ensure_ascii=False,
        ).encode()

    enricher = YtDlpYouTubeMetadataEnricher(cache, now=lambda: NOW, command=command)
    first = enricher.enrich(_android_track())
    second = enricher.enrich(_android_track())

    assert first.candidates
    assert second.candidates == first.candidates
    assert second.cache_hit is True
    assert second.network_used is False
    assert calls == 1


def test_offline_refresh_can_use_expired_sanitized_metadata_without_network() -> None:
    cache = _Cache()
    calls = 0
    current = [NOW]

    def command(
        _argv: tuple[str, ...],
        _timeout: float,
        _limit: int,
        _cancelled: Event,
    ) -> bytes:
        nonlocal calls
        calls += 1
        return json.dumps(
            {
                "id": VIDEO_ID,
                "track": "Luce sul mare",
                "artist": "Cantante Fittizia",
                "duration": 198.0,
            }
        ).encode()

    enricher = YtDlpYouTubeMetadataEnricher(
        cache, now=lambda: current[0], command=command
    )
    first = enricher.enrich(_android_track())
    current[0] += timedelta(days=4)
    offline = enricher.enrich(_android_track(), offline=True, refresh=True)

    assert first.candidates
    assert offline.candidates == first.candidates
    assert offline.cache_hit is True
    assert offline.network_used is False
    assert "stale sanitized" in offline.diagnostics[0]
    assert calls == 1


def test_explicit_music_fields_survive_cache_without_treating_uploader_as_artist() -> (
    None
):
    cache = _Cache()

    def command(
        _argv: tuple[str, ...],
        _timeout: float,
        _limit: int,
        _cancelled: Event,
    ) -> bytes:
        return json.dumps(
            {
                "id": VIDEO_ID,
                "title": "Luce sul mare - YouTube",
                "uploader": "Example Records",
                "track": "Luce sul mare",
                "artists": ["Cantante Fittizia"],
                "album": "Album di prova",
                "duration": 198.0,
            }
        ).encode()

    enricher = YtDlpYouTubeMetadataEnricher(cache, now=lambda: NOW, command=command)
    first = enricher.enrich(_android_track())
    second = enricher.enrich(_android_track())

    assert first.candidates
    assert first.candidates[0].title == "Luce sul mare"
    assert first.candidates[0].artists == ("Cantante Fittizia",)
    assert first.candidates[0].album == "Album di prova"
    assert first.candidates[0].field_provenance[:3] == (
        ("title", "youtube_track"),
        ("artists", "youtube_artists"),
        ("album", "youtube_album"),
    )
    assert second.candidates == first.candidates
    assert second.cache_hit and not second.network_used
    assert b"Example Records" in cache.puts[0].payload


def test_missing_tool_identity_mismatch_and_offline_fail_safely() -> None:
    cache = _Cache()

    def missing(
        _argv: tuple[str, ...],
        _timeout: float,
        _limit: int,
        _cancelled: Event,
    ) -> bytes:
        raise FileNotFoundError("yt-dlp")

    unavailable = YtDlpYouTubeMetadataEnricher(cache, command=missing).enrich(
        _android_track()
    )
    assert unavailable.candidates == ()
    assert unavailable.network_used is False
    assert "unavailable" in unavailable.diagnostics[0]

    calls = 0

    def wrong_identity(
        _argv: tuple[str, ...],
        _timeout: float,
        _limit: int,
        _cancelled: Event,
    ) -> bytes:
        nonlocal calls
        calls += 1
        return b'{"id":"aaaaaaaaaaa","title":"Artist - Song"}'

    enricher = YtDlpYouTubeMetadataEnricher(cache, command=wrong_identity)
    rejected = enricher.enrich(_android_track())
    offline = enricher.enrich(_android_track(), offline=True)
    assert rejected.candidates == ()
    assert "does not match" in rejected.diagnostics[0]
    assert offline.candidates == ()
    assert offline.network_used is False
    assert calls == 1


def test_latin_title_can_expose_bounded_cyrillic_recording_variant() -> None:
    track_resolver, _repository = resolver()
    track = track_resolver.resolve(
        snapshot(
            "browser",
            title="Lida, S3RL - Ali Uli",
            artists=("Lida",),
            url=f"https://youtu.be/{VIDEO_ID}",
            duration_us=178_000_000,
        )
    )

    def command(
        _argv: tuple[str, ...],
        _timeout: float,
        _limit: int,
        _cancelled: Event,
    ) -> bytes:
        return json.dumps(
            {
                "id": VIDEO_ID,
                "title": "Lida, S3RL - Ali Uli",
                "uploader": "Lida",
                "duration": 178,
                "description": (
                    "Lida, S3RL - Али Ули [Премьера альбома]\n"
                    "This prose is not metadata."
                ),
            },
            ensure_ascii=False,
        ).encode()

    result = YtDlpYouTubeMetadataEnricher(_Cache(), command=command).enrich(track)

    native = [item for item in result.candidates if item.title == "Али Ули"]
    assert len(native) == 1
    assert native[0].artist_credit is not None
    assert native[0].artist_credit.main_artists == ("Lida", "S3RL")
    assert native[0].strategy.endswith("optional-trailing-group")


def test_inflight_enrichment_cancellation_discards_completion() -> None:
    started = Event()

    def command(
        _argv: tuple[str, ...],
        _timeout: float,
        _limit: int,
        cancelled: Event,
    ) -> bytes:
        started.set()
        assert cancelled.wait(1)
        return json.dumps({"id": VIDEO_ID, "description": "Artist - Song"}).encode()

    enricher = YtDlpYouTubeMetadataEnricher(_Cache(), command=command)
    results = []
    worker = Thread(target=lambda: results.append(enricher.enrich(_android_track())))
    worker.start()
    assert started.wait(1)
    enricher.cancel_inflight()
    worker.join(1)

    assert not worker.is_alive()
    assert len(results) == 1
    assert results[0].candidates == ()
    assert "cancelled" in results[0].diagnostics[0]
