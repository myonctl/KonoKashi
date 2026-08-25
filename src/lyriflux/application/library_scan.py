"""Incremental, cancellable music-directory scanning orchestration."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event
from typing import Protocol

from lyriflux.domain.identity import LocalFileIdentity
from lyriflux.domain.library import (
    LibraryFile,
    LibraryMetadata,
    LibraryReviewItem,
    LibraryScanSummary,
    LibrarySettings,
    LibraryTrackState,
    StoredLibraryTrack,
)
from lyriflux.domain.tracks import ApprovedTrackIdentity, Confidence, TrackCandidate


class LibraryFilesystemPort(Protocol):
    """Enumerate supported recordings without mutating their directories."""

    def files(self, roots: tuple[str, ...]) -> Iterable[LibraryFile]:
        """Yield deterministic file observations or raise a controlled error."""


class LibraryMetadataPort(Protocol):
    """Read tags and safe filename fallbacks without saving metadata."""

    def read(self, file: LibraryFile) -> LibraryMetadata:
        """Return one typed interpretation."""


class LibraryRepositoryPort(Protocol):
    """Persist scanner settings, progress, index entries, and review state."""

    def get_settings(self) -> LibrarySettings: ...

    def put_settings(self, settings: LibrarySettings) -> None: ...

    def reconcile_configured_roots(self, roots: tuple[str, ...]) -> int: ...

    def begin_scan(self) -> int: ...

    def matching_file(self, file: LibraryFile) -> StoredLibraryTrack | None: ...

    def put_track(self, track: StoredLibraryTrack) -> None: ...

    def finish_scan(self, scan_id: int, summary: LibraryScanSummary) -> None: ...

    def reconcile_missing(self, roots: tuple[str, ...], seen: set[str]) -> int: ...

    def review_items(self, limit: int = 100) -> tuple[LibraryReviewItem, ...]: ...

    def counts(self) -> tuple[int, int, int]: ...


LibraryDownloader = Callable[[LibraryFile, LibraryMetadata, bool], str]
ProgressCallback = Callable[[int, str], None]


class LibraryOverridePort(Protocol):
    """Read user-approved Stage 8 recording corrections by canonical path."""

    def get(
        self, source_identity: LocalFileIdentity
    ) -> ApprovedTrackIdentity | None: ...


class LibraryScanService:
    """Run bounded extraction jobs while serializing durable scan progress."""

    def __init__(
        self,
        filesystem: LibraryFilesystemPort,
        metadata: LibraryMetadataPort,
        repository: LibraryRepositoryPort,
        *,
        downloader: LibraryDownloader | None = None,
        overrides: LibraryOverridePort | None = None,
        settings: LibrarySettings | None = None,
    ) -> None:
        self._filesystem = filesystem
        self._metadata = metadata
        self._repository = repository
        self._downloader = downloader
        self._overrides = overrides
        self._settings = settings

    def scan(
        self,
        *,
        offline: bool = False,
        cancellation: Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> LibraryScanSummary:
        """Scan configured roots and safely retain resumable per-file progress."""

        settings = self._settings or self._repository.get_settings()
        if not settings.roots:
            raise ValueError("no music-library roots are configured")
        self._repository.reconcile_configured_roots(settings.roots)
        stop = cancellation or Event()
        scan_id = self._repository.begin_scan()
        counters = {
            "discovered": 0,
            "processed": 0,
            "unchanged": 0,
            "moved": 0,
            "missing": 0,
            "review": 0,
            "downloaded": 0,
            "download_misses": 0,
            "errors": 0,
        }
        seen: set[str] = set()
        completed_walk = False
        pending: dict[Future[LibraryMetadata], tuple[LibraryFile, bool]] = {}

        def consume(
            future: Future[LibraryMetadata], file: LibraryFile, moved: bool
        ) -> None:
            try:
                metadata = future.result()
                approved = (
                    None
                    if self._overrides is None
                    else self._overrides.get(LocalFileIdentity(file.path))
                )
                if approved is not None:
                    original = metadata.candidate
                    metadata = LibraryMetadata(
                        TrackCandidate(
                            approved.title,
                            approved.artists,
                            approved.album,
                            original.duration_us,
                            evidence=(
                                *original.evidence,
                                "used a user-approved Stage 8 track correction",
                            ),
                            transformations=original.transformations,
                        ),
                        Confidence.APPROVED,
                        metadata.source,
                    )
                reason = metadata.diagnostic
                state = LibraryTrackState.READY
                if metadata.confidence not in (Confidence.HIGH, Confidence.APPROVED):
                    state = LibraryTrackState.REVIEW
                    reason = reason or "metadata is not high confidence"
                lyrics_status = "not-requested"
                if (
                    state is LibraryTrackState.READY
                    and settings.automatic_downloads
                    and self._downloader is not None
                ):
                    lyrics_status = self._downloader(file, metadata, offline)
                    if lyrics_status == "downloaded":
                        counters["downloaded"] += 1
                    elif lyrics_status not in {"cached", "local"}:
                        counters["download_misses"] += 1
                        state = LibraryTrackState.REVIEW
                        reason = f"lyrics: {lyrics_status}"
                track = StoredLibraryTrack(file, metadata, state, lyrics_status, reason)
                self._repository.put_track(track)
                counters["processed"] += 1
                counters["review"] += int(state is LibraryTrackState.REVIEW)
                counters["moved"] += int(moved)
            except Exception:
                counters["errors"] += 1
            if progress is not None:
                progress(counters["discovered"], file.path)

        try:
            with ThreadPoolExecutor(
                max_workers=settings.worker_count,
                thread_name_prefix="lyriflux-library",
            ) as pool:
                for file in self._filesystem.files(settings.roots):
                    if stop.is_set():
                        break
                    counters["discovered"] += 1
                    seen.add(file.file_key)
                    previous = self._repository.matching_file(file)
                    newly_enabled_download = bool(
                        previous is not None
                        and settings.automatic_downloads
                        and previous.state is LibraryTrackState.READY
                        and previous.lyrics_status == "not-requested"
                    )
                    if previous is not None and (
                        previous.file.path == file.path
                        and previous.file.size == file.size
                        and previous.file.mtime_ns == file.mtime_ns
                        and previous.state is not LibraryTrackState.MISSING
                        and not newly_enabled_download
                    ):
                        counters["unchanged"] += 1
                        continue
                    moved = previous is not None and previous.file.path != file.path
                    future = pool.submit(self._metadata.read, file)
                    pending[future] = (file, moved)
                    if len(pending) >= settings.worker_count * 2:
                        first = next(iter(pending))
                        item, was_moved = pending.pop(first)
                        consume(first, item, was_moved)
                else:
                    completed_walk = True
                for future, (file, moved) in tuple(pending.items()):
                    if stop.is_set():
                        future.cancel()
                        continue
                    consume(future, file, moved)
            if completed_walk and not stop.is_set():
                counters["missing"] = self._repository.reconcile_missing(
                    settings.roots, seen
                )
        except KeyboardInterrupt:
            stop.set()
        except Exception:
            counters["errors"] += 1
        summary = LibraryScanSummary(
            scan_id=scan_id,
            **counters,
            cancelled=stop.is_set(),
        )
        self._repository.finish_scan(scan_id, summary)
        return summary
