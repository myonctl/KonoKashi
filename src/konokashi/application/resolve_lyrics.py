"""Local-first, explainable lyrics resolution for one already-resolved track."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
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
from konokashi.domain.tracks import ResolvedTrack, TrackCandidate

_CONFIDENCE_RANK = {
    LyricsMatchConfidence.HIGH: 0,
    LyricsMatchConfidence.MEDIUM: 1,
    LyricsMatchConfidence.LOW: 2,
    LyricsMatchConfidence.APPROVED: -1,
}
_RESULTS_CACHE_TTL = timedelta(days=7)
_NO_RESULT_CACHE_TTL = timedelta(hours=12)


class LyricsResolver:
    """Apply accepted source precedence, matching, persistence, and offline policy."""

    def __init__(
        self,
        *,
        local_sources: Sequence[LocalLyricsProviderPort],
        provider: LyricsProviderPort,
        provider_documents: LyricsCandidateDocumentPort,
        lyrics: LyricsRepositoryPort,
        matches: LyricsMatchRepositoryPort,
        provider_cache: ProviderCacheRepositoryPort,
        now: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        title_aliases: Callable[[str], tuple[str, ...]] | None = None,
    ) -> None:
        self._local_sources = tuple(local_sources)
        self._provider = provider
        self._provider_documents = provider_documents
        self._lyrics = lyrics
        self._matches = matches
        self._provider_cache = provider_cache
        self._now = now or (lambda: datetime.now(UTC))
        self._sleeper = sleeper
        self._title_aliases = title_aliases or (lambda _title: ())

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
        network_used = False
        cache_hit = False

        exact_possible = query.album is not None and query.duration_ms is not None
        if exact_possible:
            exact, exact_cached, exact_network = self._provider_result(
                query, search=False, offline=offline, refresh=refresh
            )
            cache_hit |= exact_cached
            network_used |= exact_network
            diagnostics.extend(exact.diagnostics)
            terminal = self._provider_terminal_status(exact)
            if terminal is not None:
                return self._fallback_or_state(
                    track,
                    automatic_fallback,
                    terminal,
                    tuple(diagnostics),
                    network_used=network_used,
                    cache_hit=cache_hit,
                    retry_after=exact.retry_after_seconds,
                )
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
                "exact LRCLIB lookup skipped; missing " + ", ".join(missing)
            )

        for strategy, search_query, assessment_query in _candidate_search_plan(queries):
            diagnostics.append(
                f"provider strategy: {strategy} "
                f"[interpretation: {assessment_query.strategy}]"
            )
            if offline:
                search, search_cached, search_network = self._provider_result(
                    search_query, search=True, offline=True, refresh=False
                )
            else:
                if network_used:
                    self._sleeper(0.2)
                search, search_cached, search_network = self._provider_result(
                    search_query, search=True, offline=False, refresh=refresh
                )
            network_used |= search_network
            cache_hit |= search_cached
            diagnostics.extend(search.diagnostics)
            diagnostics.append(
                f"provider strategy {strategy!r} returned "
                f"{len(search.candidates)} candidates"
            )
            terminal = self._provider_terminal_status(search)
            if terminal is not None:
                return self._fallback_or_state(
                    track,
                    automatic_fallback,
                    terminal,
                    tuple(diagnostics),
                    network_used=network_used,
                    cache_hit=cache_hit,
                    retry_after=search.retry_after_seconds,
                )
            if search.status is LyricsProviderStatus.RESULTS:
                all_assessments.extend(
                    self._assess(assessment_query, candidate)
                    for candidate in search.candidates
                    if self._provider_documents.document_id(candidate)
                    not in rejected_document_ids
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
                _assessment_diagnostic(item) for item in _ordered(assessments)
            )
            ordered = tuple(item.candidate for item in _ordered(assessments))
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
        diagnostics: list[str] = []
        assessments: list[CandidateMatchAssessment] = []
        cache_hit = False
        network_used = False
        if query.album is not None and query.duration_ms is not None:
            exact, cached, network = self._provider_result(
                query, search=False, offline=offline, refresh=refresh
            )
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
                "exact alternative lookup skipped because album or duration is missing"
            )
        for strategy, search_query, assessment_query in _candidate_search_plan(queries):
            diagnostics.append(
                f"provider strategy: {strategy} "
                f"[interpretation: {assessment_query.strategy}]"
            )
            if offline:
                search, cached, network = self._provider_result(
                    search_query, search=True, offline=True, refresh=False
                )
            else:
                if network_used:
                    self._sleeper(0.2)
                search, cached, network = self._provider_result(
                    search_query, search=True, offline=False, refresh=refresh
                )
            network_used |= network
            cache_hit |= cached
            diagnostics.extend(search.diagnostics)
            if search.status is LyricsProviderStatus.RESULTS:
                assessments.extend(
                    self._assess(assessment_query, candidate)
                    for candidate in search.candidates
                )
            elif search.status is not LyricsProviderStatus.NO_RESULT:
                diagnostics.append(f"{strategy} lookup ended as {search.status.value}")

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
            for assessment in _ordered(_deduplicate_assessments(assessments))
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

    def _provider_result(
        self,
        query: LyricsQuery,
        *,
        search: bool,
        offline: bool,
        refresh: bool,
    ) -> tuple[LyricsProviderResult, bool, bool]:
        cache_key = provider_cache_key(self._provider.name, query, search=search)
        if not refresh:
            cached = self._provider_cache.get(self._provider.name, cache_key)
            if cached is not None:
                current = cached.expires_at is None or cached.expires_at > self._now()
                parsed = self._provider.parse_cached(cached.payload, search=search)
                if current:
                    return parsed, True, False
                if offline and parsed.status is LyricsProviderStatus.RESULTS:
                    return (
                        replace(
                            parsed,
                            diagnostics=(
                                *parsed.diagnostics,
                                "offline mode: using explicitly stale positive "
                                "provider-cache fallback",
                            ),
                        ),
                        True,
                        False,
                    )
        if offline:
            return (
                LyricsProviderResult(
                    LyricsProviderStatus.NO_RESULT,
                    diagnostics=("offline mode: no matching provider cache entry",),
                ),
                False,
                False,
            )
        result = self._provider.search(query) if search else self._provider.exact(query)
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
                    self._provider.name,
                    cache_key,
                    result.raw_payload,
                    retrieved_at,
                    retrieved_at + ttl,
                )
            )
        return result, False, True

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
            candidate = replace(candidate, synced_lyrics=None)
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

    @staticmethod
    def _unique_high(
        assessments: Sequence[CandidateMatchAssessment],
    ) -> CandidateMatchAssessment | None:
        high = _ordered(
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
        return None

    @staticmethod
    def _provider_terminal_status(
        result: LyricsProviderResult,
    ) -> LyricsResolutionStatus | None:
        if result.status is LyricsProviderStatus.RATE_LIMITED:
            return LyricsResolutionStatus.RATE_LIMITED
        if result.status is LyricsProviderStatus.UNAVAILABLE:
            return LyricsResolutionStatus.PROVIDER_UNAVAILABLE
        if result.status is LyricsProviderStatus.INVALID_RESPONSE:
            return LyricsResolutionStatus.INVALID_PROVIDER_RESPONSE
        return None

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
    duration = candidate.duration_us
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
    duration = track.candidate.duration_us
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


def _ordered(
    assessments: Sequence[CandidateMatchAssessment],
) -> list[CandidateMatchAssessment]:
    return sorted(assessments, key=_assessment_sort_key)


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
