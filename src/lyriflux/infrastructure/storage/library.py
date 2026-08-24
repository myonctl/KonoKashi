"""SQLite persistence for typed Stage 9 library settings and scan state."""

from __future__ import annotations

import json

from lyriflux.application.settings import default_library_settings
from lyriflux.domain.library import (
    LibraryFile,
    LibraryMetadata,
    LibraryMetadataSource,
    LibraryReviewItem,
    LibraryScanSummary,
    LibrarySettings,
    LibraryTrackState,
    StoredLibraryTrack,
)
from lyriflux.domain.tracks import Confidence, TrackCandidate
from lyriflux.infrastructure.storage.errors import InvalidStoredDataError
from lyriflux.infrastructure.storage.sqlite import SQLiteDatabase, utc_now_text


class SQLiteLibraryRepository:
    """Commit each file independently so interrupted scans are resumable."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def get_settings(self) -> LibrarySettings:
        with self._database.connection(readonly=True) as connection:
            row = connection.execute(
                "SELECT automatic_downloads, worker_count FROM library_settings "
                "WHERE settings_id = 1"
            ).fetchone()
            if row is None:
                return default_library_settings()
            roots = tuple(
                str(item[0])
                for item in connection.execute(
                    "SELECT root_path FROM library_roots WHERE settings_id = 1 "
                    "ORDER BY position"
                )
            )
        try:
            return LibrarySettings(roots, bool(row[0]), int(row[1]))
        except (TypeError, ValueError) as error:
            raise InvalidStoredDataError(
                "stored library settings are invalid"
            ) from error

    def put_settings(self, settings: LibrarySettings) -> None:
        now = utc_now_text()
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO library_settings(
                    settings_id, automatic_downloads, worker_count, updated_at
                ) VALUES (1, ?, ?, ?)
                ON CONFLICT(settings_id) DO UPDATE SET
                    automatic_downloads = excluded.automatic_downloads,
                    worker_count = excluded.worker_count,
                    updated_at = excluded.updated_at
                """,
                (int(settings.automatic_downloads), settings.worker_count, now),
            )
            connection.execute("DELETE FROM library_roots WHERE settings_id = 1")
            connection.executemany(
                "INSERT INTO library_roots(settings_id, position, root_path) "
                "VALUES (1, ?, ?)",
                tuple(enumerate(settings.roots)),
            )
            if settings.roots:
                placeholders = ",".join("?" for _ in settings.roots)
                connection.execute(
                    "UPDATE library_tracks SET state = 'missing', "
                    "review_reason = NULL, updated_at = ? "
                    f"WHERE root_path NOT IN ({placeholders}) AND state != 'missing'",
                    (now, *settings.roots),
                )
            else:
                connection.execute(
                    "UPDATE library_tracks SET state = 'missing', "
                    "review_reason = NULL, updated_at = ? WHERE state != 'missing'",
                    (now,),
                )

    def begin_scan(self) -> int:
        with self._database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO library_scan_runs(status, started_at) "
                "VALUES ('running', ?)",
                (utc_now_text(),),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return a library scan id")
            return cursor.lastrowid

    def matching_file(self, file: LibraryFile) -> StoredLibraryTrack | None:
        with self._database.connection(readonly=True) as connection:
            row = connection.execute(
                "SELECT * FROM library_tracks WHERE file_key = ?", (file.file_key,)
            ).fetchone()
        return None if row is None else _track(row)

    def put_track(self, track: StoredLibraryTrack) -> None:
        now = utc_now_text()
        candidate = track.metadata.candidate
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO library_tracks(
                    file_key, current_path, root_path, file_size, mtime_ns,
                    title, artists_json, album, duration_us, metadata_source,
                    confidence, state, lyrics_status, review_reason,
                    first_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_key) DO UPDATE SET
                    current_path = excluded.current_path,
                    root_path = excluded.root_path,
                    file_size = excluded.file_size,
                    mtime_ns = excluded.mtime_ns,
                    title = excluded.title,
                    artists_json = excluded.artists_json,
                    album = excluded.album,
                    duration_us = excluded.duration_us,
                    metadata_source = excluded.metadata_source,
                    confidence = excluded.confidence,
                    state = excluded.state,
                    lyrics_status = excluded.lyrics_status,
                    review_reason = excluded.review_reason,
                    updated_at = excluded.updated_at
                """,
                (
                    track.file.file_key,
                    track.file.path,
                    track.file.root,
                    track.file.size,
                    track.file.mtime_ns,
                    candidate.title,
                    json.dumps(candidate.artists, ensure_ascii=False),
                    candidate.album,
                    candidate.duration_us,
                    track.metadata.source.value,
                    track.metadata.confidence.value,
                    track.state.value,
                    track.lyrics_status,
                    track.review_reason,
                    now,
                    now,
                ),
            )

    def reconcile_missing(self, roots: tuple[str, ...], seen: set[str]) -> int:
        if not roots:
            return 0
        placeholders = ",".join("?" for _ in roots)
        with self._database.transaction() as connection:
            rows = connection.execute(
                "SELECT file_key FROM library_tracks WHERE root_path IN "
                f"({placeholders}) "
                "AND state != 'missing'",
                roots,
            ).fetchall()
            missing = tuple(str(row[0]) for row in rows if str(row[0]) not in seen)
            connection.executemany(
                "UPDATE library_tracks SET state = 'missing', review_reason = NULL, "
                "updated_at = ? WHERE file_key = ?",
                ((utc_now_text(), key) for key in missing),
            )
        return len(missing)

    def finish_scan(self, scan_id: int, summary: LibraryScanSummary) -> None:
        status = (
            "cancelled"
            if summary.cancelled
            else (
                "failed" if summary.errors and summary.processed == 0 else "completed"
            )
        )
        with self._database.transaction() as connection:
            connection.execute(
                """
                UPDATE library_scan_runs SET
                    status = ?, finished_at = ?, discovered = ?, processed = ?,
                    unchanged = ?, moved = ?, missing = ?, review = ?,
                    downloaded = ?, download_misses = ?, errors = ?
                WHERE scan_id = ?
                """,
                (
                    status,
                    utc_now_text(),
                    summary.discovered,
                    summary.processed,
                    summary.unchanged,
                    summary.moved,
                    summary.missing,
                    summary.review,
                    summary.downloaded,
                    summary.download_misses,
                    summary.errors,
                    scan_id,
                ),
            )

    def review_items(self, limit: int = 100) -> tuple[LibraryReviewItem, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("review limit must be between 1 and 1000")
        with self._database.connection(readonly=True) as connection:
            rows = connection.execute(
                """
                SELECT current_path, title, artists_json, review_reason
                FROM library_tracks WHERE state = 'review'
                ORDER BY current_path LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(
            LibraryReviewItem(
                str(row[0]),
                None if row[1] is None else str(row[1]),
                tuple(json.loads(str(row[2]))),
                str(row[3] or "review required"),
            )
            for row in rows
        )

    def counts(self) -> tuple[int, int, int]:
        with self._database.connection(readonly=True) as connection:
            roots = int(
                connection.execute("SELECT COUNT(*) FROM library_roots").fetchone()[0]
            )
            active = int(
                connection.execute(
                    "SELECT COUNT(*) FROM library_tracks WHERE state != 'missing'"
                ).fetchone()[0]
            )
            review = int(
                connection.execute(
                    "SELECT COUNT(*) FROM library_tracks WHERE state = 'review'"
                ).fetchone()[0]
            )
        return roots, active, review


def _track(row: object) -> StoredLibraryTrack:
    values = row  # sqlite3.Row supports name indexing but not a useful public protocol
    candidate = TrackCandidate(
        values["title"],  # type: ignore[index]
        tuple(json.loads(str(values["artists_json"]))),  # type: ignore[index]
        values["album"],  # type: ignore[index]
        values["duration_us"],  # type: ignore[index]
    )
    metadata = LibraryMetadata(
        candidate,
        Confidence(str(values["confidence"])),  # type: ignore[index]
        LibraryMetadataSource(str(values["metadata_source"])),  # type: ignore[index]
        values["review_reason"],  # type: ignore[index]
    )
    return StoredLibraryTrack(
        LibraryFile(
            str(values["file_key"]),  # type: ignore[index]
            str(values["current_path"]),  # type: ignore[index]
            str(values["root_path"]),  # type: ignore[index]
            int(values["file_size"]),  # type: ignore[index]
            int(values["mtime_ns"]),  # type: ignore[index]
        ),
        metadata,
        LibraryTrackState(str(values["state"])),  # type: ignore[index]
        str(values["lyrics_status"]),  # type: ignore[index]
        values["review_reason"],  # type: ignore[index]
    )
