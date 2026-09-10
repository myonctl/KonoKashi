#!/usr/bin/env python3
"""Replay sanitized or private matching cases through KonoKashi's real policy."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from konokashi.application.ports import LocalPathResolution
from konokashi.application.resolve_lyrics import LyricsResolver
from konokashi.application.resolve_track import TrackResolver
from konokashi.application.source_identity import SourceIdentityResolver
from konokashi.domain.lyrics import (
    ContentProvenance,
    LocalLyricsResult,
    LocalLyricsStatus,
    LyricDocument,
    LyricDocumentKind,
    LyricsMatch,
    LyricsMatchConfidence,
    LyricsMatchDecision,
    LyricsProviderCandidate,
    LyricsProviderResult,
    LyricsProviderStatus,
    LyricsQuery,
    LyricsTextParseStatus,
)
from konokashi.domain.models import (
    PlayerCapabilities,
    PlayerSnapshot,
    RawTrackMetadata,
)
from konokashi.domain.tracks import ApprovedTrackIdentity, ResolvedTrack
from konokashi.infrastructure.lyrics.documents import build_lyric_document
from konokashi.infrastructure.lyrics.lrc import parse_lyrics_text
from konokashi.infrastructure.lyrics.provider_documents import (
    ProviderLyricDocumentBuilder,
)
from konokashi.infrastructure.storage.bootstrap import StorageRepositories, open_storage

SCHEMA_VERSION = 1
FIXED_NOW = datetime(2026, 1, 1, tzinfo=UTC)
MAX_CASES_PER_FILE = 10_000
MAX_FIXTURE_BYTES = 16_000_000

_ACCEPTED_STATUSES = frozenset({"Timed", "Untimed", "Instrumental"})
_FALLBACK_CATEGORIES = frozenset(
    {
        "plain-only-fallback",
        "synced-timing-rejected",
        "local-override-wins",
        "approved-user-match-wins",
        "rejected-result-remains-rejected",
        "provider-search-fallback",
    }
)
_OUTCOME_CATEGORIES = frozenset(
    {
        "automatic-accept",
        "ambiguous",
        "reject",
        "plain-only-fallback",
        "synced-trusted",
        "synced-timing-rejected",
        "local-override-wins",
        "approved-user-match-wins",
        "rejected-result-remains-rejected",
        "provider-search-fallback",
    }
)


class EvaluationInputError(ValueError):
    """A corpus file is malformed or unsafe to replay."""


@dataclass(frozen=True, slots=True)
class ExpectedOutcome:
    """Explicit oracle for one sanitized or private case."""

    category: str
    status: str
    record_id: str | None
    timing: str
    origin: str
    network_used: bool


@dataclass(frozen=True, slots=True)
class CandidateFactorReport:
    """Structured projection of the real resolver's candidate assessment."""

    record_id: str
    document_id: str
    confidence: str
    text_confidence: str
    timing_confidence: str
    strategy: str
    current: bool
    rejected: bool
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TrackFactorReport:
    """Non-destructive recording interpretation used to build provider queries."""

    title: str | None
    artists: tuple[str, ...]
    album: str | None
    duration_ms: int | None
    confidence: str
    strategy: str
    field_provenance: tuple[tuple[str, str], ...]
    evidence: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ActualOutcome:
    """Observable result of one replay."""

    status: str
    record_id: str | None
    timing: str
    origin: str
    confidence: str | None
    source_label: str | None
    network_used: bool
    cache_hit: bool
    track: TrackFactorReport
    evidence: tuple[str, ...]
    diagnostics: tuple[str, ...]
    factors: tuple[CandidateFactorReport, ...]


