"""Frontend-neutral values for configured music-library scanning."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from konokashi.domain.tracks import Confidence, TrackCandidate


class LibraryMetadataSource(Enum):
    """How a scanner derived recording metadata without editing the file."""

    TAGS = "tags"
    FILENAME = "filename"
    TAGS_AND_FILENAME = "tags-and-filename"
    UNREADABLE = "unreadable"


class LibraryTrackState(Enum):
    """Durable state of one discovered filesystem recording."""

    READY = "ready"
    REVIEW = "review"
    MISSING = "missing"


class LibraryScanStatus(Enum):
    """Truthful terminal state for one scan attempt."""

    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed-with-errors"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LibraryScanIssueCategory(Enum):
    """Privacy-safe failure categories shared by local UI and diagnostics."""

    ROOT_UNAVAILABLE = "root-unavailable"
    DIRECTORY_READ = "directory-read"
    FILE_INSPECTION = "file-inspection"
    METADATA_READ = "metadata-read"
    DOWNLOAD = "download"
    STORAGE = "storage"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LibraryScanIssue:
    """Bounded local-only failure detail; paths never enter support exports."""

    category: LibraryScanIssueCategory
    path: str | None
    detail: str


class LibraryScanFailure(RuntimeError):
    """Typed adapter failure that prevents complete filesystem discovery."""

    def __init__(self, issue: LibraryScanIssue) -> None:
        super().__init__(issue.detail)
        self.issue = issue


@dataclass(frozen=True, slots=True)
class LibrarySettings:
    """Canonical global Stage 9 scanner settings shared by every frontend."""

    roots: tuple[str, ...] = field(default_factory=tuple)
    automatic_downloads: bool = False
    worker_count: int = 4

    def __post_init__(self) -> None:
        if not 1 <= self.worker_count <= 8:
            raise ValueError("library worker count must be between 1 and 8")
        paths = tuple(Path(root) for root in self.roots)
        if len(set(paths)) != len(paths):
            raise ValueError("library roots must not contain duplicates")
        if any(
            not root or not path.is_absolute()
            for root, path in zip(self.roots, paths, strict=True)
        ):
            raise ValueError("library roots must be non-empty absolute paths")
        if any(
            first in second.parents or second in first.parents
            for index, first in enumerate(paths)
            for second in paths[index + 1 :]
        ):
            raise ValueError("library roots must not overlap")


@dataclass(frozen=True, slots=True)
class LibraryFile:
    """One audio file discovered without reading its audio payload."""

    file_key: str
    path: str
    root: str
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class LibraryMetadata:
    """Read-only metadata result, including its confidence and provenance."""

    candidate: TrackCandidate
    confidence: Confidence
    source: LibraryMetadataSource
    diagnostic: str | None = None
    issue: LibraryScanIssue | None = None


@dataclass(frozen=True, slots=True)
class StoredLibraryTrack:
    """Persistable index entry for one discovered recording."""

    file: LibraryFile
    metadata: LibraryMetadata
    state: LibraryTrackState
    lyrics_status: str = "not-requested"
    review_reason: str | None = None


@dataclass(frozen=True, slots=True)
class LibraryReviewItem:
    """A bounded review-queue row without exposing it to provider policy."""

    path: str
    title: str | None
    artists: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class LibraryScanSummary:
    """Terminal counters for one resumable scan."""

    scan_id: int
    discovered: int
    processed: int
    unchanged: int
    moved: int
    missing: int
    review: int
    downloaded: int
    download_misses: int
    errors: int
    cancelled: bool = False
    issues: tuple[LibraryScanIssue, ...] = field(default_factory=tuple)
    error_categories: tuple[tuple[LibraryScanIssueCategory, int], ...] = field(
        default_factory=tuple
    )

    @property
    def status(self) -> LibraryScanStatus:
        if self.cancelled:
            return LibraryScanStatus.CANCELLED
        if not self.errors:
            return LibraryScanStatus.COMPLETED
        if self.processed or self.unchanged:
            return LibraryScanStatus.COMPLETED_WITH_ERRORS
        return LibraryScanStatus.FAILED
