"""Provider-neutral lyric documents, provider candidates, and resolution states."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from konokashi.domain.identity import SourceIdentity


class ApprovalState(Enum):
    """Whether persisted content has been reviewed by the user."""

    UNREVIEWED = "unreviewed"
    APPROVED = "approved"
    REJECTED = "rejected"


class LyricDocumentKind(Enum):
    """The timing/content form of one lyric document."""

    PLAIN = "plain"
    SYNCED = "synced"
    INSTRUMENTAL = "instrumental"


class RepresentationKind(Enum):
    """A distinct textual layer within a lyric document."""

    ORIGINAL = "original"
    ROMANIZED = "romanized"
    TRANSLITERATED = "transliterated"
    TRANSLATED = "translated"


class ContentProvenance(Enum):
    """Origin of lyric text without flattening generated and approved data."""

    PROVIDER = "provider"
    LOCAL = "local"
    IMPORTED = "imported"
    GENERATED = "generated"
    USER = "user"


class TimingProvenance(Enum):
    """Origin of a line's timing information."""

    PROVIDER = "provider"
    GENERATED = "generated"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class LyricLine:
    """One stable line instance; repeated text remains independently identified."""

    line_id: str
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    timing_provenance: TimingProvenance | None = None
    source_line_id: str | None = None


@dataclass(frozen=True, slots=True)
class LyricRepresentation:
    """One aligned original, romanized, transliterated, or translated layer."""

    representation_id: str
    kind: RepresentationKind
    provenance: ContentProvenance
    approval_state: ApprovalState
    lines: tuple[LyricLine, ...] = field(default_factory=tuple)
    language: str | None = None
    script: str | None = None
    generator_name: str | None = None
    generator_version: str | None = None


@dataclass(frozen=True, slots=True)
class LyricLineAlignment:
    """Explicit alignment between distinct representation line instances."""

    source_line_id: str
    target_line_id: str


@dataclass(frozen=True, slots=True)
class LyricDocument:
    """A complete provider-neutral lyric document suitable for local storage."""

    document_id: str
    kind: LyricDocumentKind
    source_name: str
    original_text: str | None
    raw_text_checksum: str | None
    approval_state: ApprovalState
    retrieved_at: datetime
    representations: tuple[LyricRepresentation, ...] = field(default_factory=tuple)
    alignments: tuple[LyricLineAlignment, ...] = field(default_factory=tuple)
    provider_record_id: str | None = None
    language: str | None = None
    script: str | None = None
    duration_ms: int | None = None
    source_title: str | None = None
    source_artist: str | None = None
    source_album: str | None = None


class LyricsMatchDecision(Enum):
    """A durable relationship between a recording and a lyric document."""

    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"


class LyricsMatchConfidence(Enum):
    """Confidence that lyric content belongs to one exact recording."""

    APPROVED = "Approved"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


@dataclass(frozen=True, slots=True)
class LyricsMatch:
    """A typed persisted match decision with retained provenance."""

    document_id: str
    decision: LyricsMatchDecision
    provenance: ContentProvenance
    updated_at: datetime
    confidence: LyricsMatchConfidence = LyricsMatchConfidence.LOW
    evidence: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ProviderCacheEntry:
    """An opaque provider response cache entry, separate from user approval."""

    provider: str
    cache_key: str
    payload: bytes
    retrieved_at: datetime
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LyricsQuery:
    """Minimum provider-safe metadata for one resolved recording."""

    title: str
    artists: tuple[str, ...]
    album: str | None
    duration_ms: int | None

    @property
    def artist_name(self) -> str:
        """Return the provider-facing artist string without uploader substitution."""

        return " & ".join(self.artists)


@dataclass(frozen=True, slots=True)
class LyricsProviderCandidate:
    """One provider-neutral read-only lyrics record."""

    provider: str
    record_id: str
    track_name: str
    artist_name: str
    album_name: str | None
    duration_ms: int | None
    instrumental: bool
    plain_lyrics: str | None
    synced_lyrics: str | None


class LyricsProviderStatus(Enum):
    """Outcome of one bounded provider request."""

    RESULTS = "results"
    NO_RESULT = "no-result"
    RATE_LIMITED = "rate-limited"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid-response"


@dataclass(frozen=True, slots=True)
class LyricsProviderResult:
    """Typed provider response with bounded diagnostics and cacheable raw bytes."""

    status: LyricsProviderStatus
    candidates: tuple[LyricsProviderCandidate, ...] = field(default_factory=tuple)
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    retry_after_seconds: int | None = None
    raw_payload: bytes | None = None


class LyricsResolutionStatus(Enum):
    """User-visible terminal states for one lyrics resolution attempt."""

    FOUND_TIMED = "Timed"
    FOUND_UNTIMED = "Untimed"
    INSTRUMENTAL = "Instrumental"
    AMBIGUOUS = "Ambiguous"
    NO_RESULT = "No result"
    OFFLINE_MISS = "Offline miss"
    PROVIDER_UNAVAILABLE = "Provider unavailable"
    RATE_LIMITED = "Rate limited"
    INVALID_LOCAL_LYRICS = "Invalid local lyrics"
    INVALID_PROVIDER_RESPONSE = "Invalid provider response"


@dataclass(frozen=True, slots=True)
class LyricsResolutionResult:
    """Explainable resolution state tied to the source that requested it."""

    source_identity: SourceIdentity
    status: LyricsResolutionStatus
    document: LyricDocument | None = None
    source_label: str | None = None
    confidence: LyricsMatchConfidence | None = None
    evidence: tuple[str, ...] = field(default_factory=tuple)
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    alternatives: tuple[LyricsProviderCandidate, ...] = field(default_factory=tuple)
    cache_hit: bool = False
    network_used: bool = False
    retry_after_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class LyricsAlternative:
    """One reviewable provider result with retained matching evidence."""

    document_id: str
    candidate: LyricsProviderCandidate
    confidence: LyricsMatchConfidence
    evidence: tuple[str, ...] = field(default_factory=tuple)
    current: bool = False
    rejected: bool = False


@dataclass(frozen=True, slots=True)
class LyricsAlternativeResult:
    """Bounded alternative search tied to the exact requested source."""

    source_identity: SourceIdentity
    alternatives: tuple[LyricsAlternative, ...] = field(default_factory=tuple)
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    cache_hit: bool = False
    network_used: bool = False


class LyricsTextParseStatus(Enum):
    """Whether lyric text safely produced synced, plain, or invalid content."""

    SYNCED = "synced"
    PLAIN = "plain"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ParsedLyricsText:
    """Bounded parse output before source-specific document construction."""

    status: LyricsTextParseStatus
    lines: tuple[LyricLine, ...] = field(default_factory=tuple)
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    normalized_text: str = ""
    raw_text_checksum: str | None = None


class LocalLyricsStatus(Enum):
    """Outcome of one exact-recording local lyrics source."""

    FOUND = "found"
    MISS = "miss"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class LocalLyricsResult:
    """Read-only local source result with no filesystem details in the core."""

    status: LocalLyricsStatus
    source_label: str
    document: LyricDocument | None = None
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
