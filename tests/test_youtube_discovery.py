"""URL-less browser discovery must be bounded and unable to invent a source ID."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event

from konokashi.application.resolve_track import with_discovered_web_metadata_candidates
from konokashi.domain.identity import GenericMprisIdentity, YouTubeIdentity
from konokashi.domain.lyrics import ProviderCacheEntry
from konokashi.domain.tracks import TrackCandidate
from konokashi.domain.youtube_metadata import YouTubeMetadataEnrichmentResult
from konokashi.infrastructure.metadata.youtube_discovery import (
    _PRINT_TEMPLATE,
    YtDlpYouTubeMediaDiscoverer,
)
from tests.stage2_helpers import resolver, snapshot

VIDEO_ID = "AbCdEfGh123"
NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)


class _Cache:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], ProviderCacheEntry] = {}
        self.puts: list[ProviderCacheEntry] = []

    def get(self, provider: str, cache_key: str) -> ProviderCacheEntry | None:
        return self.values.get((provider, cache_key))

    def put(self, entry: ProviderCacheEntry) -> None:
        self.values[(entry.provider, entry.cache_key)] = entry
        self.puts.append(entry)

    def delete(self, provider: str, cache_key: str) -> bool:
        return self.values.pop((provider, cache_key), None) is not None


def _track(*, artist: str = "Example Maker - Topic"):
    track_resolver, _repository = resolver()
    return track_resolver.resolve(
        snapshot(
            "chromium.instance9",
            title="FABLE - Glass Horizon",
            artists=(artist,),
            duration_us=133_641_000,
        )
    )


def _track_with_id(track_id: str):
    track = _track()
    return replace(
        track,
        source_identity=replace(track.source_identity, track_id=track_id),
        raw_snapshot=replace(
            track.raw_snapshot,
            metadata=replace(track.raw_snapshot.metadata, track_id=track_id),
        ),
    )


def _entry(
    video_id: str = VIDEO_ID,
    *,
    title: str = "FABLE - Glass Horizon",
    channel: str = "Example Maker",
    duration: float = 134,
) -> bytes:
    return "\t".join(
        json.dumps(value) for value in (video_id, title, channel, channel, duration)
    ).encode()


class _Enricher:
    def __init__(
        self, strategy: str = "youtube-enrichment:structured-music-fields"
    ) -> None:
        self.calls: list[tuple[object, bool, bool]] = []
        self.strategy = strategy

    def enrich(self, track, *, offline=False, refresh=False):  # type: ignore[no-untyped-def]
        self.calls.append((track, offline, refresh))
        return YouTubeMetadataEnrichmentResult(
            track.source_identity,
            (
                TrackCandidate(
                    "FABLE - Glass Horizon",
                    ("Example Maker", "Guest Artist"),
                    "Fictional Album",
                    134_000_000,
                    strategy=self.strategy,
                ),
            ),
            cache_hit=True,
        )


def test_unique_topic_search_supplies_hypotheses_but_not_a_source_identity() -> None:
    track = _track()
    calls: list[tuple[tuple[str, ...], float, int]] = []
    enricher = _Enricher()

    def command(argv, timeout, limit, _cancelled):  # type: ignore[no-untyped-def]
        calls.append((argv, timeout, limit))
        return b"\n".join((_entry(), _entry("ZyXwVuTs987", title="Different song")))

    discovered = YtDlpYouTubeMediaDiscoverer(
        enricher,
        command=command,  # type: ignore[arg-type]
    ).discover(track)

    assert len(calls) == 1
    argv, timeout, limit = calls[0]
    assert argv[argv.index("--print") + 1] == _PRINT_TEMPLATE
    assert argv[-1] == "ytsearch5:FABLE - Glass Horizon Example Maker"
    assert "--flat-playlist" in argv
    assert "--skip-download" in argv
    assert "--ignore-config" in argv
    assert "--no-cookies" in argv
    assert "--no-cookies-from-browser" in argv
    assert timeout <= 10 and limit <= 16_000
    assert len(enricher.calls) == 1
    assert isinstance(enricher.calls[0][0].source_identity, YouTubeIdentity)
    assert enricher.calls[0][0].source_identity.video_id == VIDEO_ID
    assert discovered.cache_hit is True
    assert discovered.network_used is True
    assert "URL unconfirmed" in discovered.candidates[0].evidence[-1]
    enriched_track = with_discovered_web_metadata_candidates(
        track, discovered.candidates
    )
    assert isinstance(enriched_track.source_identity, GenericMprisIdentity)
    assert enriched_track.source_identity == track.source_identity
    assert enriched_track.raw_snapshot == track.raw_snapshot
    assert enriched_track.interpretation_candidates[-1].title == (
        "FABLE - Glass Horizon"
    )


def test_multiple_exact_videos_or_conflicting_metadata_do_not_enrich() -> None:
    track = _track()
    enricher = _Enricher()
    responses = (
        b"\n".join((_entry(), _entry("ZyXwVuTs987"))),
        _entry(channel="Other Maker"),
        _entry(duration=149),
        _entry(title="FABLE - Another Horizon"),
    )
    for payload in responses:
        discoverer = YtDlpYouTubeMediaDiscoverer(
            enricher,
            command=lambda *_args, payload=payload: payload,  # type: ignore[arg-type]
        )
        result = discoverer.discover(track)
        assert result.candidates == ()
        assert result.network_used is True
    assert enricher.calls == []


def test_exact_browser_track_reuses_only_short_lived_session_hypotheses() -> None:
    clock = [0.0]
    command_calls = 0
    enricher = _Enricher()

    def command(*_args):  # type: ignore[no-untyped-def]
        nonlocal command_calls
        command_calls += 1
        return _entry()

    discoverer = YtDlpYouTubeMediaDiscoverer(
        enricher,
        command=command,  # type: ignore[arg-type]
        clock=lambda: clock[0],
    )
    track = _track_with_id("/org/mpris/MediaPlayer2/TrackList/Track1")
    first = discoverer.discover(track)
    repeat = discoverer.discover(track)
    offline_repeat = discoverer.discover(track, offline=True)

    assert first.network_used is True
    assert repeat.candidates == first.candidates == offline_repeat.candidates
    assert repeat.cache_hit is True
    assert repeat.network_used is False
    assert offline_repeat.network_used is False
    assert command_calls == len(enricher.calls) == 1

    refreshed = discoverer.discover(track, refresh=True)
    assert refreshed.network_used is True
    assert command_calls == len(enricher.calls) == 2

    distinct = discoverer.discover(
        _track_with_id("/org/mpris/MediaPlayer2/TrackList/Track2")
    )
    assert distinct.network_used is True
    assert command_calls == len(enricher.calls) == 3

    clock[0] = 601.0
    expired = discoverer.discover(track)
    assert expired.network_used is True
    assert command_calls == len(enricher.calls) == 4


def test_exact_discovery_is_reused_across_fresh_sessions_without_search() -> None:
    cache = _Cache()
    command_calls = 0
    enricher = _Enricher()

    def command(*_args):  # type: ignore[no-untyped-def]
        nonlocal command_calls
        command_calls += 1
        return _entry()

    first = YtDlpYouTubeMediaDiscoverer(
        enricher,
        cache=cache,
        now=lambda: NOW,
        command=command,  # type: ignore[arg-type]
    ).discover(_track_with_id("/org/mpris/MediaPlayer2/TrackList/Track1"))
    repeat = YtDlpYouTubeMediaDiscoverer(
        enricher,
        cache=cache,
        now=lambda: NOW,
        command=lambda *_args: (_ for _ in ()).throw(AssertionError("searched")),
    ).discover(_track_with_id("/org/mpris/MediaPlayer2/TrackList/Track99"))

    assert first.network_used is True
    assert repeat.candidates == first.candidates
    assert repeat.cache_hit is True
    assert repeat.network_used is False
    assert command_calls == 1
    assert len(enricher.calls) == 2
    assert len(cache.puts) == 1
    assert b"FABLE" not in cache.puts[0].payload
    assert b"Example Maker" not in cache.puts[0].payload


def test_ambiguous_or_failed_discovery_never_seeds_durable_cache() -> None:
    cache = _Cache()
    enricher = _Enricher()

    ambiguous = YtDlpYouTubeMediaDiscoverer(
        enricher,
        cache=cache,
        now=lambda: NOW,
        command=lambda *_args: b"\n".join((_entry(), _entry("ZyXwVuTs987"))),  # type: ignore[arg-type]
    ).discover(_track())
    failed = YtDlpYouTubeMediaDiscoverer(
        enricher,
        cache=cache,
        now=lambda: NOW,
        command=lambda *_args: b"malformed",
    ).discover(_track())

    assert ambiguous.candidates == failed.candidates == ()
    assert cache.puts == []
    assert enricher.calls == []


def test_malformed_durable_discovery_entry_fails_closed_and_is_replaced() -> None:
    cache = _Cache()
    enricher = _Enricher()
    YtDlpYouTubeMediaDiscoverer(
        enricher,
        cache=cache,
        now=lambda: NOW,
        command=lambda *_args: _entry(),  # type: ignore[arg-type]
    ).discover(_track_with_id("/track/1"))
    original = cache.puts[0]
    cache.values[(original.provider, original.cache_key)] = replace(
        original, payload=b'{"version":1,"video_id":"too-short"}'
    )
    searches = 0

    def command(*_args):  # type: ignore[no-untyped-def]
        nonlocal searches
        searches += 1
        return _entry()

    result = YtDlpYouTubeMediaDiscoverer(
        enricher,
        cache=cache,
        now=lambda: NOW,
        command=command,  # type: ignore[arg-type]
    ).discover(_track_with_id("/track/2"))

    assert result.candidates
    assert result.network_used is True
    assert searches == 1
    assert len(cache.puts) == 2


def test_expired_discovery_cache_is_online_miss_but_offline_fallback() -> None:
    cache = _Cache()
    enricher = _Enricher()
    seed = YtDlpYouTubeMediaDiscoverer(
        enricher,
        cache=cache,
        now=lambda: NOW,
        command=lambda *_args: _entry(),  # type: ignore[arg-type]
    )
    seed.discover(_track_with_id("/track/1"))

    online_calls = 0

    def online_command(*_args):  # type: ignore[no-untyped-def]
        nonlocal online_calls
        online_calls += 1
        return _entry()

    later = NOW + timedelta(days=4)
    online = YtDlpYouTubeMediaDiscoverer(
        enricher,
        cache=cache,
        now=lambda: later,
        command=online_command,  # type: ignore[arg-type]
    ).discover(_track_with_id("/track/2"))
    offline = YtDlpYouTubeMediaDiscoverer(
        enricher,
        cache=cache,
        now=lambda: later + timedelta(days=4),
        command=lambda *_args: (_ for _ in ()).throw(AssertionError("searched")),
    ).discover(_track_with_id("/track/3"), offline=True, refresh=True)

    assert online.network_used is True
    assert online_calls == 1
    assert offline.candidates
    assert offline.cache_hit is True
    assert offline.network_used is False
    assert "stale exact" in offline.diagnostics[0]


def test_browser_observation_without_track_id_is_not_cached() -> None:
    command_calls = 0

    def command(*_args):  # type: ignore[no-untyped-def]
        nonlocal command_calls
        command_calls += 1
        return _entry()

    discoverer = YtDlpYouTubeMediaDiscoverer(
        _Enricher(),
        command=command,  # type: ignore[arg-type]
    )
    discoverer.discover(_track())
    discoverer.discover(_track())
    assert command_calls == 2


def test_cancelled_discovery_does_not_seed_session_cache() -> None:
    command_calls = 0

    def command(*_args):  # type: ignore[no-untyped-def]
        nonlocal command_calls
        command_calls += 1
        return _entry()

    class CancellingEnricher(_Enricher):
        def enrich(self, track, *, offline=False, refresh=False):  # type: ignore[no-untyped-def]
            result = super().enrich(track, offline=offline, refresh=refresh)
            discoverer.cancel_inflight()
            return result

    discoverer = YtDlpYouTubeMediaDiscoverer(
        CancellingEnricher(),
        command=command,  # type: ignore[arg-type]
    )
    track = _track_with_id("/org/mpris/MediaPlayer2/TrackList/Track1")
    assert discoverer.discover(track).candidates == ()
    assert discoverer.discover(track).candidates == ()
    assert command_calls == 2


def test_title_only_video_metadata_cannot_corroborate_credits() -> None:
    enricher = _Enricher("youtube-enrichment:title-split")
    result = YtDlpYouTubeMediaDiscoverer(
        enricher,
        command=lambda *_args: _entry(),  # type: ignore[arg-type]
    ).discover(_track())

    assert len(enricher.calls) == 1
    assert result.candidates == ()
    assert result.network_used is True


def test_plain_official_channel_can_supply_the_same_exact_bounded_hint() -> None:
    calls = 0

    def command(*_args):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return _entry()

    discoverer = YtDlpYouTubeMediaDiscoverer(
        _Enricher(),
        command=command,  # type: ignore[arg-type]
    )

    result = discoverer.discover(_track(artist="Example Maker"))

    assert calls == 1
    assert len(result.candidates) == 1
    assert result.network_used is True
    assert "title, channel, and duration" in result.candidates[0].evidence[-1]


def test_invalid_channel_hint_or_offline_browser_does_not_search() -> None:
    calls = 0

    def command(*_args):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return _entry()

    discoverer = YtDlpYouTubeMediaDiscoverer(
        _Enricher(),
        command=command,  # type: ignore[arg-type]
    )
    assert discoverer.discover(_track(artist=" ")).candidates == ()
    assert discoverer.discover(_track(), offline=True).candidates == ()
    assert calls == 0


def test_malformed_search_and_cancellation_fail_closed() -> None:
    enricher = _Enricher()
    malformed = YtDlpYouTubeMediaDiscoverer(
        enricher,
        command=lambda *_args: b"not valid fields",  # type: ignore[arg-type]
    ).discover(_track())
    assert malformed.candidates == ()
    assert malformed.network_used is True

    def cancelled(_argv, _timeout, _limit, cancellation: Event):  # type: ignore[no-untyped-def]
        cancellation.set()
        return _entry()

    stopped = YtDlpYouTubeMediaDiscoverer(
        enricher,
        command=cancelled,  # type: ignore[arg-type]
    ).discover(_track())
    assert stopped.candidates == ()
    assert enricher.calls == []
