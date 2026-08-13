"""Local-first precedence, persistence, offline, refresh, and outcome tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from lyricflow.application.resolve_lyrics import LyricsResolver, provider_cache_key
from lyricflow.domain.identity import LocalFileIdentity, YouTubeIdentity
from lyricflow.domain.lyrics import (
    ApprovalState,
    ContentProvenance,
    LocalLyricsResult,
    LocalLyricsStatus,
    LyricDocument,
    LyricDocumentKind,
    LyricLine,
    LyricRepresentation,
    LyricsMatch,
    LyricsMatchConfidence,
    LyricsMatchDecision,
    LyricsProviderCandidate,
    LyricsProviderResult,
    LyricsProviderStatus,
    LyricsQuery,
    LyricsResolutionStatus,
    ProviderCacheEntry,
    RepresentationKind,
    TimingProvenance,
)
from lyricflow.domain.models import PlayerCapabilities, PlayerSnapshot, RawTrackMetadata
from lyricflow.domain.tracks import Confidence, ResolvedTrack, TrackCandidate
from lyricflow.infrastructure.lyrics.provider_documents import (
    ProviderLyricDocumentBuilder,
)
from lyricflow.infrastructure.storage.bootstrap import open_storage

NOW = datetime(2026, 8, 13, 14, tzinfo=UTC)


def _track(
    *,
    source: YouTubeIdentity | LocalFileIdentity | None = None,
    title: str | None = "Elevate (Radio Edit)",
    artists: tuple[str, ...] = ("Little Sis Nora & S3RL",),
    album: str | None = "Elevate",
    duration_us: int | None = 183_771_000,
) -> ResolvedTrack:
    identity = source or YouTubeIdentity("xa4WrgqI7q0")
    snapshot = PlayerSnapshot(
        "plasma-browser-integration",
        "org.mpris.MediaPlayer2.plasma-browser-integration",
        "Firefox",
        "firefox",
        "Playing",
        RawTrackMetadata(
            title=title,
            artists=artists,
            album=album,
            url="https://www.youtube.com/watch?v=xa4WrgqI7q0",
            duration_us=duration_us,
        ),
        1_000_000,
        PlayerCapabilities(),
    )
    return ResolvedTrack(
        snapshot,
        identity,
        TrackCandidate(title, artists, album, duration_us),
        Confidence.HIGH if title and artists else Confidence.LOW,
    )


def _candidate(
    record_id: str = "1",
    *,
    title: str = "Elevate (Radio Edit)",
    artist: str = "Little Sis Nora & S3RL",
    album: str | None = "Elevate",
    duration_ms: int | None = 183_771,
    plain: str | None = "First\nSecond",
    synced: str | None = "[00:01.00]First\n[00:02.00]Second",
    instrumental: bool = False,
) -> LyricsProviderCandidate:
    return LyricsProviderCandidate(
        "LRCLIB",
        record_id,
        title,
        artist,
        album,
        duration_ms,
        instrumental,
        plain,
        synced,
    )


class _FakeProvider:
    name = "LRCLIB"

    def __init__(
        self,
        *,
        exact: LyricsProviderResult | None = None,
        search: LyricsProviderResult | None = None,
        cached: dict[tuple[bytes, bool], LyricsProviderResult] | None = None,
    ) -> None:
        self.exact_result = exact or LyricsProviderResult(
            LyricsProviderStatus.NO_RESULT, raw_payload=b""
        )
        self.search_result = search or LyricsProviderResult(
            LyricsProviderStatus.NO_RESULT, raw_payload=b""
        )
        self.cached = cached or {}
        self.exact_queries: list[LyricsQuery] = []
        self.search_queries: list[LyricsQuery] = []

    def exact(self, query: LyricsQuery) -> LyricsProviderResult:
        self.exact_queries.append(query)
        return self.exact_result

    def search(self, query: LyricsQuery) -> LyricsProviderResult:
        self.search_queries.append(query)
        return self.search_result

    def parse_cached(self, payload: bytes, *, search: bool) -> LyricsProviderResult:
        return self.cached.get(
            (payload, search),
            LyricsProviderResult(
                LyricsProviderStatus.NO_RESULT,
                raw_payload=payload,
                diagnostics=("fake cached no result",),
            ),
        )


class _LocalSource:
    def __init__(self, result: LocalLyricsResult) -> None:
        self.result = result
        self.calls = 0

    def load(self, _track: ResolvedTrack) -> LocalLyricsResult:
        self.calls += 1
        return self.result


def _document(
    document_id: str,
    *,
    source: str = "manual",
    kind: LyricDocumentKind = LyricDocumentKind.PLAIN,
) -> LyricDocument:
    lines = ()
    representations = ()
    original_text = None
    if kind is not LyricDocumentKind.INSTRUMENTAL:
        lines = (
            LyricLine(
                f"{document_id}-line",
                "Correct lyric",
                1000 if kind is LyricDocumentKind.SYNCED else None,
                timing_provenance=(
                    TimingProvenance.PROVIDER
                    if kind is LyricDocumentKind.SYNCED
                    else None
                ),
            ),
        )
        representations = (
            LyricRepresentation(
                f"{document_id}:original",
                RepresentationKind.ORIGINAL,
                ContentProvenance.USER,
                ApprovalState.APPROVED,
                lines,
            ),
        )
        original_text = "Correct lyric"
    return LyricDocument(
        document_id,
        kind,
        source,
        original_text,
        None,
        ApprovalState.APPROVED,
        NOW,
        representations=representations,
    )


def _resolver(
    path: Path,
    provider: _FakeProvider,
    *,
    local_sources: tuple[_LocalSource, ...] = (),
) -> LyricsResolver:
    storage = open_storage(path)
    return LyricsResolver(
        local_sources=local_sources,
        provider=provider,
        provider_documents=ProviderLyricDocumentBuilder(),
        lyrics=storage.lyrics,
        matches=storage.lyrics_matches,
        provider_cache=storage.provider_cache,
        now=lambda: NOW,
        sleeper=lambda _seconds: None,
    )


def test_user_approved_match_outranks_local_and_network(tmp_path: Path) -> None:
    path = tmp_path / "approved.sqlite3"
    storage = open_storage(path)
    track = _track()
    approved = _document("approved")
    storage.lyrics.put(approved)
    storage.lyrics_matches.put(
        track.source_identity,
        LyricsMatch(
            approved.document_id,
            LyricsMatchDecision.APPROVED,
            ContentProvenance.USER,
            NOW,
            LyricsMatchConfidence.APPROVED,
            ("user selected this document",),
        ),
    )
    local = _LocalSource(
        LocalLyricsResult(
            LocalLyricsStatus.FOUND, "Local", _document("local", source="local")
        )
    )
    provider = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS, (_candidate(),), raw_payload=b"network"
        )
    )

    result = _resolver(path, provider, local_sources=(local,)).resolve(
        track, refresh=True
    )

    assert result.document == approved
    assert result.confidence is LyricsMatchConfidence.APPROVED
    assert result.cache_hit is True
    assert local.calls == 0
    assert provider.exact_queries == []


def test_exact_local_source_outranks_automatic_provider_cache(tmp_path: Path) -> None:
    path = tmp_path / "local.sqlite3"
    track = _track(source=LocalFileIdentity("/music/song.flac"))
    local_document = _document(
        "local-sidecar", source="local-sidecar", kind=LyricDocumentKind.SYNCED
    )
    local = _LocalSource(
        LocalLyricsResult(LocalLyricsStatus.FOUND, "Local sidecar LRC", local_document)
    )
    provider = _FakeProvider()

    result = _resolver(path, provider, local_sources=(local,)).resolve(track)

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert result.source_label == "Local sidecar LRC"
    assert result.network_used is False
    assert provider.exact_queries == []
    match = open_storage(path).lyrics_matches.get(track.source_identity)
    assert match is not None
    assert match.confidence is LyricsMatchConfidence.HIGH
    assert match.provenance is ContentProvenance.LOCAL


def test_high_exact_provider_result_persists_and_fresh_offline_process_reuses_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "offline.sqlite3"
    track = _track()
    network = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS, (_candidate(),), raw_payload=b"exact-record"
        )
    )

    first = _resolver(path, network).resolve(track)

    assert first.status is LyricsResolutionStatus.FOUND_TIMED
    assert first.network_used is True
    assert first.document is not None
    assert first.document.provider_record_id == "1"
    assert first.document.source_title == "Elevate (Radio Edit)"
    assert first.document.source_artist == "Little Sis Nora & S3RL"

    offline_provider = _FakeProvider()
    second = _resolver(path, offline_provider).resolve(track, offline=True)

    assert second.status is LyricsResolutionStatus.FOUND_TIMED
    assert second.document == first.document
    assert second.cache_hit is True
    assert second.network_used is False
    assert offline_provider.exact_queries == []


def test_missing_album_skips_exact_and_uses_search_without_fabrication(
    tmp_path: Path,
) -> None:
    path = tmp_path / "search.sqlite3"
    track = _track(
        title="Every Single Day",
        artists=("S3RL feat. JessKah",),
        album=None,
        duration_us=180_000_000,
    )
    provider = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate(
                    title="Every Single Day",
                    artist="S3RL ft. JessKah",
                    album=None,
                    duration_ms=180_400,
                ),
            ),
            raw_payload=b"search-record",
        )
    )

    result = _resolver(path, provider).resolve(track)

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert provider.exact_queries == []
    assert provider.search_queries[0].album is None
    assert any("exact LRCLIB lookup skipped" in item for item in result.diagnostics)


def test_low_or_medium_candidates_are_ambiguous_and_not_auto_attached(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ambiguous.sqlite3"
    track = _track()
    provider = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate(record_id="wrong-artist", artist="Someone Else"),
                _candidate(record_id="far", duration_ms=200_000),
            ),
            raw_payload=b"ambiguous",
        ),
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate(record_id="live", title="Elevate (Live)"),),
            raw_payload=b"search-ambiguous",
        ),
    )

    result = _resolver(path, provider).resolve(track)

    assert result.status is LyricsResolutionStatus.AMBIGUOUS
    assert len(result.alternatives) == 3
    assert open_storage(path).lyrics_matches.get(track.source_identity) is None


def test_tied_high_candidates_are_ambiguous_but_unique_closest_is_accepted(
    tmp_path: Path,
) -> None:
    track = _track(album=None)
    tied = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate("a", album=None, duration_ms=183_700),
                _candidate("b", album=None, duration_ms=183_700),
            ),
            raw_payload=b"tied",
        )
    )
    assert _resolver(tmp_path / "tied.sqlite3", tied).resolve(track).status is (
        LyricsResolutionStatus.AMBIGUOUS
    )

    closest = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate("a", album=None, duration_ms=183_700),
                _candidate("b", album=None, duration_ms=184_500),
            ),
            raw_payload=b"closest",
        )
    )
    accepted = _resolver(tmp_path / "closest.sqlite3", closest).resolve(track)
    assert accepted.status is LyricsResolutionStatus.FOUND_TIMED
    assert accepted.document is not None
    assert accepted.document.provider_record_id == "a"


def test_instrumental_provider_state_persists_without_fake_lines(
    tmp_path: Path,
) -> None:
    path = tmp_path / "instrumental.sqlite3"
    track = _track()
    provider = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate(
                    instrumental=True,
                    plain=None,
                    synced=None,
                ),
            ),
            raw_payload=b"instrumental",
        )
    )

    first = _resolver(path, provider).resolve(track)
    second = _resolver(path, _FakeProvider()).resolve(track, offline=True)

    assert first.status is LyricsResolutionStatus.INSTRUMENTAL
    assert first.document is not None
    assert first.document.representations == ()
    assert first.document.original_text is None
    assert second.status is LyricsResolutionStatus.INSTRUMENTAL
    assert second.network_used is False


def test_failed_refresh_preserves_previous_usable_provider_lyrics(
    tmp_path: Path,
) -> None:
    path = tmp_path / "refresh.sqlite3"
    track = _track()
    first_provider = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS, (_candidate(),), raw_payload=b"first"
        )
    )
    first = _resolver(path, first_provider).resolve(track)
    unavailable = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.UNAVAILABLE,
            diagnostics=("provider down",),
        )
    )

    refreshed = _resolver(path, unavailable).resolve(track, refresh=True)

    assert refreshed.document == first.document
    assert refreshed.status is LyricsResolutionStatus.FOUND_TIMED
    assert refreshed.network_used is True
    assert any(
        "previous usable lyrics were preserved" in item
        for item in refreshed.diagnostics
    )
    persisted = open_storage(path).lyrics_matches.get(track.source_identity)
    assert first.document is not None
    assert persisted is not None
    assert persisted.document_id == first.document.document_id


def test_provider_no_result_raw_cache_prevents_repeat_requests(tmp_path: Path) -> None:
    path = tmp_path / "no-result.sqlite3"
    track = _track()
    first_provider = _FakeProvider()
    first = _resolver(path, first_provider).resolve(track)

    cached_provider = _FakeProvider()
    second = _resolver(path, cached_provider).resolve(track)

    assert first.status is LyricsResolutionStatus.NO_RESULT
    assert second.status is LyricsResolutionStatus.NO_RESULT
    assert second.cache_hit is True
    assert second.network_used is False
    assert cached_provider.exact_queries == []
    assert cached_provider.search_queries == []


def test_explicitly_expired_provider_cache_is_not_reused(tmp_path: Path) -> None:
    path = tmp_path / "expired.sqlite3"
    track = _track()
    query = LyricsQuery(
        "Elevate (Radio Edit)", ("Little Sis Nora & S3RL",), "Elevate", 183_771
    )
    storage = open_storage(path)
    for search in (False, True):
        storage.provider_cache.put(
            ProviderCacheEntry(
                "LRCLIB",
                provider_cache_key("LRCLIB", query, search=search),
                b"stale",
                NOW - timedelta(days=2),
                NOW - timedelta(days=1),
            )
        )
    provider = _FakeProvider()

    result = _resolver(path, provider).resolve(track)

    assert result.status is LyricsResolutionStatus.NO_RESULT
    assert result.cache_hit is False
    assert result.network_used is True
    assert len(provider.exact_queries) == 1
    assert len(provider.search_queries) == 1


def test_offline_can_reassess_raw_cache_without_a_saved_active_match(
    tmp_path: Path,
) -> None:
    path = tmp_path / "raw-cache.sqlite3"
    track = _track()
    query = LyricsQuery(
        "Elevate (Radio Edit)", ("Little Sis Nora & S3RL",), "Elevate", 183_771
    )
    storage = open_storage(path)
    key = provider_cache_key("LRCLIB", query, search=False)
    storage.provider_cache.put(ProviderCacheEntry("LRCLIB", key, b"cached-high", NOW))
    cached_result = LyricsProviderResult(
        LyricsProviderStatus.RESULTS, (_candidate(),), raw_payload=b"cached-high"
    )
    provider = _FakeProvider(cached={(b"cached-high", False): cached_result})

    result = _resolver(path, provider).resolve(track, offline=True)

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert result.cache_hit is True
    assert result.network_used is False


def test_black_screen_low_confidence_track_never_searches_with_uploader(
    tmp_path: Path,
) -> None:
    track = _track(
        title="10 hours and 1 second of pure black screen!",
        artists=(),
        album=None,
        duration_us=None,
    )
    provider = _FakeProvider()

    result = _resolver(tmp_path / "black.sqlite3", provider).resolve(track)

    assert result.status is LyricsResolutionStatus.NO_RESULT
    assert provider.exact_queries == []
    assert provider.search_queries == []
    assert any("musical artist is missing" in item for item in result.diagnostics)


def test_rejected_document_is_not_automatically_reattached(tmp_path: Path) -> None:
    path = tmp_path / "rejected.sqlite3"
    track = _track()
    builder = ProviderLyricDocumentBuilder()
    candidate = _candidate()
    rejected_document, _ = builder.build(candidate, NOW)
    assert rejected_document is not None
    storage = open_storage(path)
    storage.lyrics.put(rejected_document)
    storage.lyrics_matches.put(
        track.source_identity,
        LyricsMatch(
            rejected_document.document_id,
            LyricsMatchDecision.REJECTED,
            ContentProvenance.USER,
            NOW,
            LyricsMatchConfidence.LOW,
            ("known wrong result",),
        ),
    )
    provider = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS, (candidate,), raw_payload=b"rejected"
        ),
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS, (candidate,), raw_payload=b"rejected-search"
        ),
    )

    result = _resolver(path, provider).resolve(track)

    assert result.status is LyricsResolutionStatus.NO_RESULT
    match = open_storage(path).lyrics_matches.get(track.source_identity)
    assert match is not None
    assert match.decision is LyricsMatchDecision.REJECTED


def test_plain_only_and_synced_invalid_plain_fallback_are_untimed(
    tmp_path: Path,
) -> None:
    for name, candidate in (
        ("plain", _candidate(synced=None)),
        ("fallback", _candidate(synced="[00:bad]bad", plain="First\nSecond")),
    ):
        provider = _FakeProvider(
            exact=LyricsProviderResult(
                LyricsProviderStatus.RESULTS,
                (candidate,),
                raw_payload=name.encode(),
            )
        )
        result = _resolver(tmp_path / f"{name}.sqlite3", provider).resolve(_track())
        assert result.status is LyricsResolutionStatus.FOUND_UNTIMED
        assert result.document is not None
        assert all(
            line.start_ms is None for line in result.document.representations[0].lines
        )


def test_rate_limit_and_invalid_local_are_distinct_states(tmp_path: Path) -> None:
    rate_limited = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RATE_LIMITED,
            retry_after_seconds=30,
            diagnostics=("slow down",),
        )
    )
    limited = _resolver(tmp_path / "limited.sqlite3", rate_limited).resolve(_track())
    assert limited.status is LyricsResolutionStatus.RATE_LIMITED
    assert limited.retry_after_seconds == 30

    invalid_local = _LocalSource(
        LocalLyricsResult(
            LocalLyricsStatus.INVALID,
            "Local sidecar LRC",
            diagnostics=("malformed timestamp",),
        )
    )
    invalid = _resolver(
        tmp_path / "invalid-local.sqlite3",
        _FakeProvider(),
        local_sources=(invalid_local,),
    ).resolve(_track(artists=()), offline=True)
    assert invalid.status is LyricsResolutionStatus.INVALID_LOCAL_LYRICS
