"""Application ports and local-diagnostic value types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from lyricflow.domain.identity import LocalFileIdentity, SourceIdentity
from lyricflow.domain.lyrics import (
    LocalLyricsResult,
    LyricDocument,
    LyricsMatch,
    LyricsProviderCandidate,
    LyricsProviderResult,
    LyricsQuery,
    ProviderCacheEntry,
)
from lyricflow.domain.models import (
    PlayerEvent,
    PlayerInspection,
    PlayerListResult,
    PlayerWatchStart,
    RawTrackMetadata,
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


class MprisRuntimePort(Protocol):
    """CLI runtime containing ports plus event-loop lifecycle control."""

    @property
    def client(self) -> PlayerDiscoveryPort:
        """Player inspection port."""

    @property
    def monitor(self) -> PlayerMonitorPort:
        """Player event-monitoring port."""

    def exec(self) -> int:
        """Run the event loop until stopped."""

    def quit(self) -> None:
        """Request clean event-loop shutdown."""


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
