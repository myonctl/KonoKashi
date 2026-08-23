"""Application ports and local-diagnostic value types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from lyricflow.application.settings import DesktopInteractionSettings
from lyricflow.domain.identity import LocalFileIdentity, SourceIdentity
from lyricflow.domain.lyrics import (
    LocalLyricsResult,
    LyricDocument,
    LyricsMatch,
    LyricsProviderCandidate,
    LyricsProviderResult,
    LyricsQuery,
    ProviderCacheEntry,
    RepresentationKind,
)
from lyricflow.domain.models import (
    PlayerEvent,
    PlayerInspection,
    PlayerListResult,
    PlayerSnapshot,
    PlayerWatchStart,
    RawTrackMetadata,
)
from lyricflow.domain.representations import (
    DocumentLanguageOverride,
    LanguageRoutingEvidence,
    RepresentationCandidate,
    RepresentationDecision,
    RepresentationDisplaySettings,
    RomanizationProviderResult,
    RomanizationRequest,
)
from lyricflow.domain.synchronization import (
    AudioLatencyProbeResult,
    LyricDocumentTiming,
    ObservationReason,
    OutputDeviceCalibration,
    PositionObservation,
)
from lyricflow.domain.tracks import (
    ApprovedTrackIdentity,
    PlayerSelectionConfig,
    ResolvedTrack,
    TrackCandidate,
)


class DiagnosticStatus(Enum):
    """Machine-readable outcome for one diagnostic check."""

    OK = "OK"
    WARNING = "WARN"
    FAILURE = "FAIL"


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    """One local prerequisite result produced by a diagnostic adapter."""

    name: str
    status: DiagnosticStatus
    message: str
    required: bool = True


class PlayerDiscoveryPort(Protocol):
    """Enumerate and inspect MPRIS players without exposing D-Bus values."""

    def list_players(self) -> PlayerListResult:
        """Inspect every currently registered MPRIS service."""

    def inspect_player(self, service_name: str) -> PlayerInspection:
        """Inspect one short or fully qualified MPRIS service name."""


class PlayerEventHandler(Protocol):
    """Callable event consumer used by watch-mode presentation."""

    def __call__(self, event: PlayerEvent) -> None:
        """Consume one provider-neutral MPRIS event."""


class PlayerMonitorPort(Protocol):
    """Attach to provider-neutral player lifecycle and property events."""

    def start(self, handler: PlayerEventHandler) -> PlayerWatchStart:
        """Attach watchers and return the initial service set."""

    def close(self) -> None:
        """Disconnect all signal subscriptions."""


class PlayerPositionSamplerPort(Protocol):
    """Capture one Position read with local monotonic timing evidence."""

    def sample(
        self,
        snapshot: PlayerSnapshot,
        session_id: str,
        *,
        reason: ObservationReason = ObservationReason.PERIODIC,
    ) -> PositionObservation:
        """Return a bracketed observation or raise a controlled runtime error."""


class AudioLatencyProbePort(Protocol):
    """Read graph latency evidence without capturing or modifying audio."""

    def probe(self) -> AudioLatencyProbeResult:
        """Return a bounded diagnostic result or controlled unavailable state."""


class ClockPort(Protocol):
    """High-resolution local clocks used without wall-clock interpolation."""

    def monotonic_ns(self) -> int:
        """Return an integer monotonic timestamp."""

    def boottime_ns(self) -> int | None:
        """Return suspend-aware Linux boottime, or None when unavailable."""


class TimingCalibrationRepositoryPort(Protocol):
    """Persist document delay and per-device residuals as separate values."""

    def get_document_timing(self, document_id: str) -> LyricDocumentTiming:
        """Return the document delay or the explicit zero default."""

    def put_document_timing(self, timing: LyricDocumentTiming) -> None:
        """Persist one whole-document display delay without editing lyrics."""

    def delete_document_timing(self, document_id: str) -> bool:
        """Reset only the selected document delay."""

    def get_output_calibration(self, device_key: str) -> OutputDeviceCalibration | None:
        """Return one device residual; never fall back to another device."""

    def put_output_calibration(self, calibration: OutputDeviceCalibration) -> None:
        """Persist a residual only for a stable output-device identity."""

    def delete_output_calibration(self, device_key: str) -> bool:
        """Reset only the selected output device's residual."""


