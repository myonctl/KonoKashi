"""Local-first, explainable lyrics resolution for one already-resolved track."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from konokashi.application.ports import (
    LocalLyricsProviderPort,
    LyricsCandidateDocumentPort,
    LyricsMatchRepositoryPort,
    LyricsProviderPort,
    LyricsRepositoryPort,
    ProviderCacheRepositoryPort,
)
from konokashi.domain.identity import PersistenceScope
from konokashi.domain.lyrics import (
    ContentProvenance,
    LocalLyricsStatus,
    LyricDocument,
    LyricDocumentKind,
    LyricsAlternative,
    LyricsAlternativeResult,
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
)
from konokashi.domain.lyrics_matching import CandidateMatchAssessment, assess_candidate
from konokashi.domain.normalization import parse_artist_credits, parse_title_version
from konokashi.domain.tracks import ResolvedTrack, TrackCandidate, semantic_duration_us

_CONFIDENCE_RANK = {
    LyricsMatchConfidence.HIGH: 0,
    LyricsMatchConfidence.MEDIUM: 1,
    LyricsMatchConfidence.LOW: 2,
    LyricsMatchConfidence.APPROVED: -1,
}
_RESULTS_CACHE_TTL = timedelta(days=7)
_NO_RESULT_CACHE_TTL = timedelta(hours=12)
_MAX_PARALLEL_PROVIDERS = 4


@dataclass(frozen=True, slots=True)
class LyricsSearchRequest:
    """Structured user evidence for read-only provider candidate inspection."""

    title: str
    artists: tuple[str, ...] = field(default_factory=tuple)
    album: str | None = None
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class LyricsSearchResult:
    """Provider-neutral manual search output with canonical match assessments."""

    request: LyricsSearchRequest
    alternatives: tuple[LyricsAlternative, ...] = field(default_factory=tuple)
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    cache_hit: bool = False
    network_used: bool = False


class LyricsResolver:
    """Apply accepted source precedence, matching, persistence, and offline policy."""

    def __init__(
        self,
        *,
        local_sources: Sequence[LocalLyricsProviderPort],
        provider: LyricsProviderPort | Sequence[LyricsProviderPort],
        provider_documents: LyricsCandidateDocumentPort,
        lyrics: LyricsRepositoryPort,
        matches: LyricsMatchRepositoryPort,
        provider_cache: ProviderCacheRepositoryPort,
        now: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        title_aliases: Callable[[str], tuple[str, ...]] | None = None,
    ) -> None:
        self._local_sources = tuple(local_sources)
        self._providers = (
            tuple(provider) if isinstance(provider, Sequence) else (provider,)
        )
        names = tuple(item.name for item in self._providers)
        if any(not name.strip() for name in names):
            raise ValueError("lyrics provider names must not be blank")
        if len(set(names)) != len(names):
            raise ValueError("lyrics provider names must be unique")
        self._provider_priority = {
            name: position for position, name in enumerate(names)
        }
        self._provider_documents = provider_documents
        self._lyrics = lyrics
        self._matches = matches
        self._provider_cache = provider_cache
        self._now = now or (lambda: datetime.now(UTC))
        self._sleeper = sleeper
        self._monotonic = monotonic
        self._title_aliases = title_aliases or (lambda _title: ())

    def cancel_inflight(self) -> None:
        """Cancel every provider request that exposes the optional cancellation API."""

        for provider in self._providers:
            cancellation = getattr(provider, "cancel_inflight", None)
            if callable(cancellation):
                cancellation()

    def _assess(
        self, query: LyricsQuery, candidate: LyricsProviderCandidate
    ) -> CandidateMatchAssessment:
        return assess_candidate(
            query,
            candidate,
            provider_title_aliases=self._title_aliases(candidate.track_name),
        )

    def resolve(
        self, track: ResolvedTrack, *, offline: bool = False, refresh: bool = False
    ) -> LyricsResolutionResult:
        """Resolve lyrics without letting network or refresh displace approved data."""

        source = track.source_identity
        diagnostics: list[str] = []
        invalid_local_seen = False
        current_match = self._matches.get(source)
        rejected_document_ids = {
            match.document_id for match in self._matches.rejections(source)
        }
        if (
            current_match is not None
            and current_match.decision is LyricsMatchDecision.REJECTED
        ):
            rejected_document_ids.add(current_match.document_id)
        current_document = (
            None
            if current_match is None
            else self._lyrics.get(current_match.document_id)
        )
        if current_match is not None and current_document is None:
            diagnostics.append("persisted lyrics match references a missing document")

        if (
            current_match is not None
            and current_document is not None
            and current_match.decision is LyricsMatchDecision.APPROVED
        ):
            return self._from_document(
                track,
                current_document,
                source_label=f"{current_document.source_name} (user-approved cache)",
                confidence=LyricsMatchConfidence.APPROVED,
                evidence=current_match.evidence,
                diagnostics=tuple(diagnostics),
                cache_hit=True,
            )

        for local_source in self._local_sources:
            local = local_source.load(track)
            diagnostics.extend(local.diagnostics)
            invalid_local_seen |= local.status is LocalLyricsStatus.INVALID
            if local.status is LocalLyricsStatus.FOUND and local.document is not None:
                if local.document.document_id in rejected_document_ids:
                    diagnostics.append(
                        f"skipped explicitly rejected local lyric result from "
                        f"{local.source_label}"
                    )
                    continue
                evidence = (
                    "lyrics came from an exact-path local recording source",
                    "local source outranks provider and automatic cache results",
                )
                self._persist(
                    track,
                    local.document,
                    LyricsMatchConfidence.HIGH,
                    evidence,
                    ContentProvenance.LOCAL,
                )
                return self._from_document(
                    track,
                    local.document,
                    source_label=local.source_label,
                    confidence=LyricsMatchConfidence.HIGH,
                    evidence=evidence,
                    diagnostics=tuple(diagnostics),
                )

        automatic_fallback: tuple[LyricDocument, LyricsMatch] | None = None
        if (
            current_match is not None
            and current_document is not None
            and current_match.decision is LyricsMatchDecision.CANDIDATE
            and current_match.confidence is LyricsMatchConfidence.HIGH
        ):
            if not refresh:
                return self._from_document(
                    track,
                    current_document,
                    source_label=f"{current_document.source_name} (persistent match)",
                    confidence=current_match.confidence,
                    evidence=current_match.evidence,
                    diagnostics=tuple(diagnostics),
                    cache_hit=True,
                )
            automatic_fallback = (current_document, current_match)

        queries = _queries(track)
        if not queries:
            diagnostics.append(
                "provider lookup skipped because resolved title or musical artist "
                "is missing"
            )
            return self._fallback_or_state(
                track,
                automatic_fallback,
                (
                    LyricsResolutionStatus.INVALID_LOCAL_LYRICS
                    if invalid_local_seen
                    else LyricsResolutionStatus.NO_RESULT
                ),
                tuple(diagnostics),
                network_used=False,
            )

        query = queries[0]
        all_assessments: list[CandidateMatchAssessment] = []
        provider_outcomes: list[LyricsProviderResult] = []
        network_used = False
        cache_hit = False

        exact_possible = query.album is not None and query.duration_ms is not None
        if exact_possible:
            for exact, exact_cached, exact_network in self._provider_results(
                query, search=False, offline=offline, refresh=refresh
            ):
                provider_outcomes.append(exact)
                cache_hit |= exact_cached
                network_used |= exact_network
                diagnostics.extend(exact.diagnostics)
                if exact.status is LyricsProviderStatus.RESULTS:
                    all_assessments.extend(
                        self._assess(query, candidate)
                        for candidate in exact.candidates
                        if self._provider_documents.document_id(candidate)
                        not in rejected_document_ids
                    )
        else:
            missing = []
            if query.album is None:
                missing.append("album")
            if query.duration_ms is None:
                missing.append("duration")
            diagnostics.append(
                "exact provider lookup skipped; missing " + ", ".join(missing)
            )

        for strategy, search_query, assessment_query in _candidate_search_plan(queries):
            diagnostics.append(
                f"provider strategy: {strategy} "
                f"[interpretation: {assessment_query.strategy}]"
            )
            if not offline and network_used:
                self._sleeper(0.2)
            strategy_candidates = 0
            for search, search_cached, search_network in self._provider_results(
                search_query,
                search=True,
                offline=offline,
                refresh=False if offline else refresh,
            ):
                provider_outcomes.append(search)
                network_used |= search_network
                cache_hit |= search_cached
                diagnostics.extend(search.diagnostics)
                strategy_candidates += len(search.candidates)
                if search.status is LyricsProviderStatus.RESULTS:
                    all_assessments.extend(
                        self._assess(assessment_query, candidate)
                        for candidate in search.candidates
                        if self._provider_documents.document_id(candidate)
                        not in rejected_document_ids
                    )
            diagnostics.append(
                f"provider strategy {strategy!r} returned "
                f"{strategy_candidates} candidates"
            )
        assessments = _deduplicate_assessments(all_assessments)
        accepted = self._unique_high(assessments)
        if accepted is not None:
            return self._accept_provider(
                track,
                accepted,
                diagnostics,
                cache_hit=cache_hit,
                network_used=network_used,
            )
        if assessments:
            diagnostics.extend(
                _assessment_diagnostic(item) for item in self._ordered(assessments)
            )
            ordered = tuple(item.candidate for item in self._ordered(assessments))
            diagnostics.append(
                "provider candidates did not meet the unique High-confidence policy"
            )
            return self._fallback_or_state(
                track,
                automatic_fallback,
                LyricsResolutionStatus.AMBIGUOUS,
                tuple(diagnostics),
                alternatives=ordered,
                network_used=network_used,
                cache_hit=cache_hit,
            )
        terminal, retry_after = self._provider_terminal_state(provider_outcomes)
        if terminal is not None and not offline:
            return self._fallback_or_state(
                track,
                automatic_fallback,
                terminal,
                tuple(diagnostics),
                network_used=network_used,
                cache_hit=cache_hit,
                retry_after=retry_after,
            )
        if invalid_local_seen:
            final_status = LyricsResolutionStatus.INVALID_LOCAL_LYRICS
        elif offline:
            final_status = LyricsResolutionStatus.OFFLINE_MISS
        else:
            final_status = LyricsResolutionStatus.NO_RESULT
        return self._fallback_or_state(
            track,
            automatic_fallback,
            final_status,
            tuple(diagnostics),
            network_used=network_used,
            cache_hit=cache_hit,
        )

    def alternatives(
        self,
        track: ResolvedTrack,
        *,
        offline: bool = False,
        refresh: bool = False,
        title: str | None = None,
        artists: tuple[str, ...] = (),
    ) -> LyricsAlternativeResult:
        """Return reviewable provider candidates without changing match decisions."""

        queries = (
            _manual_queries(track, title, artists)
            if title is not None
            else _queries(track)
        )
        if not queries:
            return LyricsAlternativeResult(
                track.source_identity,
                diagnostics=(
                    "alternative lookup skipped because resolved title or musical "
                    "artist is missing",
                ),
            )
        query = queries[0]
        assessments, diagnostics, cache_hit, network_used = (
            self._search_provider_candidates(
                queries,
                offline=offline,
                refresh=refresh,
                exact_skip_context="alternative",
            )
        )

        current_match = self._matches.get(track.source_identity)
        current_document_id = (
            None if current_match is None else current_match.document_id
        )
        rejected_document_ids = {
            match.document_id
            for match in self._matches.rejections(track.source_identity)
        }
        if (
            current_match is not None
            and current_match.decision is LyricsMatchDecision.REJECTED
        ):
            rejected_document_ids.add(current_match.document_id)
        alternatives = tuple(
            LyricsAlternative(
                self._provider_documents.document_id(assessment.candidate),
                assessment.candidate,
                assessment.confidence,
                assessment.evidence,
                current=(
                    self._provider_documents.document_id(assessment.candidate)
                    == current_document_id
                ),
                rejected=(
                    self._provider_documents.document_id(assessment.candidate)
                    in rejected_document_ids
                ),
                text_confidence=assessment.text_confidence,
                timing_confidence=assessment.timing_confidence,
                strategy=assessment.query_strategy,
            )
            for assessment in self._ordered(_deduplicate_assessments(assessments))
        )
        return LyricsAlternativeResult(
            track.source_identity,
            alternatives,
            tuple(diagnostics),
            cache_hit,
            network_used,
            query.title,
            query.artists,
        )

    def search(
        self,
        request: LyricsSearchRequest,
        *,
        offline: bool = False,
        refresh: bool = False,
    ) -> LyricsSearchResult:
        """Inspect candidates through the same provider cache and scoring policy."""

        title = request.title.strip()
        artists = tuple(value.strip() for value in request.artists if value.strip())
        album = None if request.album is None else request.album.strip() or None
        cleaned = LyricsSearchRequest(title, artists, album, request.duration_ms)
        if not title:
            return LyricsSearchResult(
                cleaned, diagnostics=("manual lyric search requires a title",)
            )
        credit = parse_artist_credits(artists)
        provenance = [("title", "manual-search")]
        if artists:
            provenance.append(("artists", "manual-search"))
        if album:
            provenance.append(("album", "manual-search"))
        if request.duration_ms is not None:
            provenance.append(("duration", "manual-search"))
        query = LyricsQuery(
            title=title,
            artists=artists,
            album=album,
            duration_ms=request.duration_ms,
            broad=not artists,
            main_artists=credit.main_artists,
            contributors=credit.contributors,
            strategy="manual-search",
            provenance=tuple(provenance),
        )
        queries = (query,)
        assessments, diagnostics, cache_hit, network_used = (
            self._search_provider_candidates(
                queries,
                offline=offline,
                refresh=refresh,
                exact_skip_context="manual search",
                search_plan=(
                    (("broad title query", query, query),) if not artists else None
                ),
            )
        )
        alternatives = tuple(
            LyricsAlternative(
                document_id=self._provider_documents.document_id(assessment.candidate),
                candidate=assessment.candidate,
                confidence=assessment.confidence,
                evidence=assessment.evidence,
                text_confidence=assessment.text_confidence,
                timing_confidence=assessment.timing_confidence,
                strategy=assessment.query_strategy,
            )
            for assessment in self._ordered(_deduplicate_assessments(assessments))
        )
        return LyricsSearchResult(
            cleaned,
            alternatives,
            tuple(diagnostics),
            cache_hit,
            network_used,
        )

    def _search_provider_candidates(
        self,
        queries: Sequence[LyricsQuery],
        *,
        offline: bool,
        refresh: bool,
        exact_skip_context: str,
        search_plan: Sequence[tuple[str, LyricsQuery, LyricsQuery]] | None = None,
    ) -> tuple[list[CandidateMatchAssessment], list[str], bool, bool]:
        """Run the shared exact/search/cache path without selecting a winner."""

        query = queries[0]
        diagnostics: list[str] = []
        assessments: list[CandidateMatchAssessment] = []
        cache_hit = False
        network_used = False
        if query.album is not None and query.duration_ms is not None and query.artists:
            for exact, cached, network in self._provider_results(
                query, search=False, offline=offline, refresh=refresh
            ):
                cache_hit |= cached
                network_used |= network
                diagnostics.extend(exact.diagnostics)
                if exact.status is LyricsProviderStatus.RESULTS:
                    assessments.extend(
                        self._assess(query, candidate) for candidate in exact.candidates
                    )
                elif exact.status is not LyricsProviderStatus.NO_RESULT:
                    diagnostics.append(f"exact lookup ended as {exact.status.value}")
        else:
            diagnostics.append(
                f"exact {exact_skip_context} lookup skipped because artist, album, "
                "or duration is missing"
            )
        plan = _candidate_search_plan(queries) if search_plan is None else search_plan
        for strategy, search_query, assessment_query in plan:
            diagnostics.append(
                f"provider strategy: {strategy} "
                f"[interpretation: {assessment_query.strategy}]"
            )
            if not offline and network_used:
                self._sleeper(0.2)
            for search, cached, network in self._provider_results(
                search_query,
                search=True,
                offline=offline,
                refresh=False if offline else refresh,
            ):
                network_used |= network
                cache_hit |= cached
                diagnostics.extend(search.diagnostics)
                if search.status is LyricsProviderStatus.RESULTS:
                    assessments.extend(
                        self._assess(assessment_query, candidate)
                        for candidate in search.candidates
                    )
                elif search.status is not LyricsProviderStatus.NO_RESULT:
                    diagnostics.append(
                        f"{strategy} lookup ended as {search.status.value}"
                    )
        return assessments, diagnostics, cache_hit, network_used

    def _provider_results(
        self,
        query: LyricsQuery,
        *,
        search: bool,
        offline: bool,
        refresh: bool,
    ) -> tuple[tuple[LyricsProviderResult, bool, bool], ...]:
        """Query configured sources concurrently while preserving preference order."""

        if not self._providers:
            return ()

        def load(
            provider: LyricsProviderPort,
        ) -> tuple[LyricsProviderResult, bool, bool]:
            return self._provider_result(
                provider,
                query,
                search=search,
                offline=offline,
                refresh=refresh,
            )

        if len(self._providers) == 1:
            return (load(self._providers[0]),)
        with ThreadPoolExecutor(
            max_workers=min(len(self._providers), _MAX_PARALLEL_PROVIDERS),
            thread_name_prefix="konokashi-lyrics",
        ) as executor:
            return tuple(executor.map(load, self._providers))

    def _provider_result(
        self,
        provider: LyricsProviderPort,
        query: LyricsQuery,
        *,
        search: bool,
        offline: bool,
        refresh: bool,
    ) -> tuple[LyricsProviderResult, bool, bool]:
        started = self._monotonic()
        cache_key = provider_cache_key(provider.name, query, search=search)
        if not refresh:
            cached = self._provider_cache.get(provider.name, cache_key)
            if cached is not None:
                current = cached.expires_at is None or cached.expires_at > self._now()
                parsed = self._validated_provider_result(
                    provider, provider.parse_cached(cached.payload, search=search)
                )
                if current:
                    return (
                        self._observed(provider.name, parsed, started, "cache hit"),
                        True,
                        False,
                    )
                if offline and parsed.status is LyricsProviderStatus.RESULTS:
                    return (
                        self._observed(
                            provider.name,
                            replace(
                                parsed,
                                diagnostics=(
                                    *parsed.diagnostics,
                                    "offline mode: using explicitly stale positive "
                                    "provider-cache fallback",
                                ),
                            ),
                            started,
                            "stale cache hit",
                        ),
                        True,
                        False,
                    )
        if offline:
            return (
                self._observed(
                    provider.name,
                    LyricsProviderResult(
                        LyricsProviderStatus.NO_RESULT,
                        diagnostics=("offline mode: no matching provider cache entry",),
                    ),
                    started,
                    "offline cache miss",
                ),
                False,
                False,
            )
        try:
            result = provider.search(query) if search else provider.exact(query)
        except Exception as error:
            result = LyricsProviderResult(
                LyricsProviderStatus.UNAVAILABLE,
                diagnostics=(
                    f"{provider.name} adapter failed as {error.__class__.__name__}",
                ),
            )
        result = self._validated_provider_result(provider, result)
        if result.raw_payload is not None and result.status in {
            LyricsProviderStatus.RESULTS,
            LyricsProviderStatus.NO_RESULT,
        }:
            ttl = (
                _RESULTS_CACHE_TTL
                if result.status is LyricsProviderStatus.RESULTS
                else _NO_RESULT_CACHE_TTL
            )
            retrieved_at = self._now()
            self._provider_cache.put(
                ProviderCacheEntry(
                    provider.name,
                    cache_key,
                    result.raw_payload,
                    retrieved_at,
                    retrieved_at + ttl,
                )
            )
        return self._observed(provider.name, result, started, "queried"), False, True

    @staticmethod
    def _validated_provider_result(
        provider: LyricsProviderPort, result: LyricsProviderResult
    ) -> LyricsProviderResult:
        if (
            result.status is LyricsProviderStatus.RESULTS and not result.candidates
        ) or (result.status is not LyricsProviderStatus.RESULTS and result.candidates):
            return LyricsProviderResult(
                LyricsProviderStatus.INVALID_RESPONSE,
                diagnostics=(
                    f"{provider.name} returned an inconsistent result status",
                ),
            )
        if result.status is LyricsProviderStatus.RESULTS and any(
            candidate.provider != provider.name for candidate in result.candidates
        ):
            return LyricsProviderResult(
                LyricsProviderStatus.INVALID_RESPONSE,
                diagnostics=(
                    f"{provider.name} returned a candidate with mismatched provenance",
                ),
            )
        return result

    def _observed(
        self,
        provider_name: str,
        result: LyricsProviderResult,
        started: float,
        operation: str,
    ) -> LyricsProviderResult:
        elapsed_ms = max(0, round((self._monotonic() - started) * 1000))
        observation = (
            f"provider {provider_name}: {operation} in {elapsed_ms} ms; "
            f"status={result.status.value}; results={len(result.candidates)}"
        )
        return replace(result, diagnostics=(observation, *result.diagnostics))

    def _accept_provider(
        self,
        track: ResolvedTrack,
        assessment: CandidateMatchAssessment,
        diagnostics: list[str],
        *,
        cache_hit: bool,
        network_used: bool,
    ) -> LyricsResolutionResult:
        diagnostics.append("chosen " + _assessment_diagnostic(assessment))
        candidate = assessment.candidate
        if (
            candidate.synced_lyrics is not None
            and assessment.timing_confidence is not LyricsMatchConfidence.HIGH
        ):
            if candidate.plain_lyrics is None:
                diagnostics.append(
                    "candidate lyric text matched, but recording timing was not "
                    "trusted and no plain lyrics were available"
                )
                return LyricsResolutionResult(
                    track.source_identity,
                    LyricsResolutionStatus.AMBIGUOUS,
                    confidence=assessment.text_confidence,
                    evidence=assessment.evidence,
                    diagnostics=tuple(diagnostics),
                    cache_hit=cache_hit,
                    network_used=network_used,
                )
            diagnostics.append(
                "accepted lyric text but discarded synchronized timing because "
                "recording timing confidence was insufficient"
            )
            candidate = replace(candidate, synced_lyrics=None, parsed_lyrics=None)
        document, document_diagnostics = self._provider_documents.build(
            candidate, self._now()
        )
        diagnostics.extend(document_diagnostics)
        if document is None:
            return LyricsResolutionResult(
                track.source_identity,
                LyricsResolutionStatus.INVALID_PROVIDER_RESPONSE,
                confidence=assessment.confidence,
                evidence=assessment.evidence,
                diagnostics=tuple(diagnostics),
                cache_hit=cache_hit,
                network_used=network_used,
            )
        self._persist(
            track,
            document,
            assessment.confidence,
            assessment.evidence,
            ContentProvenance.PROVIDER,
        )
        return self._from_document(
            track,
            document,
            source_label=assessment.candidate.provider,
            confidence=assessment.confidence,
            evidence=assessment.evidence,
            diagnostics=tuple(diagnostics),
            cache_hit=cache_hit,
            network_used=network_used,
        )

    def _persist(
        self,
        track: ResolvedTrack,
        document: LyricDocument,
        confidence: LyricsMatchConfidence,
        evidence: tuple[str, ...],
        provenance: ContentProvenance,
    ) -> None:
        self._lyrics.put(document)
        if track.source_identity.persistence_scope is PersistenceScope.PERMANENT:
            self._matches.put(
                track.source_identity,
                LyricsMatch(
                    document.document_id,
                    LyricsMatchDecision.CANDIDATE,
                    provenance,
                    self._now(),
                    confidence,
                    evidence,
                ),
            )

    def _unique_high(
        self,
        assessments: Sequence[CandidateMatchAssessment],
    ) -> CandidateMatchAssessment | None:
        high = self._ordered(
            [
                assessment
                for assessment in _deduplicate_assessments(assessments)
                if assessment.confidence is LyricsMatchConfidence.HIGH
            ]
        )
        if len(high) == 1:
            return high[0]
        if len(high) > 1:
            first_relation = _title_relation_rank(high[0])
            if all(first_relation < _title_relation_rank(other) for other in high[1:]):
                return high[0]
            if high[0].candidate.synced_lyrics and all(
                _same_recording_fields(high[0], other)
                and not other.candidate.synced_lyrics
                for other in high[1:]
            ):
                return high[0]
            if all(
                _same_recording_fields(high[0], other)
                and _same_lyric_content(high[0], other)
                for other in high[1:]
            ) and len({item.candidate.provider for item in high}) == len(high):
                return high[0]
        return None

    def _ordered(
        self, assessments: Sequence[CandidateMatchAssessment]
    ) -> list[CandidateMatchAssessment]:
        return sorted(
            assessments,
            key=lambda item: (
                *_assessment_sort_key(item)[:-1],
                self._provider_priority.get(
                    item.candidate.provider, len(self._provider_priority)
                ),
                (
                    _CONFIDENCE_RANK[item.candidate.provider_confidence]
                    if item.candidate.provider_confidence is not None
                    else _CONFIDENCE_RANK[LyricsMatchConfidence.LOW] + 1
                ),
                item.candidate.record_id,
            ),
        )

    @staticmethod
    def _provider_terminal_state(
        results: Sequence[LyricsProviderResult],
    ) -> tuple[LyricsResolutionStatus | None, int | None]:
        statuses = {result.status for result in results}
        if not statuses:
            return None, None
        if LyricsProviderStatus.RATE_LIMITED in statuses:
            retries = tuple(
                result.retry_after_seconds
                for result in results
                if result.status is LyricsProviderStatus.RATE_LIMITED
                and result.retry_after_seconds is not None
            )
            return LyricsResolutionStatus.RATE_LIMITED, min(retries, default=None)
        if LyricsProviderStatus.UNAVAILABLE in statuses:
            return LyricsResolutionStatus.PROVIDER_UNAVAILABLE, None
        if LyricsProviderStatus.INVALID_RESPONSE in statuses:
            return LyricsResolutionStatus.INVALID_PROVIDER_RESPONSE, None
        return None, None

    def _fallback_or_state(
        self,
        track: ResolvedTrack,
        fallback: tuple[LyricDocument, LyricsMatch] | None,
        status: LyricsResolutionStatus,
        diagnostics: tuple[str, ...],
        *,
        alternatives: tuple[LyricsProviderCandidate, ...] = (),
        network_used: bool,
        cache_hit: bool = False,
        retry_after: int | None = None,
    ) -> LyricsResolutionResult:
        if fallback is not None:
            document, match = fallback
            return self._from_document(
                track,
                document,
                source_label=f"{document.source_name} (preserved after refresh)",
                confidence=match.confidence,
                evidence=match.evidence,
                diagnostics=(
                    *diagnostics,
                    f"refresh ended as {status.value}; previous usable lyrics "
                    "were preserved",
                ),
                cache_hit=True,
                network_used=network_used,
            )
        return LyricsResolutionResult(
            track.source_identity,
            status,
            diagnostics=diagnostics,
            alternatives=alternatives,
            cache_hit=cache_hit,
            network_used=network_used,
            retry_after_seconds=retry_after,
        )

    @staticmethod
    def _from_document(
        track: ResolvedTrack,
        document: LyricDocument,
        *,
        source_label: str,
        confidence: LyricsMatchConfidence,
        evidence: tuple[str, ...],
        diagnostics: tuple[str, ...],
        cache_hit: bool = False,
        network_used: bool = False,
    ) -> LyricsResolutionResult:
        status = {
            LyricDocumentKind.SYNCED: LyricsResolutionStatus.FOUND_TIMED,
            LyricDocumentKind.PLAIN: LyricsResolutionStatus.FOUND_UNTIMED,
            LyricDocumentKind.INSTRUMENTAL: LyricsResolutionStatus.INSTRUMENTAL,
        }[document.kind]
        return LyricsResolutionResult(
            track.source_identity,
            status,
            document,
            source_label,
            confidence,
            evidence,
            diagnostics,
            cache_hit=cache_hit,
            network_used=network_used,
        )


def provider_cache_key(provider: str, query: LyricsQuery, *, search: bool) -> str:
    """Hash only provider-safe metadata; never include a local path or MPRIS URL."""

    payload = json.dumps(
        {
            "provider": provider,
            "mode": "search" if search else "exact",
            "title": query.title,
            "artists": query.artists,
            "album": query.album,
            "duration_ms": query.duration_ms,
            "broad": query.broad,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(payload).hexdigest()


def _candidate_query(
    track: ResolvedTrack, candidate: TrackCandidate
) -> LyricsQuery | None:
    title = candidate.title
    artists = tuple(artist for artist in candidate.artists if artist.strip())
    if title is None or not title.strip() or not artists:
        return None
    duration = semantic_duration_us(candidate.duration_us)
    credit = candidate.artist_credit
    return LyricsQuery(
        title,
        artists,
        candidate.album if candidate.album else None,
        None if duration is None else (duration + 500) // 1000,
        source_confidence=track.confidence.value,
        main_artists=() if credit is None else credit.main_artists,
        contributors=() if credit is None else credit.contributors,
        strategy=candidate.strategy,
        provenance=candidate.field_provenance,
    )


def _queries(track: ResolvedTrack) -> tuple[LyricsQuery, ...]:
    candidates = (
        (track.candidate,)
        if track.user_approved or not track.interpretation_candidates
        else track.interpretation_candidates
    )
    result: list[LyricsQuery] = []
    seen: set[tuple[object, ...]] = set()
    for candidate in candidates:
        query = _candidate_query(track, candidate)
        if query is None:
            continue
        key = (
            query.title,
            query.artists,
            query.album,
            query.duration_ms,
            query.main_artists,
            query.contributors,
        )
        if key not in seen:
            seen.add(key)
            result.append(query)
    return tuple(result)


def _manual_queries(
    track: ResolvedTrack,
    title: str | None,
    artists: tuple[str, ...],
) -> tuple[LyricsQuery, ...]:
    """Build one bounded review-only query without mutating raw track metadata."""

    cleaned_title = "" if title is None else title.strip()
    cleaned_artists = tuple(item.strip() for item in artists if item.strip())
    if not cleaned_title or not cleaned_artists:
        return ()
    duration = semantic_duration_us(track.candidate.duration_us)
    credit = parse_artist_credits(cleaned_artists)
    return (
        LyricsQuery(
            cleaned_title,
            cleaned_artists,
            None,
            None if duration is None else (duration + 500) // 1000,
            source_confidence=track.confidence.value,
            main_artists=credit.main_artists,
            contributors=credit.contributors,
            strategy="manual-review-search",
            provenance=(
                ("title", "manual-review-search"),
                ("artists", "manual-review-search"),
                *((("duration", "mpris-duration"),) if duration is not None else ()),
            ),
        ),
    )


def _search_queries(query: LyricsQuery) -> tuple[tuple[str, LyricsQuery], ...]:
    """Build bounded normalized, base-title, then broad provider strategies."""

    strategies: list[tuple[str, LyricsQuery]] = [("normalized title and artist", query)]
    title = parse_title_version(query.title)
    if title.qualifier is not None and title.base_title != query.title:
        strategies.append(
            (
                "base title and artist",
                replace(query, title=title.base_title, album=None),
            )
        )
    strategies.append(
        ("broader artist catalogue", replace(query, album=None, broad=True))
    )
    return tuple(strategies)


def _candidate_search_plan(
    queries: Sequence[LyricsQuery],
) -> tuple[tuple[str, LyricsQuery, LyricsQuery], ...]:
    """Build bounded lookups while retaining each scoring interpretation."""

    plan: list[tuple[str, LyricsQuery, LyricsQuery]] = []
    seen: set[tuple[object, ...]] = set()
    for assessment_query in queries:
        for strategy, search_query in _search_queries(assessment_query):
            key = (
                search_query.title,
                search_query.artists,
                search_query.album,
                search_query.duration_ms,
                search_query.broad,
                assessment_query.main_artists,
                assessment_query.contributors,
            )
            if key not in seen:
                seen.add(key)
                plan.append((strategy, search_query, assessment_query))
    return tuple(plan)


def _assessment_sort_key(
    item: CandidateMatchAssessment,
) -> tuple[int, int, int, int, str]:
    return (
        _CONFIDENCE_RANK[item.confidence],
        _title_relation_rank(item),
        0 if item.candidate.synced_lyrics else 1,
        (
            item.duration_difference_ms
            if item.duration_difference_ms is not None
            else 2**63
        ),
        item.candidate.record_id,
    )


def _same_recording_fields(
    first: CandidateMatchAssessment,
    second: CandidateMatchAssessment,
) -> bool:
    return (
        first.candidate.track_name == second.candidate.track_name
        and first.candidate.artist_name == second.candidate.artist_name
        and first.candidate.album_name == second.candidate.album_name
        and first.candidate.duration_ms == second.candidate.duration_ms
    )


def _same_lyric_content(
    first: CandidateMatchAssessment,
    second: CandidateMatchAssessment,
) -> bool:
    def normalized(value: str | None) -> str | None:
        if value is None:
            return None
        lines = value.replace("\r\n", "\n").split("\n")
        return "\n".join(line.rstrip() for line in lines)

    return (
        normalized(first.candidate.synced_lyrics)
        == normalized(second.candidate.synced_lyrics)
        and normalized(first.candidate.plain_lyrics)
        == normalized(second.candidate.plain_lyrics)
        and first.candidate.instrumental == second.candidate.instrumental
    )


def _title_relation_rank(assessment: CandidateMatchAssessment) -> int:
    return {
        "exact-raw": 0,
        "normalized": 1,
        "base-title": 2,
        "phonetic-transliteration": 3,
    }.get(assessment.title_relation, 4)


def _assessment_diagnostic(assessment: CandidateMatchAssessment) -> str:
    return (
        f"candidate {assessment.candidate.record_id}: "
        f"overall={assessment.confidence.value}, "
        f"title={assessment.title_relation}, "
        f"text={assessment.text_confidence.value}, "
        f"timing={assessment.timing_confidence.value}; "
        + "; ".join(assessment.evidence)
    )


def _deduplicate_assessments(
    assessments: Sequence[CandidateMatchAssessment],
) -> list[CandidateMatchAssessment]:
    by_record: dict[tuple[str, str], CandidateMatchAssessment] = {}
    for assessment in assessments:
        key = (assessment.candidate.provider, assessment.candidate.record_id)
        existing = by_record.get(key)
        if existing is None or _assessment_sort_key(assessment) < _assessment_sort_key(
            existing
        ):
            by_record[key] = assessment
    return list(by_record.values())
