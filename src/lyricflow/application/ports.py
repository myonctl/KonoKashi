"""Application ports and local-diagnostic value types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from lyricflow.domain.identity import LocalFileIdentity, SourceIdentity
from lyricflow.domain.models import (
    PlayerEvent,
    PlayerInspection,
    PlayerListResult,
    PlayerWatchStart,
    RawTrackMetadata,
)
from lyricflow.domain.tracks import ApprovedTrackIdentity, TrackCandidate


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