class MprisRuntimePort(Protocol):
    """CLI runtime containing ports plus event-loop lifecycle control."""

    @property
    def client(self) -> PlayerDiscoveryPort:
        """Player inspection port."""

    @property
    def monitor(self) -> PlayerMonitorPort:
        """Player event-monitoring port."""

    @property
    def timing(self) -> PlayerPositionSamplerPort:
        """Precision position-sampling port."""

    @property
    def clock(self) -> ClockPort:
        """Shared integer clock for sampling, deadlines, and suspend detection."""

    def exec(self) -> int:
        """Run the event loop until stopped."""

    def quit(self) -> None:
        """Request clean event-loop shutdown."""

    def wait(self, timeout_ms: int) -> None:
        """Process adapter events for a bounded interval between samples."""

    def wake(self) -> None:
        """Interrupt a bounded wait after a meaningful player event."""


@dataclass(frozen=True, slots=True)
class LocalPathResolution:
    """Filesystem adapter result for a decoded absolute local path."""

    canonical_path: str
    exists: bool | None
    symlink_resolved: bool
    warning: str | None = None


class LocalPathCanonicalizerPort(Protocol):
    """Resolve local path spelling and symlinks behind a filesystem boundary."""

    def canonicalize(self, path: str) -> LocalPathResolution:
        """Return a stable best-effort path without reading audio contents."""


class TrackMetadataReaderPort(Protocol):
    """Optional future local-tag enrichment behind a Stage 2-ready seam."""

    def read(
        self,
        source_identity: LocalFileIdentity,
        reported_metadata: RawTrackMetadata,
    ) -> TrackCandidate | None:
        """Return safe enrichment or a normal miss; Stage 2 has no adapter."""


class TrackOverrideRepositoryPort(Protocol):
    """Read and write user-approved identity corrections."""

    def get(self, source_identity: SourceIdentity) -> ApprovedTrackIdentity | None:
        """Return the approved correction for an identity, if any."""

    def put(
        self,
        source_identity: SourceIdentity,
        approved_identity: ApprovedTrackIdentity,
    ) -> None:
        """Remember an approved correction."""

    def delete(self, source_identity: SourceIdentity) -> bool:
        """Explicitly remove a correction; return whether one existed."""


class SourceIdentityRepositoryPort(Protocol):
    """Persist and reconstruct structured source identities."""

    def put(self, source_identity: SourceIdentity) -> SourceIdentity:
        """Store an identity idempotently and return its canonical value."""

    def get(self, source_identity: SourceIdentity) -> SourceIdentity | None:
        """Return the same persisted identity, if present."""


class SettingsRepositoryPort(Protocol):
    """Read and atomically replace the accepted durable player settings."""

    def get_player_selection(self) -> PlayerSelectionConfig:
        """Return persisted values or the documented default when absent."""

    def put_player_selection(self, config: PlayerSelectionConfig) -> None:
        """Atomically replace preferred and ignored player settings."""

    def get_representation_display(self) -> RepresentationDisplaySettings:
        """Return durable layer toggles or the product defaults."""

    def put_representation_display(
        self, settings: RepresentationDisplaySettings
    ) -> None:
        """Atomically persist independent original/romanized/translated toggles."""

    def get_desktop_interaction(self) -> DesktopInteractionSettings:
        """Return durable desktop mechanics or their passive defaults."""

    def put_desktop_interaction(self, settings: DesktopInteractionSettings) -> None:
        """Persist opt-in desktop interaction mechanics."""


class LyricsRepositoryPort(Protocol):
    """Store provider-neutral lyric documents without resolving providers."""

    def get(self, document_id: str) -> LyricDocument | None:
        """Return one complete document, if present."""

    def put(self, document: LyricDocument) -> None:
        """Atomically insert or deliberately replace one document."""

    def delete(self, document_id: str) -> bool:
        """Explicitly remove one document and its owned representation data."""


class LyricsMatchRepositoryPort(Protocol):
    """Persist recording-to-document decisions independently of cache data."""

    def get(self, source_identity: SourceIdentity) -> LyricsMatch | None:
        """Return the current decision for a durable source identity."""

    def put(self, source_identity: SourceIdentity, match: LyricsMatch) -> None:
        """Atomically save or replace one match decision."""

    def delete(self, source_identity: SourceIdentity) -> bool:
        """Explicitly reset one match decision."""

    def rejections(self, source_identity: SourceIdentity) -> tuple[LyricsMatch, ...]:
        """Return durable document rejections for one recording."""

    def put_rejection(
        self, source_identity: SourceIdentity, match: LyricsMatch
    ) -> None:
        """Persist one rejected document without replacing the current decision."""

    def delete_rejection(
        self, source_identity: SourceIdentity, document_id: str
    ) -> bool:
        """Explicitly reverse one durable document rejection."""

    def clear_rejections(self, source_identity: SourceIdentity) -> int:
        """Reset every rejected-document preference for one recording."""


