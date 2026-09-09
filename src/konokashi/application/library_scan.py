"""Incremental, cancellable music-directory scanning orchestration."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event
from typing import Protocol

from konokashi.domain.identity import LocalFileIdentity
from konokashi.domain.library import (
    LibraryFile,
    LibraryMetadata,
    LibraryReviewItem,
    LibraryScanFailure,
    LibraryScanIssue,
    LibraryScanIssueCategory,
    LibraryScanSummary,
    LibrarySettings,
    LibraryTrackState,
    StoredLibraryTrack,
)
from konokashi.domain.tracks import ApprovedTrackIdentity, Confidence, TrackCandidate


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
        issues: list[LibraryScanIssue] = []
        issue_counts: Counter[LibraryScanIssueCategory] = Counter()

        def record_issue(issue: LibraryScanIssue) -> None:
            counters["errors"] += 1
            issue_counts[issue.category] += 1
            if len(issues) >= 100:
                return
            detail = " ".join(issue.detail.split()) or "Operation failed."
            if len(detail) > 240:
                detail = f"{detail[:239]}…"
            path = issue.path
            if path is not None and len(path) > 2_048:
                path = f"…{path[-2_047:]}"
            issues.append(LibraryScanIssue(issue.category, path, detail))

        def caught_issue(
            category: LibraryScanIssueCategory,
            file: LibraryFile | None,
            error: BaseException,
        ) -> LibraryScanIssue:
            return LibraryScanIssue(
                category,
                None if file is None else file.path,
                f"{type(error).__name__}: {error}",
            )

        def consume(
            future: Future[LibraryMetadata], file: LibraryFile, moved: bool
        ) -> None:
            try:
                metadata = future.result()
            except Exception as error:
                record_issue(
                    caught_issue(LibraryScanIssueCategory.METADATA_READ, file, error)
                )
                if progress is not None:
                    progress(counters["discovered"], file.path)
                return
            if not isinstance(metadata, LibraryMetadata):
                record_issue(
                    LibraryScanIssue(
                        LibraryScanIssueCategory.METADATA_READ,
                        file.path,
                        "Metadata reader returned an invalid result.",
                    )
                )
                if progress is not None:
                    progress(counters["discovered"], file.path)
                return
            if metadata.issue is not None:
                record_issue(metadata.issue)
            try:
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
                    try:
                        lyrics_status = self._downloader(file, metadata, offline)
                    except Exception as error:
                        lyrics_status = "error"
                        state = LibraryTrackState.REVIEW
                        reason = "Lyrics download failed; review and retry this item."
                        record_issue(
                            caught_issue(LibraryScanIssueCategory.DOWNLOAD, file, error)
                        )
                    else:
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
            except Exception as error:
                record_issue(
                    caught_issue(LibraryScanIssueCategory.STORAGE, file, error)
                )
            if progress is not None:
                progress(counters["discovered"], file.path)

        try:
            with ThreadPoolExecutor(
                max_workers=settings.worker_count,
                thread_name_prefix="konokashi-library",
            ) as pool:
                for file in self._filesystem.files(settings.roots):
                    if stop.is_set():
                        break
                    counters["discovered"] += 1
                    seen.add(file.file_key)
                    try:
                        previous = self._repository.matching_file(file)
                    except Exception as error:
                        record_issue(
                            caught_issue(LibraryScanIssueCategory.STORAGE, file, error)
                        )
                        break
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
                try:
                    counters["missing"] = self._repository.reconcile_missing(
                        settings.roots, seen
                    )
                except Exception as error:
                    record_issue(
                        caught_issue(LibraryScanIssueCategory.STORAGE, None, error)
                    )
        except KeyboardInterrupt:
            stop.set()
        except LibraryScanFailure as error:
            record_issue(error.issue)
        except Exception as error:
            record_issue(caught_issue(LibraryScanIssueCategory.UNKNOWN, None, error))
        summary = LibraryScanSummary(
            scan_id=scan_id,
            **counters,
            cancelled=stop.is_set(),
            issues=tuple(issues),
            error_categories=tuple(
                sorted(issue_counts.items(), key=lambda item: item[0].value)
            ),
        )
        self._repository.finish_scan(scan_id, summary)
        return summary
