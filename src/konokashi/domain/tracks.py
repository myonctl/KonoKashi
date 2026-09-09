"""Resolved-track values that retain their original MPRIS evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from konokashi.domain.identity import SourceIdentity
from konokashi.domain.models import PlayerSnapshot


class Confidence(Enum):
    """Product-level confidence classes for track interpretation."""

    APPROVED = "Approved"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


@dataclass(frozen=True, slots=True)
class ApprovedTrackIdentity:
    """A user's explicit correction for one stable source identity."""

    title: str
    artists: tuple[str, ...]
    album: str | None = None


@dataclass(frozen=True, slots=True)
class ArtistCredit:
    """Ordered musical artists and explicitly featured contributors."""

    main_artists: tuple[str, ...] = field(default_factory=tuple)
    contributors: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class TrackCandidate:
    """One non-destructive interpretation of raw track metadata."""

    title: str | None
    artists: tuple[str, ...]
    album: str | None
    duration_us: int | None
    evidence: tuple[str, ...] = field(default_factory=tuple)
    transformations: tuple[str, ...] = field(default_factory=tuple)
    strategy: str = "reported-mpris"
    artist_credit: ArtistCredit | None = None
    field_provenance: tuple[tuple[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ResolvedTrack:
    """Chosen interpretation plus raw snapshot, provenance, and uncertainty."""

    raw_snapshot: PlayerSnapshot
    source_identity: SourceIdentity
    candidate: TrackCandidate
    confidence: Confidence
    evidence: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    user_approved: bool = False
    automatic_candidate: TrackCandidate | None = None
    automatic_confidence: Confidence | None = None
    interpretation_candidates: tuple[TrackCandidate, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PlayerAssessment:
    """Explainable selection evidence for one usable player."""

    track: ResolvedTrack
    reasons: tuple[str, ...]
    rank: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SuppressedPlayer:
    """A duplicate player retained for troubleshooting."""

    assessment: PlayerAssessment
    winner_service_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class PlayerSelectionResult:
    """Primary player plus every independent and suppressed observation."""

    selected: PlayerAssessment | None = None
    alternatives: tuple[PlayerAssessment, ...] = field(default_factory=tuple)
    suppressed: tuple[SuppressedPlayer, ...] = field(default_factory=tuple)
    unavailable_diagnostics: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PlayerSelectionConfig:
    """Explicit user selection preferences."""

    preferred_players: tuple[str, ...] = field(default_factory=tuple)
    ignored_players: tuple[str, ...] = field(default_factory=tuple)
