"""Stage 9 incremental scanner, settings, cancellation, and safety regressions."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event, Lock
from time import sleep

import pytest

from konokashi.application.library_scan import LibraryScanService
from konokashi.cli import main
from konokashi.domain.identity import LocalFileIdentity
from konokashi.domain.library import (
    LibraryFile,
    LibraryMetadata,
    LibraryMetadataSource,
    LibraryScanIssue,
    LibraryScanIssueCategory,
    LibraryScanStatus,
    LibrarySettings,
)
from konokashi.domain.tracks import ApprovedTrackIdentity, Confidence, TrackCandidate
from konokashi.infrastructure.metadata.library import (
    MusicDirectoryFilesystem,
    MutagenLibraryMetadataReader,
)
from konokashi.infrastructure.storage.bootstrap import open_storage


class _Files:
    def __init__(self, files: tuple[LibraryFile, ...]) -> None:
        self.items = files

    def files(self, roots: tuple[str, ...]):  # type: ignore[no-untyped-def]
        del roots
        yield from self.items


class _Reader:
    def __init__(self, confidence: Confidence = Confidence.HIGH) -> None:
        self.confidence = confidence
        self.calls: list[str] = []

    def read(self, file: LibraryFile) -> LibraryMetadata:
        self.calls.append(file.path)
        artists = ("Artist",) if self.confidence is not Confidence.LOW else ()
        return LibraryMetadata(
            TrackCandidate("Title", artists, "Album", 180_000_000),
            self.confidence,
            LibraryMetadataSource.TAGS,
        )


def _file(key: str, path: str, *, mtime: int = 1) -> LibraryFile:
    return LibraryFile(key, path, "/music", 123, mtime)


def test_library_settings_are_typed_validated_and_dormant_by_default() -> None:
    assert LibrarySettings() == LibrarySettings((), False, 4)
    with pytest.raises(ValueError, match="absolute"):
        LibrarySettings(("relative",))
    with pytest.raises(ValueError, match="duplicates"):
        LibrarySettings(("/music", "/music"))
    with pytest.raises(ValueError, match="overlap"):
        LibrarySettings(("/music", "/music/album"))
    with pytest.raises(ValueError, match="between 1 and 8"):
        LibrarySettings(("/music",), worker_count=9)


def test_incremental_scan_skips_unchanged_and_handles_move_and_delete(
    tmp_path: Path,
) -> None:
    storage = open_storage(tmp_path / "library.sqlite3")
    storage.library.put_settings(LibrarySettings(("/music",), worker_count=2))
    files = _Files((_file("stable-key", "/music/old.flac"),))
    reader = _Reader()
    service = LibraryScanService(files, reader, storage.library)

    first = service.scan()
    second = service.scan()
    files.items = (_file("stable-key", "/music/new.flac"),)
    moved = service.scan()
    files.items = ()
    deleted = service.scan()

    assert (first.processed, first.unchanged) == (1, 0)
    assert (second.processed, second.unchanged) == (0, 1)
    assert (moved.processed, moved.moved) == (1, 1)
    assert deleted.missing == 1
    assert reader.calls == ["/music/old.flac", "/music/new.flac"]
    assert storage.library.counts() == (1, 0, 0)


def test_cancelled_scan_keeps_unseen_index_entries_active(tmp_path: Path) -> None:
    storage = open_storage(tmp_path / "cancel.sqlite3")
    storage.library.put_settings(LibrarySettings(("/music",)))
    files = _Files((_file("one", "/music/one.flac"),))
    service = LibraryScanService(files, _Reader(), storage.library)
    assert service.scan().processed == 1

    cancellation = Event()
    cancellation.set()
    files.items = ()
    summary = service.scan(cancellation=cancellation)

    assert summary.cancelled
    assert summary.missing == 0
    assert storage.library.counts() == (1, 1, 0)


def test_mid_scan_cancellation_retains_progress_and_next_scan_resumes(
    tmp_path: Path,
) -> None:
    storage = open_storage(tmp_path / "resume.sqlite3")
    storage.library.put_settings(LibrarySettings(("/music",), worker_count=1))
    files = _Files(
        tuple(_file(f"key-{index}", f"/music/{index}.flac") for index in range(3))
    )
    reader = _Reader()
    cancellation = Event()

    def cancel_after_progress(count: int, path: str) -> None:
        del count, path
        cancellation.set()

    interrupted = LibraryScanService(files, reader, storage.library).scan(
        cancellation=cancellation,
        progress=cancel_after_progress,
    )
    resumed = LibraryScanService(files, reader, storage.library).scan()

    assert interrupted.cancelled
    assert interrupted.processed == 1
    assert resumed.unchanged == 1
    assert resumed.processed == 2
    assert storage.library.counts() == (1, 3, 0)


def test_unavailable_root_fails_without_false_missing_reconciliation(
    tmp_path: Path,
) -> None:
    storage = open_storage(tmp_path / "unavailable.sqlite3")
    missing_root = tmp_path / "not-present"
    storage.library.put_settings(LibrarySettings((str(missing_root),)))
    indexed = _file("one", str(missing_root / "one.flac"))
    indexed = replace(indexed, root=str(missing_root))
    LibraryScanService(_Files((indexed,)), _Reader(), storage.library).scan()

    summary = LibraryScanService(
        MusicDirectoryFilesystem(), _Reader(), storage.library
    ).scan()

    assert summary.errors == 1
    assert summary.status is LibraryScanStatus.FAILED
    assert summary.error_categories == ((LibraryScanIssueCategory.ROOT_UNAVAILABLE, 1),)
    assert summary.missing == 0
    assert storage.library.counts() == (1, 1, 0)


def test_inaccessible_subtree_is_typed_and_does_not_reconcile_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "music"
    root.mkdir()
    storage = open_storage(tmp_path / "subtree.sqlite3")
    storage.library.put_settings(LibrarySettings((str(root),)))
    indexed = replace(_file("one", str(root / "one.flac")), root=str(root))
    LibraryScanService(_Files((indexed,)), _Reader(), storage.library).scan()

    def denied_walk(path: Path, *, followlinks: bool, onerror: object):  # type: ignore[no-untyped-def]
        del path, followlinks
        assert callable(onerror)
        onerror(PermissionError(13, "denied", str(root / "private")))
        yield from ()

    monkeypatch.setattr(
        "konokashi.infrastructure.metadata.library.os.walk", denied_walk
    )
    summary = LibraryScanService(
        MusicDirectoryFilesystem(), _Reader(), storage.library
    ).scan()

    assert summary.status is LibraryScanStatus.FAILED
    assert summary.error_categories == ((LibraryScanIssueCategory.DIRECTORY_READ, 1),)
    assert summary.missing == 0
    assert storage.library.counts() == (1, 1, 0)


def test_automatic_downloads_only_run_for_high_confidence_metadata(
    tmp_path: Path,
) -> None:
    storage = open_storage(tmp_path / "policy.sqlite3")
    storage.library.put_settings(
        LibrarySettings(("/music",), automatic_downloads=True, worker_count=1)
    )
    downloads: list[str] = []

    def download(file: LibraryFile, metadata: LibraryMetadata, offline: bool) -> str:
        del metadata, offline
        downloads.append(file.path)
        return "downloaded"

    high = LibraryScanService(
        _Files((_file("high", "/music/high.flac"),)),
        _Reader(Confidence.HIGH),
        storage.library,
        downloader=download,
    ).scan()
    medium = LibraryScanService(
        _Files((_file("medium", "/music/medium.flac"),)),
        _Reader(Confidence.MEDIUM),
        storage.library,
        downloader=download,
    ).scan()

    assert high.downloaded == 1
    assert medium.review == 1
    assert downloads == ["/music/high.flac"]
    assert storage.library.review_items()[0].path == "/music/medium.flac"


def test_user_approved_stage_eight_correction_allows_uncertain_item_download(
    tmp_path: Path,
) -> None:
    storage = open_storage(tmp_path / "approved.sqlite3")
    storage.library.put_settings(
        LibrarySettings(("/music",), automatic_downloads=True, worker_count=1)
    )
    storage.track_overrides.put(
        LocalFileIdentity("/music/uncertain.flac"),
        ApprovedTrackIdentity("Approved title", ("Approved artist",)),
    )
    observed: list[Confidence] = []

    def download(file: LibraryFile, metadata: LibraryMetadata, offline: bool) -> str:
        del file, offline
        observed.append(metadata.confidence)
        return "downloaded"

    summary = LibraryScanService(
        _Files((_file("uncertain", "/music/uncertain.flac"),)),
        _Reader(Confidence.LOW),
        storage.library,
        downloader=download,
        overrides=storage.track_overrides,
    ).scan()

    assert summary.downloaded == 1
    assert summary.review == 0
    assert observed == [Confidence.APPROVED]


def test_mutagen_reader_uses_conservative_filename_fallback_without_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []

    def read(path: str, *, easy: bool):
        calls.append((path, easy))
        return None

    monkeypatch.setattr("konokashi.infrastructure.metadata.library.mutagen.File", read)
    result = MutagenLibraryMetadataReader().read(
        _file("key", "/music/07 - Artist - Song Title.mp3")
    )

    assert result.candidate.title == "Song Title"
    assert result.candidate.artists == ("Artist",)
    assert result.confidence is Confidence.MEDIUM
    assert calls == [("/music/07 - Artist - Song Title.mp3", True)]


def test_mutagen_failure_is_typed_and_keeps_usable_filename_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_path = "/music/private/Artist - Song.mp3"

    def fail(path: str, *, easy: bool):
        del path, easy
        raise OSError("private low-level detail")

    monkeypatch.setattr("konokashi.infrastructure.metadata.library.mutagen.File", fail)
    result = MutagenLibraryMetadataReader().read(_file("key", private_path))

    assert result.candidate.title == "Song"
    assert result.issue is not None
    assert result.issue.category is LibraryScanIssueCategory.METADATA_READ
    assert result.issue.path == private_path
    assert "private low-level detail" not in result.issue.detail


def test_scan_reports_partial_and_total_failures_truthfully(tmp_path: Path) -> None:
    class SelectiveReader(_Reader):
        def read(self, file: LibraryFile) -> LibraryMetadata:
            if file.file_key != "good":
                raise ValueError("broken metadata")
            return super().read(file)

    storage = open_storage(tmp_path / "outcomes.sqlite3")
    storage.library.put_settings(LibrarySettings(("/music",), worker_count=1))
    partial = LibraryScanService(
        _Files((_file("good", "/music/good.flac"), _file("bad", "/music/bad.flac"))),
        SelectiveReader(),
        storage.library,
    ).scan()
    failed = LibraryScanService(
        _Files((_file("bad-2", "/music/bad-2.flac"),)),
        SelectiveReader(),
        storage.library,
    ).scan()

    assert partial.status is LibraryScanStatus.COMPLETED_WITH_ERRORS
    assert (partial.processed, partial.errors) == (1, 1)
    assert failed.status is LibraryScanStatus.FAILED
    assert (failed.processed, failed.errors) == (0, 1)


def test_downloader_exception_is_reviewable_and_scan_continues(tmp_path: Path) -> None:
    storage = open_storage(tmp_path / "download-error.sqlite3")
    storage.library.put_settings(
        LibrarySettings(("/music",), automatic_downloads=True, worker_count=1)
    )

    def fail_download(
        file: LibraryFile, metadata: LibraryMetadata, offline: bool
    ) -> str:
        del file, metadata, offline
        raise RuntimeError("provider detail")

    summary = LibraryScanService(
        _Files((_file("one", "/music/one.flac"),)),
        _Reader(),
        storage.library,
        downloader=fail_download,
    ).scan()

    assert summary.status is LibraryScanStatus.COMPLETED_WITH_ERRORS
    assert summary.error_categories == ((LibraryScanIssueCategory.DOWNLOAD, 1),)
    assert summary.review == 1
    item = storage.library.review_items()[0]
    assert item.path == "/music/one.flac"
    assert "retry" in item.reason


def test_scan_bounds_local_issue_details_but_preserves_category_total(
    tmp_path: Path,
) -> None:
    class IssueReader(_Reader):
        def read(self, file: LibraryFile) -> LibraryMetadata:
            result = super().read(file)
            return replace(
                result,
                issue=LibraryScanIssue(
                    LibraryScanIssueCategory.METADATA_READ,
                    file.path,
                    "x" * 500,
                ),
            )

    storage = open_storage(tmp_path / "bounded.sqlite3")
    storage.library.put_settings(LibrarySettings(("/music",), worker_count=2))
    files = tuple(_file(f"key-{index}", f"/music/{index}.flac") for index in range(120))
    summary = LibraryScanService(_Files(files), IssueReader(), storage.library).scan()

    assert summary.errors == 120
    assert len(summary.issues) == 100
    assert max(len(issue.detail) for issue in summary.issues) == 240
    assert summary.error_categories == ((LibraryScanIssueCategory.METADATA_READ, 120),)
    with storage.database.connection(readonly=True) as connection:
        run = connection.execute(
            "SELECT status FROM library_scan_runs WHERE scan_id = ?",
            (summary.scan_id,),
        ).fetchone()
        categories = connection.execute(
            "SELECT category, error_count FROM library_scan_error_counts "
            "WHERE scan_id = ?",
            (summary.scan_id,),
        ).fetchall()
    assert run[0] == "completed-with-errors"
    assert [tuple(row) for row in categories] == [("metadata-read", 120)]


def test_large_fixture_never_exceeds_configured_worker_bound(tmp_path: Path) -> None:
    storage = open_storage(tmp_path / "large.sqlite3")
    storage.library.put_settings(LibrarySettings(("/music",), worker_count=3))
    lock = Lock()
    active = 0
    maximum = 0

    class SlowReader(_Reader):
        def read(self, file: LibraryFile) -> LibraryMetadata:
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            sleep(0.001)
            result = super().read(file)
            with lock:
                active -= 1
            return result

    files = tuple(_file(f"key-{index}", f"/music/{index}.flac") for index in range(200))
    summary = LibraryScanService(_Files(files), SlowReader(), storage.library).scan()

    assert summary.processed == 200
    assert maximum <= 3


def test_library_cli_configures_scans_and_resumes_without_provider(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "Small library"
    root.mkdir()
    recording = root / "Artist - Song.mp3"
    recording.write_bytes(b"not real audio")
    database = tmp_path / "cli.sqlite3"

    def unexpected_provider():
        raise AssertionError("automatic downloads defaulted to network access")

    assert (
        main(
            ["library", "settings", "--root", str(root), "--workers", "1"],
            database_path=database,
        )
        == 0
    )
    assert (
        main(
            ["library", "scan", "--offline"],
            database_path=database,
            lyrics_provider_factory=unexpected_provider,
        )
        == 1
    )
    assert (
        main(
            ["library", "scan", "--offline"],
            database_path=database,
            lyrics_provider_factory=unexpected_provider,
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "automatic downloads: off" in output
    assert "status: completed-with-errors" in output
    assert "processed: 1" in output
    assert "unchanged: 1" in output
    assert recording.read_bytes() == b"not real audio"
    assert (
        main(
            ["library", "settings", "--clear-roots"],
            database_path=database,
        )
        == 0
    )
    assert open_storage(database).library.get_settings().roots == ()
    assert open_storage(database).library.counts() == (0, 0, 0)
