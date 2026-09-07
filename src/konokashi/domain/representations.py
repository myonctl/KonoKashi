"""Durable line-aligned multilingual representation values and precedence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from konokashi.domain.lyrics import (
    ApprovalState,
    ContentProvenance,
    LyricLine,
    RepresentationKind,
)
from konokashi.domain.scripts import ScriptAnalysis


class RomanizationRoute(Enum):
    """Conservative adapter routes; these are not inferred language claims."""

    JAPANESE = "japanese"
    KOREAN = "korean"
    CHINESE = "chinese"
    CYRILLIC = "cyrillic"
    GREEK = "greek"
    ARABIC = "arabic"
    THAI = "thai"


class GenerationStatus(Enum):
    """Outcome for one independently generated original line."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class RepresentationUncertainty(Enum):
    """Qualitative uncertainty; adapters must not invent numeric confidence."""

    NONE = "none"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class LanguageRoutingEvidence:
    """Provider-neutral document-level language evidence for script routing."""

    language: str | None
    uncertainty: RepresentationUncertainty
    diagnostic: str


@dataclass(frozen=True, slots=True)
class DocumentLanguageOverride:
    """One user-approved language hint scoped to a stable lyric document."""

    document_id: str
    language: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RomanizationRequest:
    """One stable-line request passed through the replaceable provider port."""

    original_line_id: str
    text: str
    route: RomanizationRoute
    language_hint: str | None = None


@dataclass(frozen=True, slots=True)
class RomanizationProviderResult:
    """Provider-neutral generated line or controlled unavailable/failure state."""

    status: GenerationStatus
    provider_name: str
    provider_version: str
    kind: RepresentationKind
    text: str | None = None
    language: str | None = None
    script: str = "Latn"
    uncertainty: RepresentationUncertainty = RepresentationUncertainty.AMBIGUOUS
    diagnostics: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RepresentationCandidate:
    """One retained provider/imported/generated value for one original line."""

    candidate_id: str
    document_id: str
    source_line_id: str
    kind: RepresentationKind
    status: GenerationStatus
    provenance: ContentProvenance
    source_name: str
    source_version: str | None
    approval_state: ApprovalState
    uncertainty: RepresentationUncertainty
    created_at: datetime
    updated_at: datetime
    text: str | None = None
    language: str | None = None
    script: str | None = "Latn"
    diagnostics: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RepresentationDecision:
    """Independent user draft/approval/rejection for one line and layer."""

    document_id: str
    source_line_id: str
    kind: RepresentationKind
    approval_state: ApprovalState
    created_at: datetime
    updated_at: datetime
    text: str | None = None
    based_on_candidate_id: str | None = None


@dataclass(frozen=True, slots=True)
class RepresentationDisplaySettings:
    """Global Stage 5 layer toggles; originals stay visible by default."""

    show_original: bool = True
    show_romanized: bool = True
    show_translated: bool = False


@dataclass(frozen=True, slots=True)
class EffectiveRepresentationLine:
    """The winning visible value for one original line and representation kind."""

    original_line: LyricLine
    kind: RepresentationKind
    text: str | None
    provenance: ContentProvenance | None
    approval_state: ApprovalState | None
    source_name: str | None
    source_version: str | None
    uncertainty: RepresentationUncertainty | None
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    inherited_start_ms: int | None = None
    candidate_id: str | None = None


@dataclass(frozen=True, slots=True)
class ScriptRoutingDecision:
    """Explain an adapter route or why generation is deliberately unavailable."""

    analysis: ScriptAnalysis
    route: RomanizationRoute | None
    kind: RepresentationKind | None
    language: str | None
    uncertainty: RepresentationUncertainty
    diagnostic: str


@dataclass(frozen=True, slots=True)
class RepresentationGenerationReport:
    """Bounded aggregate result for a per-line document generation pass."""

    document_id: str
    original_lines: int
    generated: int
    reused: int
    unavailable: int
    failed: int
    diagnostics: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ImportedRepresentationLine:
    """Provider/import input that explicitly names its original line."""

    source_line_id: str
    text: str


@dataclass(frozen=True, slots=True)
class RepresentationImportResult:
    """Import result that can reject unsafe alignment without losing evidence."""

    imported: int
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