@dataclass(frozen=True, slots=True)
class CaseReport:
    """Expected-versus-actual evidence for one corpus case."""

    case_id: str
    description: str
    passed: bool
    expected: ExpectedOutcome
    actual: ActualOutcome
    mismatches: tuple[str, ...]
    corpus_path: str


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    """Metrics that keep dangerous acceptance separate from ordinary misses."""

    total_cases: int
    passed_cases: int
    expected_automatic_accepts: int
    correct_automatic_accepts: int
    wrong_automatic_accepts: int
    unnecessary_ambiguities: int
    missed_matches: int
    wrong_timing_trust: int
    fallback_cases: int
    fallback_failures: int
    provider_search_fallback_cases: int
    provider_search_fallback_failures: int


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Complete deterministic corpus result."""

    schema_version: int
    metrics: EvaluationMetrics
    cases: tuple[CaseReport, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class _LoadedCase:
    case_id: str
    description: str
    track: Mapping[str, object]
    provider: Mapping[str, object]
    local: Mapping[str, object] | None
    prior: Mapping[str, object]
    expected: ExpectedOutcome
    corpus_path: str


class _FixtureLocalPathCanonicalizer:
    def canonicalize(self, path: str) -> LocalPathResolution:
        return LocalPathResolution(path, True, False)


class _FixtureProvider:
    name = "evaluation-provider"

    def __init__(
        self,
        exact_result: LyricsProviderResult,
        search_result: LyricsProviderResult,
    ) -> None:
        self._exact_result = exact_result
        self._search_result = search_result

    def exact(self, query: LyricsQuery) -> LyricsProviderResult:
        del query
        return self._exact_result

    def search(self, query: LyricsQuery) -> LyricsProviderResult:
        del query
        return self._search_result

    def parse_cached(self, payload: bytes, *, search: bool) -> LyricsProviderResult:
        del payload
        return self._search_result if search else self._exact_result


class _FixtureLocalSource:
    def __init__(self, result: LocalLyricsResult) -> None:
        self._result = result

    def load(self, track: ResolvedTrack) -> LocalLyricsResult:
        del track
        return self._result


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise EvaluationInputError(f"{context} must be a JSON object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, context: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise EvaluationInputError(f"{context} must be a JSON array")
    return cast(Sequence[object], value)


def _string(value: object, context: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise EvaluationInputError(f"{context} must be a non-blank string")
    return value.strip()


def _integer(value: object, context: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise EvaluationInputError(f"{context} must be an integer")
    return value


def _boolean(value: object, context: str, *, default: bool | None = None) -> bool:
    if value is None and default is not None:
        return default
    if not isinstance(value, bool):
        raise EvaluationInputError(f"{context} must be a boolean")
    return value


def _strings(value: object, context: str) -> tuple[str, ...]:
    values = _sequence(value, context)
    result: list[str] = []
    for index, item in enumerate(values):
        parsed = _string(item, f"{context}[{index}]")
        assert parsed is not None
        result.append(parsed)
    return tuple(result)


def _optional_mapping(value: object, context: str) -> Mapping[str, object] | None:
    return None if value is None else _mapping(value, context)


def _expected(value: object, context: str) -> ExpectedOutcome:
    raw = _mapping(value, context)
    category = _string(raw.get("category"), f"{context}.category")
    assert category is not None
    if category not in _OUTCOME_CATEGORIES:
        raise EvaluationInputError(
            f"{context}.category must be one of {sorted(_OUTCOME_CATEGORIES)}"
        )
    status = _string(raw.get("status"), f"{context}.status")
    timing = _string(raw.get("timing"), f"{context}.timing")
    origin = _string(raw.get("origin"), f"{context}.origin")
    assert status is not None and timing is not None and origin is not None
    if timing not in {"synced", "plain", "instrumental", "none"}:
        raise EvaluationInputError(f"{context}.timing is unsupported")
    if origin not in {"provider-auto", "local", "approved", "none"}:
        raise EvaluationInputError(f"{context}.origin is unsupported")
    record_id = _string(raw.get("record_id"), f"{context}.record_id", optional=True)
    return ExpectedOutcome(
        category,
        status,
        record_id,
        timing,
        origin,
        _boolean(raw.get("network_used"), f"{context}.network_used", default=False),
    )


def _load_file(path: Path) -> tuple[_LoadedCase, ...]:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise EvaluationInputError(f"cannot inspect {path}: {error}") from error
    if size > MAX_FIXTURE_BYTES:
        raise EvaluationInputError(
            f"{path} exceeds the {MAX_FIXTURE_BYTES}-byte corpus limit"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvaluationInputError(
            f"cannot read JSON corpus {path}: {error}"
        ) from error
    root = _mapping(payload, str(path))
    version = _integer(root.get("schema_version"), f"{path}.schema_version")
    if version != SCHEMA_VERSION:
        raise EvaluationInputError(
            f"{path} uses schema {version}; evaluator requires {SCHEMA_VERSION}"
        )
    case_values = _sequence(root.get("cases"), f"{path}.cases")
    if len(case_values) > MAX_CASES_PER_FILE:
        raise EvaluationInputError(
            f"{path} exceeds the {MAX_CASES_PER_FILE}-case corpus limit"
        )
    loaded: list[_LoadedCase] = []
    for index, value in enumerate(case_values):
        context = f"{path}.cases[{index}]"
        raw = _mapping(value, context)
        case_id = _string(raw.get("id"), f"{context}.id")
        description = _string(raw.get("description"), f"{context}.description")
        assert case_id is not None and description is not None
        loaded.append(
            _LoadedCase(
                case_id,
                description,
                _mapping(raw.get("track"), f"{context}.track"),
                _mapping(raw.get("provider", {}), f"{context}.provider"),
                _optional_mapping(raw.get("local"), f"{context}.local"),
                _mapping(raw.get("prior", {}), f"{context}.prior"),
                _expected(raw.get("expected"), f"{context}.expected"),
                path.name,
            )
        )
    return tuple(loaded)


def load_cases(paths: Sequence[Path]) -> tuple[_LoadedCase, ...]:
    """Load JSON files or every JSON file directly beneath supplied directories."""

    files: list[Path] = []
    for path in paths:
        try:
            if path.is_dir():
                files.extend(
                    sorted(item for item in path.iterdir() if item.suffix == ".json")
                )
            else:
                files.append(path)
        except OSError as error:
            raise EvaluationInputError(f"cannot inspect {path}: {error}") from error
    if not files:
        raise EvaluationInputError("no JSON corpus files were supplied")
    loaded = tuple(case for path in files for case in _load_file(path))
    identifiers: dict[str, str] = {}
    for case in loaded:
        previous = identifiers.get(case.case_id)
        if previous is not None:
            raise EvaluationInputError(
                f"duplicate case id {case.case_id!r} in {previous} and "
                f"{case.corpus_path}"
            )
        identifiers[case.case_id] = case.corpus_path
    return loaded


def _candidate(value: object, context: str) -> LyricsProviderCandidate:
    raw = _mapping(value, context)
    record_id = _string(raw.get("record_id"), f"{context}.record_id")
    track_name = _string(raw.get("track_name"), f"{context}.track_name")
    artist_name = _string(raw.get("artist_name"), f"{context}.artist_name")
    assert record_id is not None and track_name is not None and artist_name is not None
    return LyricsProviderCandidate(
        provider="evaluation-provider",
        record_id=record_id,
        track_name=track_name,
        artist_name=artist_name,
        album_name=_string(
            raw.get("album_name"), f"{context}.album_name", optional=True
        ),
        duration_ms=_integer(
            raw.get("duration_ms"), f"{context}.duration_ms", optional=True
        ),
        instrumental=_boolean(
            raw.get("instrumental"), f"{context}.instrumental", default=False
        ),
        plain_lyrics=_string(
            raw.get("plain_lyrics"), f"{context}.plain_lyrics", optional=True
        ),
        synced_lyrics=_string(
            raw.get("synced_lyrics"), f"{context}.synced_lyrics", optional=True
        ),
    )


def _provider_result(
    value: object, context: str, *, default_candidates: Sequence[object]
) -> LyricsProviderResult:
    raw = _mapping(value, context)
    status_text = _string(raw.get("status", "results"), f"{context}.status")
    assert status_text is not None
    try:
        status = LyricsProviderStatus(status_text)
    except ValueError as error:
        raise EvaluationInputError(f"{context}.status is unsupported") from error
    values = _sequence(
        raw.get("candidates", list(default_candidates)), f"{context}.candidates"
    )
    candidates = tuple(
        _candidate(item, f"{context}.candidates[{index}]")
        for index, item in enumerate(values)
    )
    return LyricsProviderResult(
        status,
        candidates,
        diagnostics=(f"replayed {context.rsplit('.', 1)[-1]} fixture result",),
    )


def _provider(value: Mapping[str, object], context: str) -> _FixtureProvider:
    default_candidates = _sequence(value.get("candidates", []), f"{context}.candidates")
    exact = _provider_result(
        value.get("exact", {}),
        f"{context}.exact",
        default_candidates=default_candidates,
    )
    search = _provider_result(
        value.get("search", {}),
        f"{context}.search",
        default_candidates=default_candidates,
    )
    return _FixtureProvider(exact, search)


def _snapshot(value: Mapping[str, object], context: str) -> PlayerSnapshot:
    service_name = _string(
        value.get("service_name", "evaluation"), f"{context}.service_name"
    )
    title = _string(value.get("title"), f"{context}.title", optional=True)
    artists = _strings(value.get("artists", []), f"{context}.artists")
    duration_ms = _integer(
        value.get("duration_ms"), f"{context}.duration_ms", optional=True
    )
    assert service_name is not None
    return PlayerSnapshot(
        service_name=service_name,
        bus_name=f"org.mpris.MediaPlayer2.{service_name}",
        identity="Evaluation fixture",
        desktop_entry=service_name,
        playback_status="Playing",
        metadata=RawTrackMetadata(
            title=title,
            artists=artists,
            album=_string(value.get("album"), f"{context}.album", optional=True),
            url=_string(value.get("url"), f"{context}.url", optional=True),
            duration_us=None if duration_ms is None else duration_ms * 1_000,
            track_id=_string(
                value.get("track_id"), f"{context}.track_id", optional=True
            ),
        ),
        position_us=0,
        capabilities=PlayerCapabilities(),
        rate=1.0,
    )


def _build_document(
    candidate: LyricsProviderCandidate,
    builder: ProviderLyricDocumentBuilder,
) -> LyricDocument:
    document, diagnostics = builder.build(candidate, FIXED_NOW)
    if document is None:
        raise EvaluationInputError(
            f"prior document {candidate.record_id!r} is invalid: "
            + "; ".join(diagnostics)
        )
    return document


def _local_result(
    value: Mapping[str, object] | None,
    context: str,
) -> tuple[LocalLyricsResult, str | None]:
    if value is None:
        return LocalLyricsResult(LocalLyricsStatus.MISS, "evaluation-local"), None
    source_name = _string(
        value.get("source_name", "evaluation-local"), f"{context}.source_name"
    )
    record_id = _string(value.get("record_id"), f"{context}.record_id")
    lyrics = _string(value.get("lyrics"), f"{context}.lyrics")
    assert source_name is not None and record_id is not None and lyrics is not None
    parsed = parse_lyrics_text(lyrics)
    if parsed.status is LyricsTextParseStatus.INVALID:
        raise EvaluationInputError(
            f"{context}.lyrics is invalid: " + "; ".join(parsed.diagnostics)
        )
    document = build_lyric_document(
        parsed,
        source_name=source_name,
        provenance=ContentProvenance.LOCAL,
        retrieved_at=FIXED_NOW,
        provider_record_id=record_id,
    )
    return LocalLyricsResult(LocalLyricsStatus.FOUND, source_name, document), record_id


def _find_candidate(
    provider: Mapping[str, object], record_id: str, context: str
) -> LyricsProviderCandidate:
    pools: list[object] = list(
        _sequence(provider.get("candidates", []), f"{context}.candidates")
    )
    for key in ("exact", "search"):
        nested = _mapping(provider.get(key, {}), f"{context}.{key}")
        pools.extend(
            _sequence(nested.get("candidates", []), f"{context}.{key}.candidates")
        )
    candidates = tuple(
        _candidate(value, f"{context}.candidate[{index}]")
        for index, value in enumerate(pools)
    )
    found = next((item for item in candidates if item.record_id == record_id), None)
    if found is None:
        raise EvaluationInputError(
            f"{context} has no provider candidate with record_id {record_id!r}"
        )
    return found


def _seed_prior_state(
    case: _LoadedCase,
    track: ResolvedTrack,
    builder: ProviderLyricDocumentBuilder,
    storage: StorageRepositories,
) -> ResolvedTrack:
    override = _optional_mapping(
        case.prior.get("track_override"), f"{case.case_id}.prior.track_override"
    )
    if override is not None:
        title = _string(override.get("title"), f"{case.case_id}.override.title")
        artists = _strings(
            override.get("artists", []), f"{case.case_id}.override.artists"
        )
        assert title is not None
        storage.track_overrides.put(
            track.source_identity, ApprovedTrackIdentity(title, artists)
        )
        track = TrackResolver(
            SourceIdentityResolver(_FixtureLocalPathCanonicalizer()),
            storage.track_overrides,
        ).resolve(track.raw_snapshot)

    approved_id = _string(
        case.prior.get("approved_record_id"),
        f"{case.case_id}.prior.approved_record_id",
        optional=True,
    )
    rejected_ids = _strings(
        case.prior.get("rejected_record_ids", []),
        f"{case.case_id}.prior.rejected_record_ids",
    )
    current_rejection = _boolean(
        case.prior.get("current_rejection"),
        f"{case.case_id}.prior.current_rejection",
        default=False,
    )
    if approved_id is not None:
        candidate = _find_candidate(
            case.provider, approved_id, f"{case.case_id}.provider"
        )
        document = _build_document(candidate, builder)
        storage.lyrics.put(document)
        storage.lyrics_matches.approve(
            track.source_identity,
            LyricsMatch(
                document.document_id,
                LyricsMatchDecision.APPROVED,
                ContentProvenance.USER,
                FIXED_NOW,
                LyricsMatchConfidence.APPROVED,
                ("evaluation fixture user approval",),
            ),
        )
    for record_id in rejected_ids:
        candidate = _find_candidate(
            case.provider, record_id, f"{case.case_id}.provider"
        )
        document = _build_document(candidate, builder)
        storage.lyrics.put(document)
        match = LyricsMatch(
            document.document_id,
            LyricsMatchDecision.REJECTED,
            ContentProvenance.USER,
            FIXED_NOW,
            LyricsMatchConfidence.APPROVED,
            ("evaluation fixture user rejection",),
        )
        if current_rejection:
            storage.lyrics_matches.reject(track.source_identity, match)
        else:
            storage.lyrics_matches.put_rejection(track.source_identity, match)
    return track


def _timing(document: LyricDocument | None) -> str:
    if document is None:
        return "none"
    return {
        LyricDocumentKind.SYNCED: "synced",
        LyricDocumentKind.PLAIN: "plain",
        LyricDocumentKind.INSTRUMENTAL: "instrumental",
    }[document.kind]


def _origin(
    document: LyricDocument | None,
    confidence: LyricsMatchConfidence | None,
    local_record_id: str | None,
) -> str:
    if document is None:
        return "none"
    if confidence is LyricsMatchConfidence.APPROVED:
        return "approved"
    if local_record_id is not None and document.provider_record_id == local_record_id:
        return "local"
    return "provider-auto"


def _mismatches(expected: ExpectedOutcome, actual: ActualOutcome) -> tuple[str, ...]:
    values = (
        ("status", expected.status, actual.status),
        ("record_id", expected.record_id, actual.record_id),
        ("timing", expected.timing, actual.timing),
        ("origin", expected.origin, actual.origin),
        ("network_used", expected.network_used, actual.network_used),
    )
    return tuple(
        f"{name}: expected {wanted!r}, observed {observed!r}"
        for name, wanted, observed in values
        if wanted != observed
    )


def _run_case(case: _LoadedCase, database_path: Path) -> CaseReport:
    storage = open_storage(database_path)
    snapshot = _snapshot(case.track, f"{case.case_id}.track")
    track_resolver = TrackResolver(
        SourceIdentityResolver(_FixtureLocalPathCanonicalizer()),
        storage.track_overrides,
    )
    track = track_resolver.resolve(snapshot)
    builder = ProviderLyricDocumentBuilder()
    track = _seed_prior_state(case, track, builder, storage)
    local, local_record_id = _local_result(case.local, f"{case.case_id}.local")
    provider = _provider(case.provider, f"{case.case_id}.provider")
    resolver = LyricsResolver(
        local_sources=(_FixtureLocalSource(local),),
        provider=provider,
        provider_documents=builder,
        lyrics=storage.lyrics,
        matches=storage.lyrics_matches,
        provider_cache=storage.provider_cache,
        now=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
    )
    resolution = resolver.resolve(track)
    alternatives = resolver.alternatives(track)
    factors = tuple(
        CandidateFactorReport(
            alternative.candidate.record_id,
            alternative.document_id,
            alternative.confidence.value,
            alternative.text_confidence.value,
            alternative.timing_confidence.value,
            alternative.strategy,
            alternative.current,
            alternative.rejected,
            alternative.evidence,
        )
        for alternative in alternatives.alternatives
    )
    document = resolution.document
    duration_us = track.candidate.duration_us
    actual = ActualOutcome(
        status=resolution.status.value,
        record_id=None if document is None else document.provider_record_id,
        timing=_timing(document),
        origin=_origin(document, resolution.confidence, local_record_id),
        confidence=(
            None if resolution.confidence is None else resolution.confidence.value
        ),
        source_label=resolution.source_label,
        network_used=resolution.network_used,
        cache_hit=resolution.cache_hit,
        track=TrackFactorReport(
            track.candidate.title,
            track.candidate.artists,
            track.candidate.album,
            None if duration_us is None else duration_us // 1_000,
            track.confidence.value,
            track.candidate.strategy,
            track.candidate.field_provenance,
            track.evidence,
            track.warnings,
        ),
        evidence=resolution.evidence,
        diagnostics=resolution.diagnostics,
        factors=factors,
    )
    mismatches = _mismatches(case.expected, actual)
    return CaseReport(
        case.case_id,
        case.description,
        not mismatches,
        case.expected,
        actual,
        mismatches,
        case.corpus_path,
    )


def _metrics(cases: Sequence[CaseReport]) -> EvaluationMetrics:
    correct_auto = 0
    wrong_auto = 0
    expected_auto = 0
    unnecessary_ambiguities = 0
    missed_matches = 0
    wrong_timing = 0
    fallback_cases = 0
    fallback_failures = 0
    provider_search_fallback_cases = 0
    provider_search_fallback_failures = 0
    for case in cases:
        expected_accept = case.expected.status in _ACCEPTED_STATUSES
        actual_accept = case.actual.status in _ACCEPTED_STATUSES
        expects_auto = expected_accept and case.expected.origin == "provider-auto"
        actual_auto = actual_accept and case.actual.origin == "provider-auto"
        if expects_auto:
            expected_auto += 1
            if (
                actual_auto
                and case.actual.record_id == case.expected.record_id
                and case.actual.timing == case.expected.timing
            ):
                correct_auto += 1
        if actual_auto and (
            not expects_auto or case.actual.record_id != case.expected.record_id
        ):
            wrong_auto += 1
        if expected_accept and case.actual.status == "Ambiguous":
            unnecessary_ambiguities += 1
        elif expected_accept and not actual_accept:
            missed_matches += 1
        if case.actual.timing == "synced" and case.expected.timing != "synced":
            wrong_timing += 1
        if case.expected.category in _FALLBACK_CATEGORIES:
            fallback_cases += 1
            if not case.passed:
                fallback_failures += 1
        if case.expected.category == "provider-search-fallback":
            provider_search_fallback_cases += 1
            if not case.passed:
                provider_search_fallback_failures += 1
    return EvaluationMetrics(
        total_cases=len(cases),
        passed_cases=sum(case.passed for case in cases),
        expected_automatic_accepts=expected_auto,
        correct_automatic_accepts=correct_auto,
        wrong_automatic_accepts=wrong_auto,
        unnecessary_ambiguities=unnecessary_ambiguities,
        missed_matches=missed_matches,
        wrong_timing_trust=wrong_timing,
        fallback_cases=fallback_cases,
        fallback_failures=fallback_failures,
        provider_search_fallback_cases=provider_search_fallback_cases,
        provider_search_fallback_failures=provider_search_fallback_failures,
    )


def evaluate(paths: Sequence[Path]) -> EvaluationReport:
    """Replay every supplied case in isolated, temporary real SQLite storage."""

    loaded = load_cases(paths)
    with tempfile.TemporaryDirectory(prefix="konokashi-matching-evaluation-") as root:
        reports = tuple(
            _run_case(case, Path(root) / f"case-{index}.sqlite3")
            for index, case in enumerate(loaded)
        )
    return EvaluationReport(SCHEMA_VERSION, _metrics(reports), reports)


def _human(report: EvaluationReport) -> str:
    metrics = report.metrics
    lines = [
        "KonoKashi matching evaluation",
        f"cases: {metrics.passed_cases}/{metrics.total_cases} passed",
        (
            "automatic accepts: "
            f"{metrics.correct_automatic_accepts}/"
            f"{metrics.expected_automatic_accepts} correct"
        ),
        f"wrong automatic accepts: {metrics.wrong_automatic_accepts}",
        f"unnecessary ambiguities: {metrics.unnecessary_ambiguities}",
        f"missed matches: {metrics.missed_matches}",
        f"wrong timing trust: {metrics.wrong_timing_trust}",
        (
            "recovery/fallback failures: "
            f"{metrics.fallback_failures}/{metrics.fallback_cases}"
        ),
        (
            "provider search fallback failures: "
            f"{metrics.provider_search_fallback_failures}/"
            f"{metrics.provider_search_fallback_cases}"
        ),
    ]
    for case in report.cases:
        lines.append(
            f"\n[{'PASS' if case.passed else 'FAIL'}] {case.case_id}: "
            f"{case.description}"
        )
        lines.append(
            "  observed: "
            f"{case.actual.status}, record={case.actual.record_id or '-'}, "
            f"timing={case.actual.timing}, origin={case.actual.origin}"
        )
        lines.append(
            "  track: "
            f"{case.actual.track.title or '-'} / "
            f"{', '.join(case.actual.track.artists) or '-'} "
            f"confidence={case.actual.track.confidence} "
            f"strategy={case.actual.track.strategy}"
        )
        lines.extend(
            f"    - {name}: {source}"
            for name, source in case.actual.track.field_provenance
        )
        for mismatch in case.mismatches:
            lines.append(f"  mismatch: {mismatch}")
        for factor in case.actual.factors:
            lines.append(
                "  candidate: "
                f"{factor.record_id} confidence={factor.confidence} "
                f"text={factor.text_confidence} timing={factor.timing_confidence} "
                f"strategy={factor.strategy} rejected={factor.rejected}"
            )
            lines.extend(f"    - {item}" for item in factor.evidence)
        lines.extend(f"  resolver: {item}" for item in case.actual.diagnostics)
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replay network-free matching corpora through KonoKashi's real "
            "resolver and matcher."
        )
    )
    parser.add_argument(
        "paths",
        type=Path,
        nargs="+",
        help="JSON corpus files or directories containing JSON corpus files",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the complete structured report as JSON",
    )
    parser.add_argument(
        "--allow-mismatches",
        action="store_true",
        help="exit successfully even when observed behavior differs from the oracle",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = evaluate(arguments.paths)
    except EvaluationInputError as error:
        print(f"Invalid matching-evaluation corpus: {error}", file=sys.stderr)
        return 2
    if arguments.json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_human(report))
    all_passed = report.metrics.passed_cases == report.metrics.total_cases
    return 0 if arguments.allow_mismatches or all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
