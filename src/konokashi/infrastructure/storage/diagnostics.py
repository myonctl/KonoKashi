"""Read-only SQLite status collection for the normal diagnostic CLI path."""

from __future__ import annotations

import os
from pathlib import Path

from konokashi.application.storage_diagnostics import StorageCounts, StorageStatus
from konokashi.infrastructure.storage.errors import StorageError
from konokashi.infrastructure.storage.migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
)
from konokashi.infrastructure.storage.sqlite import SQLiteDatabase


def _writable(path: Path) -> bool:
    if path.exists():
        return os.access(path, os.W_OK) and os.access(path.parent, os.W_OK)
    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return parent.is_dir() and os.access(parent, os.W_OK)


def inspect_storage(path: Path) -> StorageStatus:
    """Inspect a database without creating, migrating, deleting, or resetting it."""

    exists = path.is_file()
    writable = _writable(path)
    if not exists:
        return StorageStatus(
            path=str(path),
            exists=False,
            current_schema_version=CURRENT_SCHEMA_VERSION,
            schema_version=None,
            migration_status="not initialized; run `konokashi storage migrate`",
            readable=False,
            writable=writable,
            integrity_status="not checked",
        )
    database = SQLiteDatabase(path)
    try:
        history = database.migration_history()
        schema_version = history[-1][0] if history else 0
        expected = {migration.version: migration for migration in MIGRATIONS}
        incompatible = schema_version > CURRENT_SCHEMA_VERSION or any(
            version not in expected
            or expected[version].name != name
            or expected[version].checksum != checksum
            for version, name, checksum, _applied_at in history
        )
        with database.connection(readonly=True) as connection:
            integrity_row = connection.execute("PRAGMA quick_check").fetchone()
            integrity = (
                "unknown"
                if integrity_row is None
                else str(integrity_row[0]).strip().lower()
            )
            counts = None
            if schema_version == CURRENT_SCHEMA_VERSION and not incompatible:
                counts = StorageCounts(
                    source_identities=int(
                        connection.execute(
                            "SELECT COUNT(*) FROM source_identities"
                        ).fetchone()[0]
                    ),
                    track_overrides=int(
                        connection.execute(
                            "SELECT COUNT(*) FROM track_overrides"
                        ).fetchone()[0]
                    ),
                    lyric_documents=int(
                        connection.execute(
                            "SELECT COUNT(*) FROM lyrics_documents"
                        ).fetchone()[0]
                    ),
                    lyrics_matches=int(
                        connection.execute(
                            "SELECT COUNT(*) FROM lyrics_matches"
                        ).fetchone()[0]
                    ),
                    lyric_match_rejections=int(
                        connection.execute(
                            "SELECT COUNT(*) FROM lyrics_match_rejections"
                        ).fetchone()[0]
                    ),
                    provider_cache_entries=int(
                        connection.execute(
                            "SELECT COUNT(*) FROM provider_cache"
                        ).fetchone()[0]
                    ),
                    representation_candidates=int(
                        connection.execute(
                            "SELECT COUNT(*) FROM lyric_representation_candidates"
                        ).fetchone()[0]
                    ),
                    representation_decisions=int(
                        connection.execute(
                            "SELECT COUNT(*) FROM lyric_representation_decisions"
                        ).fetchone()[0]
                    ),
                    language_overrides=int(
                        connection.execute(
                            "SELECT COUNT(*) FROM lyric_document_language_overrides"
                        ).fetchone()[0]
                    ),
                    lyric_document_delays=int(
                        connection.execute(
                            "SELECT COUNT(*) FROM lyric_document_timing"
                        ).fetchone()[0]
                    ),
                    audio_output_calibrations=int(
                        connection.execute(
                            "SELECT COUNT(*) FROM audio_output_calibrations"
                        ).fetchone()[0]
                    ),
                    library_roots=int(
                        connection.execute(
                            "SELECT COUNT(*) FROM library_roots"
                        ).fetchone()[0]
                    ),
                    library_tracks=int(
                        connection.execute(
                            "SELECT COUNT(*) FROM library_tracks"
                        ).fetchone()[0]
                    ),
                    library_review_items=int(
                        connection.execute(
                            "SELECT COUNT(*) FROM library_tracks WHERE state = 'review'"
                        ).fetchone()[0]
                    ),
                    library_scan_runs=int(
                        connection.execute(
                            "SELECT COUNT(*) FROM library_scan_runs"
                        ).fetchone()[0]
                    ),
                )
        if incompatible:
            return StorageStatus(
                path=str(path),
                exists=True,
                current_schema_version=CURRENT_SCHEMA_VERSION,
                schema_version=schema_version,
                migration_status="incompatible migration history",
                readable=True,
                writable=writable,
                integrity_status=integrity,
                error="database schema is newer or differs from this build",
            )
        if integrity != "ok":
            return StorageStatus(
                path=str(path),
                exists=True,
                current_schema_version=CURRENT_SCHEMA_VERSION,
                schema_version=schema_version,
                migration_status="current"
                if schema_version == CURRENT_SCHEMA_VERSION
                else "pending",
                readable=True,
                writable=writable,
                integrity_status=integrity,
                error=(
                    "database integrity check did not return ok; "
                    "no recovery was attempted"
                ),
            )
        return StorageStatus(
            path=str(path),
            exists=True,
            current_schema_version=CURRENT_SCHEMA_VERSION,
            schema_version=schema_version,
            migration_status=(
                "current" if schema_version == CURRENT_SCHEMA_VERSION else "pending"
            ),
            readable=True,
            writable=writable,
            integrity_status=integrity,
            counts=counts,
        )
    except (OSError, StorageError) as error:
        return StorageStatus(
            path=str(path),
            exists=True,
            current_schema_version=CURRENT_SCHEMA_VERSION,
            schema_version=None,
            migration_status="unavailable",
            readable=False,
            writable=writable,
            integrity_status="not checked",
            error=str(error),
        )
