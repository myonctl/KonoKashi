"""URL-less browser discovery must be bounded and unable to invent a source ID."""

from __future__ import annotations

import json
from threading import Event

from konokashi.application.resolve_track import with_discovered_web_metadata_candidates
from konokashi.domain.identity import GenericMprisIdentity, YouTubeIdentity
from konokashi.domain.tracks import TrackCandidate
from konokashi.domain.youtube_metadata import YouTubeMetadataEnrichmentResult
from konokashi.infrastructure.metadata.youtube_discovery import (
    _PRINT_TEMPLATE,
    YtDlpYouTubeMediaDiscoverer,
)
from tests.stage2_helpers import resolver, snapshot

VIDEO_ID = "AbCdEfGh123"


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


def test_title_only_video_metadata_cannot_corroborate_credits() -> None:
    enricher = _Enricher("youtube-enrichment:title-split")
    result = YtDlpYouTubeMediaDiscoverer(
        enricher,
        command=lambda *_args: _entry(),  # type: ignore[arg-type]
    ).discover(_track())

    assert len(enricher.calls) == 1
    assert result.candidates == ()
    assert result.network_used is True


def test_non_topic_or_offline_browser_does_not_search() -> None:
    calls = 0

    def command(*_args):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return _entry()

    discoverer = YtDlpYouTubeMediaDiscoverer(
        _Enricher(),
        command=command,  # type: ignore[arg-type]
    )
    assert discoverer.discover(_track(artist="Example Maker")).candidates == ()
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
