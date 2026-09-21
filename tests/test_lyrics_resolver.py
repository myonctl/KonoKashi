"""Local-first precedence, persistence, offline, refresh, and outcome tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event, Thread
from time import monotonic, sleep

from konokashi.application.resolve_lyrics import (
    LyricsResolver,
    LyricsSearchRequest,
    _candidate_search_plan,
    provider_cache_key,
)
from konokashi.application.resolve_track import with_youtube_metadata_candidates
from konokashi.domain.identity import LocalFileIdentity, YouTubeIdentity
from konokashi.domain.lyrics import (
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
    LyricsResolutionResult,
    LyricsResolutionStatus,
    ProviderCacheEntry,
    RepresentationKind,
    TimingProvenance,
)
from konokashi.domain.models import PlayerCapabilities, PlayerSnapshot, RawTrackMetadata
from konokashi.domain.tracks import (
    ArtistCredit,
    Confidence,
    ResolvedTrack,
    TrackCandidate,
)
from konokashi.infrastructure.lyrics.provider_documents import (
    ProviderLyricDocumentBuilder,
)
from konokashi.infrastructure.storage.bootstrap import open_storage
from tests.stage2_helpers import resolver as track_resolver
from tests.stage2_helpers import snapshot

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
    provider: str = "LRCLIB",
    title: str = "Elevate (Radio Edit)",
    artist: str = "Little Sis Nora & S3RL",
    album: str | None = "Elevate",
    duration_ms: int | None = 183_771,
    plain: str | None = "First\nSecond",
    synced: str | None = "[00:01.00]First\n[00:02.00]Second",
    instrumental: bool = False,
) -> LyricsProviderCandidate:
    return LyricsProviderCandidate(
        provider,
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
        name: str = "LRCLIB",
        exact: LyricsProviderResult | None = None,
        search: LyricsProviderResult | None = None,
        cached: dict[tuple[bytes, bool], LyricsProviderResult] | None = None,
    ) -> None:
        self.name = name
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
    provider: _FakeProvider | tuple[_FakeProvider, ...],
    *,
    local_sources: tuple[_LocalSource, ...] = (),
    title_aliases: object | None = None,
    evidence_window_seconds: float = 0.4,
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
        title_aliases=(title_aliases if callable(title_aliases) else None),
        evidence_window_seconds=evidence_window_seconds,
    )


def test_enriched_youtube_identity_finds_lrclib_without_uploader_as_artist(
    tmp_path: Path,
) -> None:
    source_resolver, _overrides = track_resolver()
    track = source_resolver.resolve(
        snapshot(
            "browser",
            title="Luce sul mare (Official Video) - YouTube",
            artists=("Example Records",),
            url="https://www.youtube.com/watch?v=AbCdEfGhI12",
            duration_us=198_000_000,
        )
    )
    provider = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate(
                    title="Luce sul mare",
                    artist="Cantante Fittizia",
                    album=None,
                    duration_ms=198_000,
                ),
            ),
        )
    )
    resolver = _resolver(tmp_path / "youtube-miss.db", provider)

    missed = resolver.resolve(track)
    assert missed.status is LyricsResolutionStatus.NO_RESULT
    assert provider.search_queries == []

    enriched = with_youtube_metadata_candidates(
        track,
        (
            TrackCandidate(
                "Luce sul mare",
                ("Cantante Fittizia",),
                None,
                198_000_000,
                strategy="youtube-enrichment:labelled-description",
                field_provenance=(
                    ("title", "youtube_description"),
                    ("artists", "youtube_credit"),
                ),
            ),
        ),
    )
    stale_generation = resolver.cancellation_generation
    resolver.cancel_inflight()
    stale = resolver.resolve(enriched, expected_generation=stale_generation)
    assert stale.status is LyricsResolutionStatus.NO_RESULT
    assert provider.search_queries == []
    found = resolver.resolve(enriched)

    assert found.status is LyricsResolutionStatus.FOUND_TIMED
    assert found.confidence is LyricsMatchConfidence.HIGH
    assert enriched.candidate == track.candidate
    assert enriched.raw_snapshot == track.raw_snapshot
    assert provider.search_queries[0].artists == ("Cantante Fittizia",)
    assert provider.search_queries[0].source_confidence == "Medium"
    assert all(
        "Example Records" not in query.artists for query in provider.search_queries
    )


def test_multi_artist_youtube_identity_can_retrieve_by_lead_artist(
    tmp_path: Path,
) -> None:
    class LeadArtistProvider(_FakeProvider):
        def search(self, query: LyricsQuery) -> LyricsProviderResult:
            self.search_queries.append(query)
            if query.artists != ("Lead Artist",) or query.broad:
                return LyricsProviderResult(LyricsProviderStatus.NO_RESULT)
            return LyricsProviderResult(
                LyricsProviderStatus.RESULTS,
                (
                    _candidate(
                        "collaboration",
                        title="Collaborative Song",
                        artist="Lead Artist & Guest Artist",
                        album="Shared Album",
                        duration_ms=201_000,
                    ),
                ),
            )

    candidate = TrackCandidate(
        "Collaborative Song",
        ("Lead Artist", "Guest Artist"),
        "Shared Album",
        201_000_000,
        strategy="youtube-enrichment:structured-music-fields",
        artist_credit=ArtistCredit(("Lead Artist", "Guest Artist"), ()),
        field_provenance=(
            ("title", "youtube_track"),
            ("artists", "youtube_artists"),
            ("album", "youtube_album"),
            ("duration", "youtube_duration"),
        ),
        identity_confidence=Confidence.MEDIUM,
    )
    track = replace(
        _track(
            title=candidate.title,
            artists=candidate.artists,
            album=candidate.album,
            duration_us=candidate.duration_us,
        ),
        candidate=candidate,
    )
    provider = LeadArtistProvider()

    result = _resolver(tmp_path / "lead-artist.db", provider).resolve(track)

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert result.document is not None
    assert result.document.provider_record_id == "collaboration"
    assert any(query.artists == ("Lead Artist",) for query in provider.search_queries)
    assert "retrieved by primary artist and title" in " ".join(result.evidence)
    assert "ordered main-artist credits match" in result.evidence


def test_automatic_retry_reuses_first_pass_provider_query_cache(tmp_path: Path) -> None:
    class SearchProvider(_FakeProvider):
        def search(self, query: LyricsQuery) -> LyricsProviderResult:
            self.search_queries.append(query)
            if query.title == "Luce sul mare":
                return LyricsProviderResult(
                    LyricsProviderStatus.RESULTS,
                    (
                        _candidate(
                            "recording",
                            title="Luce sul mare",
                            artist="Cantante Fittizia",
                            album=None,
                            duration_ms=198_000,
                        ),
                    ),
                    raw_payload=b"enriched-hit",
                )
            return LyricsProviderResult(
                LyricsProviderStatus.NO_RESULT,
                raw_payload=b"initial-miss",
            )

    track = _track(
        title="Misleading Caption",
        artists=("Uploader Channel",),
        album=None,
        duration_us=198_000_000,
    )
    provider = SearchProvider()
    resolver = _resolver(tmp_path / "retry-cache.db", provider)

    first = resolver.resolve(track)
    initial_queries = tuple(provider.search_queries)
    enriched = with_youtube_metadata_candidates(
        track,
        (
            TrackCandidate(
                "Luce sul mare",
                ("Cantante Fittizia",),
                None,
                198_000_000,
                strategy="youtube-enrichment:music-fields",
            ),
        ),
    )
    retried = resolver.resolve(enriched)

    assert first.status is LyricsResolutionStatus.NO_RESULT
    assert retried.status is LyricsResolutionStatus.FOUND_TIMED
    assert retried.cache_hit is True
    assert provider.search_queries[: len(initial_queries)] == list(initial_queries)
    assert all(
        query.title != "Misleading Caption"
        for query in provider.search_queries[len(initial_queries) :]
    )
    assert len(provider.search_queries) == len(initial_queries) + 1


def test_enriched_retry_does_not_reaccept_an_explicitly_rejected_result(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rejected-enriched-retry.db"
    track = _track(
        title="Luce sul mare (Official Video)",
        artists=(),
        album=None,
        duration_us=198_000_000,
    )
    candidate = _candidate(
        "rejected-enriched",
        title="Luce sul mare",
        artist="Cantante Fittizia",
        album=None,
        duration_ms=198_000,
    )
    rejected_document, _ = ProviderLyricDocumentBuilder().build(candidate, NOW)
    assert rejected_document is not None
    storage = open_storage(path)
    storage.lyrics.put(rejected_document)
    rejected_match = LyricsMatch(
        rejected_document.document_id,
        LyricsMatchDecision.REJECTED,
        ContentProvenance.USER,
        NOW,
        LyricsMatchConfidence.LOW,
        ("known wrong result",),
    )
    storage.lyrics_matches.put(track.source_identity, rejected_match)
    storage.lyrics_matches.put_rejection(track.source_identity, rejected_match)
    provider = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (candidate,),
            raw_payload=b"rejected-enriched",
        )
    )
    resolver = _resolver(path, provider)

    first = resolver.resolve(track)
    enriched = with_youtube_metadata_candidates(
        track,
        (
            TrackCandidate(
                "Luce sul mare",
                ("Cantante Fittizia",),
                None,
                198_000_000,
                strategy="youtube-enrichment:music-fields",
            ),
        ),
    )
    retried = resolver.resolve(enriched)

    assert first.status is LyricsResolutionStatus.NO_RESULT
    assert retried.status is LyricsResolutionStatus.NO_RESULT
    assert retried.document is None
    assert storage.lyrics_matches.get(track.source_identity) == rejected_match


def test_uncorroborated_pipe_hypothesis_is_searchable_but_not_auto_accepted(
    tmp_path: Path,
) -> None:
    source_resolver, _overrides = track_resolver()
    track = source_resolver.resolve(
        snapshot(
            "browser",
            title="Song | Artist (Official Video)",
            artists=("Uploader Channel",),
            url="https://www.youtube.com/watch?v=AbCdEfGhI12",
            duration_us=198_000_000,
        )
    )
    provider = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate(
                    title="Song",
                    artist="Artist",
                    album=None,
                    duration_ms=198_000,
                ),
            ),
        )
    )

    result = _resolver(tmp_path / "pipe-hypothesis.db", provider).resolve(track)

    assert any(
        query.title == "Song" and query.artists == ("Artist",)
        for query in provider.search_queries
    )
    assert all(query.source_confidence == "Low" for query in provider.search_queries)
    assert result.status not in (
        LyricsResolutionStatus.FOUND_TIMED,
        LyricsResolutionStatus.FOUND_UNTIMED,
    )


def test_provider_candidate_can_corroborate_an_alternate_recording_hypothesis(
    tmp_path: Path,
) -> None:
    class PrimaryOnlyProvider(_FakeProvider):
        def search(self, query: LyricsQuery) -> LyricsProviderResult:
            self.search_queries.append(query)
            if query.title != "Misleading Caption" or query.broad:
                return LyricsProviderResult(LyricsProviderStatus.NO_RESULT)
            return LyricsProviderResult(
                LyricsProviderStatus.RESULTS,
                (
                    _candidate(
                        "alternate-recording",
                        title="Luce sul mare",
                        artist="Cantante Fittizia",
                        album=None,
                        duration_ms=198_000,
                    ),
                ),
            )

    track = _track(
        title="Misleading Caption",
        artists=("Uploader Channel",),
        album=None,
        duration_us=198_000_000,
    )
    alternate = TrackCandidate(
        "Luce sul mare",
        ("Cantante Fittizia",),
        None,
        198_000_000,
        strategy="youtube-enrichment:music-fields",
        identity_confidence=Confidence.MEDIUM,
        field_provenance=(
            ("title", "youtube_track"),
            ("artists", "youtube_artists"),
        ),
    )
    track = replace(
        track,
        interpretation_candidates=(track.candidate, alternate),
    )
    provider = PrimaryOnlyProvider()

    result = _resolver(tmp_path / "cross-hypothesis.db", provider).resolve(track)

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert result.document is not None
    assert result.document.source_title == "Luce sul mare"
    assert track.raw_snapshot.metadata.title == "Misleading Caption"
    assert provider.search_queries[0].title == "Misleading Caption"
    assert "provider result retrieved by" in " ".join(result.evidence)
    assert "retrieval confidence is Medium" in result.evidence
    assert "youtube-enrichment:music-fields" in " ".join(result.diagnostics)
    assert "accepted because" in " ".join(result.diagnostics)
    assert "recording identity=Medium" in " ".join(result.diagnostics)

    uncorroborated = replace(
        track,
        interpretation_candidates=(
            track.candidate,
            replace(alternate, identity_confidence=Confidence.LOW),
        ),
    )
    weak_result = _resolver(
        tmp_path / "cross-hypothesis-low.db", PrimaryOnlyProvider()
    ).resolve(uncorroborated)
    assert weak_result.status is LyricsResolutionStatus.AMBIGUOUS
    assert weak_result.document is None
    assert "rejected automatic attachment because" in " ".join(weak_result.diagnostics)
    assert "remained ambiguous because" in " ".join(weak_result.diagnostics)


def test_query_ladder_bounds_and_deduplicates_alternative_interpretations() -> None:
    primary = LyricsQuery(
        "Song (Radio Edit)",
        ("Artist",),
        "Album",
        180_000,
        strategy="youtube-title:spaced dash",
    )
    alternatives = tuple(
        LyricsQuery(
            f"Alternate {index}",
            ("Artist",),
            None,
            180_000,
            source_confidence="Low",
            strategy=f"youtube-title:hypothesis:{index}",
        )
        for index in range(20)
    )

    plan = _candidate_search_plan((primary, *alternatives, alternatives[0]))

    assert len(plan) == 16
    assert [(strategy, query.title) for strategy, query, _ in plan[:4]] == [
        ("normalized title and artist", "Song (Radio Edit)"),
        ("base title and artist", "Song"),
        ("broader artist catalogue", "Song (Radio Edit)"),
        ("normalized title and artist", "Alternate 0"),
    ]
    assert all(not query.broad for _strategy, query, _ in plan[3:])
    assert len({query.title for _strategy, query, _ in plan[3:]}) == 13


def test_query_ladder_tries_bounded_phonetic_alias_before_base_fallback() -> None:
    query = LyricsQuery("アンドロイドガール (Radio Edit)", ("DECO*27",), None, 215_000)

    plan = _candidate_search_plan(
        (query,),
        title_aliases=lambda _title: (
            "andoroidogaru (Radio Edit)",
            "andoroidogaru edit",
            "unbounded third alias",
        ),
    )

    assert [strategy for strategy, _search, _assessment in plan] == [
        "normalized title and artist",
        "phonetic title alias and artist",
        "phonetic title alias and artist",
        "base title and artist",
        "broader artist catalogue",
    ]
    assert all(assessment is query for _strategy, _search, assessment in plan)


def test_query_ladder_deduplicates_unicode_punctuation_spellings() -> None:
    first = LyricsQuery("Long — Term", ("Artist",), None, 200_000)
    equivalent = LyricsQuery("Long - Term", ("Artist",), None, 200_000)

    plan = _candidate_search_plan((first, equivalent))

    assert len(plan) == 2
    assert [query.broad for _strategy, query, _assessment in plan] == [False, True]


def test_query_ladder_deduplicates_provider_request_across_credit_hypotheses() -> None:
    first = LyricsQuery("Song", ("Artist",), None, 200_000)
    different_credit = replace(
        first,
        main_artists=("Artist",),
        contributors=("Guest",),
        strategy="featured-credit-hypothesis",
    )

    plan = _candidate_search_plan((first, different_credit))

    assert len(plan) == 2
    assert all(assessment is first for _strategy, _search, assessment in plan)


def test_query_ladder_uses_one_primary_artist_retrieval_variant() -> None:
    query = LyricsQuery(
        "Collaborative Song",
        ("Lead Artist", "Guest Artist", "Producer"),
        "Shared Album",
        201_000,
        main_artists=("Lead Artist", "Guest Artist", "Producer"),
        strategy="youtube-enrichment:structured-music-fields",
    )

    plan = _candidate_search_plan((query,))

    primary = next(
        (search, assessment)
        for strategy, search, assessment in plan
        if strategy == "primary artist and title"
    )
    search, assessment = primary
    assert search.artists == ("Lead Artist",)
    assert search.album is None
    assert assessment is query
    assert len(plan) == 3


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
    assert any("exact provider lookup skipped" in item for item in result.diagnostics)


def test_album_free_duration_lookup_recovers_candidate_missed_by_search(
    tmp_path: Path,
) -> None:
    provider = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            tuple(
                _candidate(f"noise-{index}", title=f"Unrelated {index}", artist="Other")
                for index in range(20)
            ),
        ),
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate("duration-get", album="Provider Album"),),
        ),
    )

    result = _resolver(tmp_path / "album-free-get.sqlite3", provider).resolve(
        _track(album=None)
    )

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert result.document is not None
    assert result.document.provider_record_id == "duration-get"
    assert provider.search_queries
    assert len(provider.exact_queries) == 1
    assert provider.exact_queries[0].album is None
    assert any("album-free duration lookup" in item for item in result.diagnostics)


def test_album_free_get_cannot_break_conflicting_search_results(
    tmp_path: Path,
) -> None:
    first = _candidate("first", album="Provider Album One")
    second = _candidate(
        "second",
        album="Provider Album Two",
        plain="Different\nWords",
        synced="[00:01.00]Different\n[00:02.00]Words",
    )
    provider = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (first, second),
        ),
        exact=LyricsProviderResult(LyricsProviderStatus.RESULTS, (first,)),
    )

    result = _resolver(tmp_path / "album-free-conflict.sqlite3", provider).resolve(
        _track(album=None)
    )

    assert result.status is LyricsResolutionStatus.AMBIGUOUS
    assert {item.record_id for item in result.alternatives} == {"first", "second"}
    assert len(provider.exact_queries) == 1


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


def test_multiple_high_candidates_remain_ambiguous_even_with_closest_duration(
    tmp_path: Path,
) -> None:
    track = _track(album=None)
    tied = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate("a", album=None, duration_ms=183_700),
                _candidate(
                    "b",
                    album=None,
                    duration_ms=183_700,
                    plain="Different first\nDifferent second",
                    synced="[00:01.00]Different first\n[00:02.00]Different second",
                ),
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
                _candidate(
                    "b",
                    album=None,
                    duration_ms=184_500,
                    plain="Different first\nDifferent second",
                    synced="[00:01.00]Different first\n[00:02.00]Different second",
                ),
            ),
            raw_payload=b"closest",
        )
    )
    still_ambiguous = _resolver(tmp_path / "closest.sqlite3", closest).resolve(track)
    assert still_ambiguous.status is LyricsResolutionStatus.AMBIGUOUS
    assert still_ambiguous.document is None
    assert "2 candidates met the explicit acceptance policy" in " ".join(
        still_ambiguous.diagnostics
    )


def test_same_provider_duplicate_records_with_identical_content_remain_ambiguous(
    tmp_path: Path,
) -> None:
    track = _track(album=None)
    provider = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate("duplicate-a", album="Compilation A"),
                _candidate("duplicate-b", album="Compilation B"),
            ),
            raw_payload=b"duplicate provider records",
        )
    )

    result = _resolver(tmp_path / "same-content.sqlite3", provider).resolve(track)

    assert result.status is LyricsResolutionStatus.AMBIGUOUS
    assert result.document is None
    assert {item.record_id for item in result.alternatives} == {
        "duplicate-a",
        "duplicate-b",
    }


def test_plain_text_consensus_discards_conflicting_synchronized_variants(
    tmp_path: Path,
) -> None:
    track = _track(album=None)
    lrclib_candidate = _candidate(
        "timing-a",
        album="Compilation A",
        synced="[00:01.00]First\n[00:02.00]Second",
    )
    unison_candidate = _candidate(
        "timing-b",
        provider="Unison",
        album="Compilation B",
        synced="[00:04.00]First\n[00:05.00]Second",
    )
    lrclib = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                lrclib_candidate,
                _candidate(
                    "timing-a-copy",
                    album="Compilation Copy",
                    synced="[00:01.00]First\n[00:02.00]Second",
                ),
            ),
            raw_payload=b"lrclib plain consensus",
        )
    )
    unison = _FakeProvider(
        name="Unison",
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (unison_candidate,),
            raw_payload=b"unison plain consensus",
        ),
    )

    result = _resolver(tmp_path / "plain-consensus.sqlite3", (lrclib, unison)).resolve(
        track
    )

    assert result.status is LyricsResolutionStatus.FOUND_UNTIMED
    assert result.document is not None
    assert result.document.kind is LyricDocumentKind.PLAIN
    assert result.document.original_text == "First\nSecond"
    assert any(
        "3 eligible records from 2 distinct providers agree on exact plain lyric text"
        in item
        for item in result.evidence
    )
    assert any("without timestamps" in item for item in result.evidence)


def test_rejecting_plain_consensus_suppresses_content_equivalent_records(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rejected-plain-consensus.sqlite3"
    track = _track(album=None)
    lrclib_candidate = _candidate(
        "timing-a",
        album="Compilation A",
        synced="[00:01.00]First\n[00:02.00]Second",
    )
    unison_candidate = _candidate(
        "timing-b",
        provider="Unison",
        album="Compilation B",
        synced="[00:04.00]First\n[00:05.00]Second",
    )
    first = _resolver(
        path,
        (
            _FakeProvider(
                search=LyricsProviderResult(
                    LyricsProviderStatus.RESULTS,
                    (lrclib_candidate,),
                    raw_payload=b"lrclib plain consensus",
                )
            ),
            _FakeProvider(
                name="Unison",
                search=LyricsProviderResult(
                    LyricsProviderStatus.RESULTS,
                    (unison_candidate,),
                    raw_payload=b"unison plain consensus",
                ),
            ),
        ),
    ).resolve(track)
    assert first.document is not None
    storage = open_storage(path)
    rejected = LyricsMatch(
        first.document.document_id,
        LyricsMatchDecision.REJECTED,
        ContentProvenance.USER,
        NOW,
        LyricsMatchConfidence.HIGH,
        ("explicitly rejected by the user",),
    )
    storage.lyrics_matches.reject(track.source_identity, rejected)

    repeated = _resolver(
        path,
        (
            _FakeProvider(
                search=LyricsProviderResult(
                    LyricsProviderStatus.RESULTS,
                    (lrclib_candidate,),
                    raw_payload=b"lrclib plain consensus repeated",
                )
            ),
            _FakeProvider(
                name="Unison",
                search=LyricsProviderResult(
                    LyricsProviderStatus.RESULTS,
                    (unison_candidate,),
                    raw_payload=b"unison plain consensus repeated",
                ),
            ),
        ),
    ).resolve(track)

    assert repeated.status is LyricsResolutionStatus.NO_RESULT
    assert repeated.document is None


def test_exact_raw_case_cannot_break_conflicting_high_lyric_content(
    tmp_path: Path,
) -> None:
    provider = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate("raw-case", album=None),
                _candidate(
                    "normalized-case",
                    title="elevate (radio edit)",
                    album=None,
                    plain="Different first\nDifferent second",
                    synced="[00:01.00]Different first\n[00:02.00]Different second",
                ),
            ),
        )
    )

    result = _resolver(tmp_path / "raw-case-conflict.sqlite3", provider).resolve(
        _track(album=None)
    )

    assert result.status is LyricsResolutionStatus.AMBIGUOUS
    assert {item.record_id for item in result.alternatives} == {
        "raw-case",
        "normalized-case",
    }


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
    query = LyricsQuery(
        "Elevate (Radio Edit)",
        ("Little Sis Nora & S3RL",),
        "Elevate",
        183_771,
    )
    entry = open_storage(path).provider_cache.get(
        "LRCLIB", provider_cache_key("LRCLIB", query, search=False)
    )
    assert entry is not None
    assert entry.expires_at == NOW + timedelta(hours=12)


def test_nonpositive_and_unbounded_duration_never_enter_provider_queries(
    tmp_path: Path,
) -> None:
    for index, duration_us in enumerate((-1, 0, 8 * 24 * 60 * 60 * 1_000_000)):
        provider = _FakeProvider()
        _resolver(tmp_path / f"duration-{index}.sqlite3", provider).resolve(
            _track(duration_us=duration_us)
        )
        assert provider.exact_queries == []
        assert provider.search_queries
        assert all(query.duration_ms is None for query in provider.search_queries)


def test_positive_provider_cache_has_longer_explicit_ttl(tmp_path: Path) -> None:
    path = tmp_path / "positive-ttl.sqlite3"
    track = _track()
    provider = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate(),),
            raw_payload=b"positive",
        )
    )

    _resolver(path, provider).resolve(track)

    query = LyricsQuery(
        "Elevate (Radio Edit)",
        ("Little Sis Nora & S3RL",),
        "Elevate",
        183_771,
    )
    entry = open_storage(path).provider_cache.get(
        "LRCLIB", provider_cache_key("LRCLIB", query, search=False)
    )
    assert entry is not None
    assert entry.expires_at == NOW + timedelta(days=7)


def test_offline_expired_positive_is_labelled_but_negative_is_not_current(
    tmp_path: Path,
) -> None:
    track = _track()
    query = LyricsQuery(
        "Elevate (Radio Edit)",
        ("Little Sis Nora & S3RL",),
        "Elevate",
        183_771,
    )
    positive_path = tmp_path / "stale-positive.sqlite3"
    positive_storage = open_storage(positive_path)
    positive_storage.provider_cache.put(
        ProviderCacheEntry(
            "LRCLIB",
            provider_cache_key("LRCLIB", query, search=False),
            b"stale-positive",
            NOW - timedelta(days=9),
            NOW - timedelta(days=2),
        )
    )
    cached = LyricsProviderResult(
        LyricsProviderStatus.RESULTS,
        (_candidate(),),
        raw_payload=b"stale-positive",
    )
    positive = _resolver(
        positive_path,
        _FakeProvider(cached={(b"stale-positive", False): cached}),
    ).resolve(track, offline=True)

    assert positive.status is LyricsResolutionStatus.FOUND_TIMED
    assert positive.cache_hit is True
    assert "stale positive" in " ".join(positive.diagnostics)

    negative_path = tmp_path / "stale-negative.sqlite3"
    negative_storage = open_storage(negative_path)
    negative_storage.provider_cache.put(
        ProviderCacheEntry(
            "LRCLIB",
            provider_cache_key("LRCLIB", query, search=False),
            b"stale-negative",
            NOW - timedelta(days=2),
            NOW - timedelta(days=1),
        )
    )
    negative = _resolver(
        negative_path,
        _FakeProvider(
            cached={
                (b"stale-negative", False): LyricsProviderResult(
                    LyricsProviderStatus.NO_RESULT
                )
            }
        ),
    ).resolve(track, offline=True)

    assert negative.status is LyricsResolutionStatus.OFFLINE_MISS
    assert negative.cache_hit is False


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
    assert [query.broad for query in provider.search_queries] == [
        False,
        False,
        False,
        True,
    ]
    assert provider.search_queries[2].artists == ("Little Sis Nora",)


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


def test_offline_refresh_uses_existing_provider_cache_instead_of_discarding_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "offline-refresh-cache.sqlite3"
    track = _track()
    query = LyricsQuery(
        "Elevate (Radio Edit)", ("Little Sis Nora & S3RL",), "Elevate", 183_771
    )
    storage = open_storage(path)
    storage.provider_cache.put(
        ProviderCacheEntry(
            "LRCLIB",
            provider_cache_key("LRCLIB", query, search=False),
            b"cached-high",
            NOW,
            NOW + timedelta(days=1),
        )
    )
    cached_result = LyricsProviderResult(
        LyricsProviderStatus.RESULTS, (_candidate(),), raw_payload=b"cached-high"
    )
    provider = _FakeProvider(cached={(b"cached-high", False): cached_result})

    result = _resolver(path, provider).resolve(track, offline=True, refresh=True)

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert result.cache_hit is True
    assert result.network_used is False
    assert provider.exact_queries == []


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
        rejected_match := LyricsMatch(
            rejected_document.document_id,
            LyricsMatchDecision.REJECTED,
            ContentProvenance.USER,
            NOW,
            LyricsMatchConfidence.LOW,
            ("known wrong result",),
        ),
    )
    storage.lyrics_matches.put_rejection(track.source_identity, rejected_match)
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


def test_alternative_catalog_deduplicates_and_marks_current_rejection(
    tmp_path: Path,
) -> None:
    path = tmp_path / "alternatives.sqlite3"
    track = _track()
    builder = ProviderLyricDocumentBuilder()
    rejected_candidate = _candidate("rejected")
    rejected_document, _ = builder.build(rejected_candidate, NOW)
    assert rejected_document is not None
    storage = open_storage(path)
    storage.lyrics.put(rejected_document)
    storage.lyrics_matches.put(
        track.source_identity,
        rejected_match := LyricsMatch(
            rejected_document.document_id,
            LyricsMatchDecision.REJECTED,
            ContentProvenance.USER,
            NOW,
            LyricsMatchConfidence.HIGH,
            ("explicitly rejected by the user",),
        ),
    )
    storage.lyrics_matches.put_rejection(track.source_identity, rejected_match)
    other = _candidate("other", title="Different Version")
    provider = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (rejected_candidate,),
            raw_payload=b"exact-alternatives",
        ),
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (rejected_candidate, other),
            raw_payload=b"search-alternatives",
        ),
    )

    result = _resolver(path, provider).alternatives(track)

    assert len(result.alternatives) == 2
    rejected = next(item for item in result.alternatives if item.rejected)
    assert rejected.current
    assert rejected.document_id == rejected_document.document_id
    assert {item.candidate.record_id for item in result.alternatives} == {
        "rejected",
        "other",
    }
    assert result.network_used
    match = open_storage(path).lyrics_matches.get(track.source_identity)
    assert match is not None
    assert match.decision is LyricsMatchDecision.REJECTED


def test_rejection_survives_another_selection_restart_and_unavailable_result(
    tmp_path: Path,
) -> None:
    path = tmp_path / "durable-rejection.sqlite3"
    track = _track()
    builder = ProviderLyricDocumentBuilder()
    rejected_candidate = _candidate("wrong")
    rejected_document, _ = builder.build(rejected_candidate, NOW)
    selected_document, _ = builder.build(_candidate("selected"), NOW)
    assert rejected_document is not None
    assert selected_document is not None
    storage = open_storage(path)
    storage.lyrics.put(rejected_document)
    storage.lyrics.put(selected_document)
    rejected_match = LyricsMatch(
        rejected_document.document_id,
        LyricsMatchDecision.REJECTED,
        ContentProvenance.USER,
        NOW,
        LyricsMatchConfidence.HIGH,
        ("explicitly rejected by the user",),
    )
    storage.lyrics_matches.put_rejection(track.source_identity, rejected_match)
    storage.lyrics_matches.put(
        track.source_identity,
        LyricsMatch(
            selected_document.document_id,
            LyricsMatchDecision.APPROVED,
            ContentProvenance.USER,
            NOW,
            LyricsMatchConfidence.APPROVED,
            ("explicitly selected by the user",),
        ),
    )

    restarted = open_storage(path)
    assert restarted.lyrics_matches.rejections(track.source_identity) == (
        rejected_match,
    )
    assert restarted.lyrics_matches.delete(track.source_identity)
    assert restarted.lyrics.delete(selected_document.document_id)
    provider = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (rejected_candidate,),
            raw_payload=b"wrong-exact",
        ),
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (rejected_candidate,),
            raw_payload=b"wrong-search",
        ),
    )

    result = _resolver(path, provider).resolve(track)

    assert result.status is LyricsResolutionStatus.NO_RESULT
    assert result.document is None


def test_rejected_local_document_is_not_automatically_reattached(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rejected-local.sqlite3"
    track = _track(source=LocalFileIdentity("/music/song.flac"))
    document = _document(
        "rejected-local", source="local-sidecar", kind=LyricDocumentKind.SYNCED
    )
    storage = open_storage(path)
    storage.lyrics.put(document)
    storage.lyrics_matches.put(
        track.source_identity,
        rejected_match := LyricsMatch(
            document.document_id,
            LyricsMatchDecision.REJECTED,
            ContentProvenance.USER,
            NOW,
            LyricsMatchConfidence.HIGH,
            ("explicitly rejected by the user",),
        ),
    )
    storage.lyrics_matches.put_rejection(track.source_identity, rejected_match)
    local = _LocalSource(
        LocalLyricsResult(LocalLyricsStatus.FOUND, "Local sidecar LRC", document)
    )

    result = _resolver(path, _FakeProvider(), local_sources=(local,)).resolve(track)

    assert result.status is LyricsResolutionStatus.NO_RESULT
    assert result.document is None
    assert any("rejected local lyric" in item for item in result.diagnostics)


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


def test_unavailable_primary_does_not_hide_high_confidence_backup(
    tmp_path: Path,
) -> None:
    primary = _FakeProvider(
        exact=LyricsProviderResult(LyricsProviderStatus.UNAVAILABLE),
        search=LyricsProviderResult(LyricsProviderStatus.UNAVAILABLE),
    )
    backup_candidate = replace(
        _candidate(provider="Unison", duration_ms=None),
        provider_duration_matched=True,
    )
    backup = _FakeProvider(
        name="Unison",
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (backup_candidate,),
            raw_payload=b"unison-exact",
        ),
    )

    result = _resolver(tmp_path / "fallback.sqlite3", (primary, backup)).resolve(
        _track()
    )

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert result.source_label == "Unison"
    assert primary.exact_queries
    assert backup.exact_queries
    assert any("provider LRCLIB: queried" in item for item in result.diagnostics)
    assert any("provider Unison: queried" in item for item in result.diagnostics)


def test_configured_providers_begin_each_lookup_in_bounded_parallel(
    tmp_path: Path,
) -> None:
    rendezvous = Barrier(2)

    class CoordinatedProvider(_FakeProvider):
        def exact(self, query: LyricsQuery) -> LyricsProviderResult:
            self.exact_queries.append(query)
            rendezvous.wait(timeout=2)
            return self.exact_result

    first = CoordinatedProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate("l"),),
        )
    )
    second = CoordinatedProvider(
        name="Unison",
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate("u", provider="Unison"),),
        ),
    )

    result = _resolver(tmp_path / "parallel.sqlite3", (first, second)).resolve(_track())

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert first.exact_queries and second.exact_queries


def test_slow_provider_cannot_hold_sufficient_evidence_hostage(
    tmp_path: Path,
) -> None:
    slow_started = Event()
    slow_released = Event()

    class SlowProvider(_FakeProvider):
        def exact(self, query: LyricsQuery) -> LyricsProviderResult:
            self.exact_queries.append(query)
            slow_started.set()
            slow_released.wait(timeout=2)
            return self.exact_result

    fast = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate("fast"),),
        )
    )
    slow = SlowProvider(name="Unison")
    resolver = _resolver(
        tmp_path / "bounded-evidence.sqlite3",
        (fast, slow),
        evidence_window_seconds=0.05,
    )

    started = monotonic()
    result = resolver.resolve(_track())
    elapsed = monotonic() - started
    slow_released.set()

    assert slow_started.is_set()
    assert elapsed < 0.5
    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert any("first usable candidate" in item for item in result.diagnostics)
    assert any("first viable candidate" in item for item in result.diagnostics)
    assert any("sufficient evidence in" in item for item in result.diagnostics)
    assert any("detached 1 running" in item for item in result.diagnostics)


def test_sufficient_evidence_cancels_only_provider_work_that_has_not_started(
    tmp_path: Path,
) -> None:
    release_blockers = Event()

    class BlockingProvider(_FakeProvider):
        def exact(self, query: LyricsQuery) -> LyricsProviderResult:
            self.exact_queries.append(query)
            release_blockers.wait(timeout=2)
            return self.exact_result

    fast = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate("fast"),),
        )
    )
    blockers = tuple(
        BlockingProvider(name=f"Blocking {index}") for index in range(1, 5)
    )
    queued = _FakeProvider(name="Queued")
    resolver = _resolver(
        tmp_path / "queued-cancellation.sqlite3",
        (fast, *blockers, queued),
        evidence_window_seconds=0.05,
    )

    result = resolver.resolve(_track())
    release_blockers.set()

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert queued.exact_queries == []
    assert any("cancelled 1 queued" in item for item in result.diagnostics)
    assert any("detached 4 running" in item for item in result.diagnostics)


def test_preferred_conflict_inside_evidence_window_prevents_automatic_choice(
    tmp_path: Path,
) -> None:
    release_preferred = Event()

    class DelayedPreferredProvider(_FakeProvider):
        def exact(self, query: LyricsQuery) -> LyricsProviderResult:
            self.exact_queries.append(query)
            release_preferred.wait(timeout=1)
            return self.exact_result

    preferred = DelayedPreferredProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate(
                    "preferred",
                    plain="Conflicting",
                    synced="[00:01.00]Conflicting",
                ),
            ),
        )
    )
    fast_backup = _FakeProvider(
        name="Unison",
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate("backup", provider="Unison"),),
        ),
    )
    resolver = _resolver(
        tmp_path / "window-conflict.sqlite3",
        (preferred, fast_backup),
        evidence_window_seconds=0.2,
    )

    def release_after_delay() -> None:
        sleep(0.03)
        release_preferred.set()

    timer = Thread(target=release_after_delay)
    timer.start()

    result = resolver.resolve(_track())
    timer.join(timeout=1)

    assert result.status is LyricsResolutionStatus.AMBIGUOUS
    assert {item.provider for item in result.alternatives} == {"LRCLIB", "Unison"}
    assert not any("detached" in item for item in result.diagnostics)


def test_fast_garbage_does_not_start_the_evidence_deadline(tmp_path: Path) -> None:
    release_good = Event()

    class DelayedGoodProvider(_FakeProvider):
        def exact(self, query: LyricsQuery) -> LyricsProviderResult:
            self.exact_queries.append(query)
            release_good.wait(timeout=1)
            return self.exact_result

    garbage = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate("garbage", title="Wrong", artist="Someone else"),),
        )
    )
    good = DelayedGoodProvider(
        name="Unison",
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate("good", provider="Unison"),),
        ),
    )
    resolver = _resolver(
        tmp_path / "garbage-first.sqlite3",
        (garbage, good),
        evidence_window_seconds=0.02,
    )

    def release_after_delay() -> None:
        sleep(0.08)
        release_good.set()

    timer = Thread(target=release_after_delay)
    timer.start()

    result = resolver.resolve(_track())
    timer.join(timeout=1)

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert result.source_label == "Unison"
    assert good.exact_queries
    assert sum("first usable candidate" in item for item in result.diagnostics) == 1
    assert not any("detached" in item for item in result.diagnostics)


def test_uncancellable_late_provider_can_still_warm_its_query_cache(
    tmp_path: Path,
) -> None:
    slow_started = Event()
    release_slow = Event()

    class SlowCacheProvider(_FakeProvider):
        def exact(self, query: LyricsQuery) -> LyricsProviderResult:
            self.exact_queries.append(query)
            slow_started.set()
            release_slow.wait(timeout=2)
            return self.exact_result

    path = tmp_path / "late-cache.sqlite3"
    fast = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate("fast"),),
        )
    )
    slow = SlowCacheProvider(
        name="Unison",
        exact=LyricsProviderResult(
            LyricsProviderStatus.NO_RESULT,
            raw_payload=b"late-no-result",
        ),
    )
    resolver = _resolver(path, (fast, slow), evidence_window_seconds=0.02)

    result = resolver.resolve(_track())
    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert slow_started.is_set()
    release_slow.set()

    cache_key = provider_cache_key("Unison", slow.exact_queries[0], search=False)
    deadline = monotonic() + 1
    cached = None
    while cached is None and monotonic() < deadline:
        cached = open_storage(path).provider_cache.get("Unison", cache_key)
        if cached is None:
            sleep(0.01)
    assert cached is not None
    assert cached.payload == b"late-no-result"


def test_superseded_uncancellable_completion_cannot_persist_a_match(
    tmp_path: Path,
) -> None:
    request_started = Event()
    release_request = Event()

    class UncancellableProvider(_FakeProvider):
        def exact(self, query: LyricsQuery) -> LyricsProviderResult:
            self.exact_queries.append(query)
            request_started.set()
            release_request.wait(timeout=2)
            return self.exact_result

        def cancel_inflight(self) -> None:
            pass

    path = tmp_path / "superseded.sqlite3"
    provider = UncancellableProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate("late"),),
        )
    )
    resolver = _resolver(path, provider)
    track = _track()
    completed: list[LyricsResolutionResult] = []
    worker = Thread(target=lambda: completed.append(resolver.resolve(track)))
    worker.start()
    assert request_started.wait(timeout=1)

    resolver.cancel_inflight()
    release_request.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert completed
    result = completed[0]
    assert result.status is LyricsResolutionStatus.NO_RESULT
    assert any("superseded" in item for item in result.diagnostics)
    assert open_storage(path).lyrics_matches.get(track.source_identity) is None


def test_equivalent_cross_provider_results_use_preference_only_as_tie_break(
    tmp_path: Path,
) -> None:
    unison = _FakeProvider(
        name="Unison",
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate("u", provider="Unison"),),
        ),
    )
    lrclib = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate("l"),),
        ),
    )

    result = _resolver(tmp_path / "preference.sqlite3", (unison, lrclib)).resolve(
        _track()
    )

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert result.source_label == "Unison"


def test_preference_cannot_break_conflicting_high_confidence_results(
    tmp_path: Path,
) -> None:
    unison = _FakeProvider(
        name="Unison",
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate(
                    "u",
                    provider="Unison",
                    plain="Different text",
                    synced="[00:01.00]Different text",
                ),
            ),
        ),
    )
    lrclib = _FakeProvider(
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate("l"),),
        ),
    )

    result = _resolver(tmp_path / "conflict.sqlite3", (unison, lrclib)).resolve(
        _track()
    )

    assert result.status is LyricsResolutionStatus.AMBIGUOUS
    assert {item.provider for item in result.alternatives} == {"LRCLIB", "Unison"}


def test_low_community_confidence_never_increases_automatic_acceptance(
    tmp_path: Path,
) -> None:
    candidate = replace(
        _candidate(provider="Unison"),
        provider_confidence=LyricsMatchConfidence.LOW,
    )
    provider = _FakeProvider(
        name="Unison",
        exact=LyricsProviderResult(LyricsProviderStatus.RESULTS, (candidate,)),
    )

    result = _resolver(tmp_path / "community-low.sqlite3", provider).resolve(_track())

    assert result.status is LyricsResolutionStatus.AMBIGUOUS
    assert result.alternatives[0].provider_confidence is LyricsMatchConfidence.LOW
    assert any(
        "low provider confidence prevents automatic match" in item
        for item in result.diagnostics
    )


def test_provider_exception_isolated_without_leaking_exception_text(
    tmp_path: Path,
) -> None:
    class BrokenProvider(_FakeProvider):
        def exact(self, query: LyricsQuery) -> LyricsProviderResult:
            self.exact_queries.append(query)
            raise RuntimeError("private-token-value")

        def search(self, query: LyricsQuery) -> LyricsProviderResult:
            self.search_queries.append(query)
            raise RuntimeError("private-token-value")

    backup = _FakeProvider(
        name="Unison",
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate(provider="Unison"),),
        ),
    )

    result = _resolver(
        tmp_path / "adapter-failure.sqlite3", (BrokenProvider(), backup)
    ).resolve(_track())

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert any("adapter failed as RuntimeError" in item for item in result.diagnostics)
    assert all("private-token-value" not in item for item in result.diagnostics)


def test_candidate_cannot_impersonate_another_provider(tmp_path: Path) -> None:
    provider = _FakeProvider(
        name="Unison",
        exact=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate(provider="LRCLIB"),),
        ),
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (_candidate(provider="LRCLIB"),),
        ),
    )

    result = _resolver(tmp_path / "provenance.sqlite3", provider).resolve(_track())

    assert result.status is LyricsResolutionStatus.INVALID_PROVIDER_RESPONSE
    assert any("mismatched provenance" in item for item in result.diagnostics)


def test_cancellation_reaches_every_configured_provider(tmp_path: Path) -> None:
    class CancellableProvider(_FakeProvider):
        def __init__(self, *, name: str) -> None:
            super().__init__(name=name)
            self.cancellations = 0

        def cancel_inflight(self) -> None:
            self.cancellations += 1

    first = CancellableProvider(name="LRCLIB")
    second = CancellableProvider(name="Unison")
    resolver = _resolver(tmp_path / "cancel.sqlite3", (first, second))

    resolver.cancel_inflight()

    assert first.cancellations == second.cancellations == 1


def test_exact_version_candidate_outranks_base_title_fallback(tmp_path: Path) -> None:
    track = _track(
        title="Party With Us (Radio Edit)",
        artists=("S3RL",),
        album=None,
        duration_us=180_000_000,
    )
    provider = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate(
                    "base",
                    title="Party With Us",
                    artist="S3RL",
                    album=None,
                    duration_ms=180_000,
                ),
                _candidate(
                    "exact",
                    title="Party With Us (Radio Edit)",
                    artist="S3RL",
                    album=None,
                    duration_ms=180_000,
                ),
            ),
            raw_payload=b"version candidates",
        )
    )

    result = _resolver(tmp_path / "version-rank.sqlite3", provider).resolve(track)

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert result.document is not None
    assert result.document.provider_record_id == "exact"


def test_synchronized_candidate_outranks_equivalent_plain_candidate(
    tmp_path: Path,
) -> None:
    track = _track(
        title="Song", artists=("Artist",), album=None, duration_us=180_000_000
    )
    provider = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate(
                    "plain",
                    title="Song",
                    artist="Artist",
                    album=None,
                    duration_ms=180_000,
                    synced=None,
                ),
                _candidate(
                    "synced",
                    title="Song",
                    artist="Artist",
                    album=None,
                    duration_ms=180_000,
                ),
            ),
            raw_payload=b"equivalent candidates",
        )
    )

    result = _resolver(tmp_path / "sync-rank.sqlite3", provider).resolve(track)

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert result.document is not None
    assert result.document.provider_record_id == "synced"


def test_base_title_with_wrong_recording_duration_downgrades_to_plain_text(
    tmp_path: Path,
) -> None:
    track = _track(
        title="Party With Us (Radio Edit)",
        artists=("S3RL",),
        album=None,
        duration_us=180_000_000,
    )
    candidate = _candidate(
        "base",
        title="Party With Us",
        artist="S3RL",
        album=None,
        duration_ms=240_000,
    )
    provider = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (candidate,),
            raw_payload=b"base title",
        )
    )

    result = _resolver(tmp_path / "version-text.sqlite3", provider).resolve(track)

    assert result.status is LyricsResolutionStatus.FOUND_UNTIMED
    assert result.document is not None
    assert result.document.kind is LyricDocumentKind.PLAIN
    assert "discarded synchronized timing" in " ".join(result.diagnostics)


def test_base_title_without_plain_fallback_rejects_untrusted_sync_timing(
    tmp_path: Path,
) -> None:
    track = _track(
        title="Party With Us (Radio Edit)",
        artists=("S3RL",),
        album=None,
        duration_us=180_000_000,
    )
    provider = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate(
                    "synced-only",
                    title="Party With Us",
                    artist="S3RL",
                    album=None,
                    duration_ms=240_000,
                    plain=None,
                ),
            ),
            raw_payload=b"untrusted timing",
        )
    )

    result = _resolver(tmp_path / "version-reject.sqlite3", provider).resolve(track)

    assert result.status is LyricsResolutionStatus.AMBIGUOUS
    assert result.document is None
    assert "timing was not trusted" in " ".join(result.diagnostics)


def test_broad_artist_catalogue_can_resolve_cross_script_phonetic_title(
    tmp_path: Path,
) -> None:
    class BroadOnlyProvider(_FakeProvider):
        def search(self, query: LyricsQuery) -> LyricsProviderResult:
            self.search_queries.append(query)
            if not query.broad:
                return LyricsProviderResult(
                    LyricsProviderStatus.NO_RESULT, raw_payload=b""
                )
            return LyricsProviderResult(
                LyricsProviderStatus.RESULTS,
                (
                    _candidate(
                        "android-girl",
                        title="アンドロイドガール",
                        artist="DECO*27",
                        album=None,
                        duration_ms=215_200,
                    ),
                ),
                raw_payload=b"cross script",
            )

    track = _track(
        title="Android Girl",
        artists=("DECO*27",),
        album=None,
        duration_us=215_441_000,
    )
    provider = BroadOnlyProvider()
    result = _resolver(
        tmp_path / "cross-script.sqlite3",
        provider,
        title_aliases=lambda _title: ("andoroidogaru",),
    ).resolve(track)

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert result.document is not None
    assert result.document.source_title == "アンドロイドガール"
    assert [query.broad for query in provider.search_queries] == [False, False, True]
    assert provider.search_queries[1].title == "andoroidogaru"
    assert "transliteration" in " ".join(result.evidence)
    assert "broader artist catalogue" in " ".join(result.diagnostics)


def test_native_interpretation_is_searched_after_english_miss(
    tmp_path: Path,
) -> None:
    class NativeOnlyProvider(_FakeProvider):
        def search(self, query: LyricsQuery) -> LyricsProviderResult:
            self.search_queries.append(query)
            if query.title != "アンドロイドガール":
                return LyricsProviderResult(
                    LyricsProviderStatus.NO_RESULT, raw_payload=b""
                )
            return LyricsProviderResult(
                LyricsProviderStatus.RESULTS,
                (
                    _candidate(
                        "android-native",
                        title="アンドロイドガール",
                        artist="DECO*27",
                        album=None,
                        duration_ms=215_200,
                    ),
                ),
                raw_payload=b"native",
            )

    track = _track(
        title="Android Girl",
        artists=("DECO*27",),
        album=None,
        duration_us=215_441_000,
    )
    native = TrackCandidate(
        "アンドロイドガール",
        ("DECO*27",),
        None,
        215_200_000,
        ("native title extracted from YouTube description",),
        strategy="youtube-enrichment:youtube_description",
        artist_credit=ArtistCredit(("DECO*27",), ("初音ミク",)),
        field_provenance=(
            ("title", "youtube_description"),
            ("artists", "youtube_description"),
        ),
    )
    track = replace(
        track,
        interpretation_candidates=(track.candidate, native),
    )
    provider = NativeOnlyProvider()

    result = _resolver(tmp_path / "native-interpretation.sqlite3", provider).resolve(
        track
    )

    assert result.status is LyricsResolutionStatus.FOUND_TIMED
    assert result.document is not None
    assert result.document.source_title == "アンドロイドガール"
    assert any(query.title == "アンドロイドガール" for query in provider.search_queries)
    assert "youtube-enrichment" in " ".join(result.diagnostics)


def test_equally_plausible_native_artist_forms_remain_reviewable(
    tmp_path: Path,
) -> None:
    candidates = (
        _candidate(
            "ali-bullet",
            title="Али Ули",
            artist="Lida • S3RL",
            album=None,
            duration_ms=178_000,
        ),
        _candidate(
            "ali-ampersand",
            title="Али Ули",
            artist="Lida & S3RL",
            album=None,
            duration_ms=178_000,
        ),
    )

    class NativeAliProvider(_FakeProvider):
        def search(self, query: LyricsQuery) -> LyricsProviderResult:
            self.search_queries.append(query)
            if query.title != "Али Ули":
                return LyricsProviderResult(
                    LyricsProviderStatus.NO_RESULT, raw_payload=b""
                )
            return LyricsProviderResult(
                LyricsProviderStatus.RESULTS,
                candidates,
                raw_payload=b"ali-native",
            )

    track = _track(
        title="Ali Uli",
        artists=("Lida, S3RL",),
        album=None,
        duration_us=178_000_000,
    )
    native = TrackCandidate(
        "Али Ули",
        ("Lida, S3RL",),
        None,
        178_000_000,
        ("native title extracted from YouTube description",),
        strategy="youtube-enrichment:optional-trailing-group",
        artist_credit=ArtistCredit(("Lida", "S3RL")),
    )
    track = replace(
        track,
        interpretation_candidates=(track.candidate, native),
    )
    provider = NativeAliProvider()
    resolver = _resolver(tmp_path / "ali-ambiguous.sqlite3", provider)

    result = resolver.resolve(track)
    review = resolver.alternatives(track, refresh=True)

    assert result.status is LyricsResolutionStatus.AMBIGUOUS
    assert {item.record_id for item in result.alternatives} == {
        "ali-bullet",
        "ali-ampersand",
    }
    assert {item.candidate.record_id for item in review.alternatives} == {
        "ali-bullet",
        "ali-ampersand",
    }
    assert all(
        item.text_confidence is LyricsMatchConfidence.HIGH
        and item.timing_confidence is LyricsMatchConfidence.HIGH
        for item in review.alternatives
    )


def test_manual_alternative_search_is_bounded_and_does_not_mutate_track(
    tmp_path: Path,
) -> None:
    track = _track(title="Raw title", artists=("Raw artist",), album=None)
    provider = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate(
                    "manual",
                    title="Manual title",
                    artist="Manual artist",
                    album=None,
                ),
            ),
            raw_payload=b"manual",
        )
    )

    review = _resolver(tmp_path / "manual-search.sqlite3", provider).alternatives(
        track,
        title=" Manual title ",
        artists=(" Manual artist ",),
    )

    assert review.search_title == "Manual title"
    assert review.search_artists == ("Manual artist",)
    assert {query.title for query in provider.search_queries} == {"Manual title"}
    assert {query.artist_name for query in provider.search_queries} == {"Manual artist"}
    assert len(provider.search_queries) == 2
    assert track.raw_snapshot.metadata.title == "Raw title"
    assert track.candidate.title == "Raw title"


def test_manual_search_reuses_cache_scoring_and_supports_title_only(
    tmp_path: Path,
) -> None:
    provider = _FakeProvider(
        search=LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                _candidate(
                    "native-title",
                    title="アンドロイドガール",
                    artist="DECO*27",
                    album="Android Girl",
                    duration_ms=215_441,
                ),
            ),
            raw_payload=b"manual-native-title",
        )
    )
    resolver = _resolver(tmp_path / "manual-title-only.sqlite3", provider)

    result = resolver.search(LyricsSearchRequest("Android Girl"))
    cached = resolver.search(LyricsSearchRequest("Android Girl"), offline=True)

    assert len(result.alternatives) == 1
    candidate = result.alternatives[0]
    assert candidate.candidate.track_name == "アンドロイドガール"
    assert candidate.candidate.artist_name == "DECO*27"
    assert candidate.confidence is LyricsMatchConfidence.LOW
    assert candidate.text_confidence is LyricsMatchConfidence.LOW
    assert provider.search_queries == [
        LyricsQuery(
            "Android Girl",
            (),
            None,
            None,
            broad=True,
            strategy="manual-search",
            provenance=(("title", "manual-search"),),
        )
    ]
    assert cached.cache_hit is True
    assert cached.network_used is False
