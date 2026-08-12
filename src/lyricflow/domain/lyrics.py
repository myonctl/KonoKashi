"""Provider-neutral lyric persistence values for the Stage 3 foundation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


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


class LyricsMatchDecision(Enum):
    """A durable relationship between a recording and a lyric document."""

    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class LyricsMatch:
    """A typed persisted match decision with retained provenance."""

    document_id: str
    decision: LyricsMatchDecision
    provenance: ContentProvenance
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderCacheEntry:
    """An opaque provider response cache entry, separate from user approval."""

    provider: str
    cache_key: str
    payload: bytes
    retrieved_at: datetime
    expires_at: datetime | None = None