class ProviderCacheRepositoryPort(Protocol):
    """Persist provider responses without granting them approval semantics."""

    def get(self, provider: str, cache_key: str) -> ProviderCacheEntry | None:
        """Return one cached provider response, if present."""

    def put(self, entry: ProviderCacheEntry) -> None:
        """Atomically insert or replace one cache entry."""

    def delete(self, provider: str, cache_key: str) -> bool:
        """Remove only the selected optional cache entry."""


class LyricsProviderPort(Protocol):
    """Read-only provider adapter returning provider-neutral candidates."""

    @property
    def name(self) -> str:
        """Stable provider name used for provenance and cache keys."""

    def exact(self, query: LyricsQuery) -> LyricsProviderResult:
        """Attempt one exact metadata lookup or explain why it was skipped."""

    def search(self, query: LyricsQuery) -> LyricsProviderResult:
        """Search for bounded candidates without auto-approving any result."""

    def parse_cached(self, payload: bytes, *, search: bool) -> LyricsProviderResult:
        """Reconstruct a typed provider result from an adapter-owned raw payload."""


class LocalLyricsProviderPort(Protocol):
    """Inspect one exact recording for a bounded read-only local lyrics source."""

    def load(self, track: ResolvedTrack) -> LocalLyricsResult:
        """Return found, miss, or invalid without changing local files or tags."""


class LyricsCandidateDocumentPort(Protocol):
    """Turn one provider-neutral candidate into a canonical original document."""

    def document_id(self, candidate: LyricsProviderCandidate) -> str:
        """Return the deterministic canonical document ID for deduplication."""

    def build(
        self, candidate: LyricsProviderCandidate, retrieved_at: datetime
    ) -> tuple[LyricDocument | None, tuple[str, ...]]:
        """Build timed/plain/instrumental content or controlled diagnostics."""


class RomanizationProviderPort(Protocol):
    """Generate one line through replaceable offline language/script adapters."""

    @property
    def name(self) -> str:
        """Stable composite provider name for diagnostics."""

    def generate(self, request: RomanizationRequest) -> RomanizationProviderResult:
        """Generate or return a controlled unavailable/failure result."""


class LanguageEvidenceProviderPort(Protocol):
    """Classify bounded document text without changing or retaining it."""

    def classify_han(self, text: str) -> LanguageRoutingEvidence:
        """Return conservative Chinese evidence or an explained ambiguity."""


class RepresentationRepositoryPort(Protocol):
    """Persist alternate candidates and user decisions independently of originals."""

    def candidates(self, document_id: str) -> tuple[RepresentationCandidate, ...]:
        """Return retained candidates in deterministic order."""

    def decisions(self, document_id: str) -> tuple[RepresentationDecision, ...]:
        """Return line-level user decisions in deterministic order."""

    def language_override(self, document_id: str) -> DocumentLanguageOverride | None:
        """Return one user-approved document language hint, if present."""

    def put_language_override(self, override: DocumentLanguageOverride) -> None:
        """Persist one exact-document user-approved language hint."""

    def delete_language_override(self, document_id: str) -> bool:
        """Reset only one exact-document language hint."""

    def delete_generated(self, document_id: str) -> int:
        """Invalidate generated fallback without touching imported/user evidence."""

    def put_candidates(self, candidates: tuple[RepresentationCandidate, ...]) -> None:
        """Atomically insert or update explicitly aligned candidates."""

    def replace_generated(
        self,
        document_id: str,
        source_line_id: str,
        kind: RepresentationKind,
        candidate: RepresentationCandidate,
    ) -> None:
        """Atomically replace generated evidence for one exact original line."""

    def put_decision(self, decision: RepresentationDecision) -> None:
        """Atomically save one line-level draft, approval, or rejection."""

    def delete_decision(
        self, document_id: str, source_line_id: str, kind: RepresentationKind
    ) -> bool:
        """Reset only one selected line/kind decision."""
