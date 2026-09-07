"""Consistent, non-overwriting SQLite backup support."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from konokashi.infrastructure.storage.errors import StorageError, StoragePathError
from konokashi.infrastructure.storage.sqlite import SQLiteDatabase


@dataclass(frozen=True, slots=True)
class StorageBackup:
    """Verified backup artifact metadata."""

    destination: Path
    size_bytes: int
    schema_version: int


def backup_database(database: SQLiteDatabase, destination: Path) -> StorageBackup:
    """Create one consistent backup atomically and never overwrite a target."""

    resolved_destination = destination.expanduser().resolve()
    if resolved_destination == database.path.expanduser().resolve():
        raise StoragePathError(
            "backup destination must differ from the live database",
            path=resolved_destination,
        )
    if resolved_destination.exists():
        raise StoragePathError(
            "backup destination already exists; choose a new file",
            path=resolved_destination,
        )
    try:
        resolved_destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{resolved_destination.name}.",
            suffix=".tmp",
            dir=resolved_destination.parent,
        )
        os.close(descriptor)
    except OSError as error:
        raise StoragePathError(
            f"cannot prepare backup destination ({error.__class__.__name__})",
            path=resolved_destination,
        ) from error

    temporary = Path(temporary_name)
    try:
        with database.connection(readonly=True) as source:
            target = sqlite3.connect(temporary)
            try:
                source.backup(target)
                integrity = target.execute("PRAGMA quick_check").fetchone()
                if integrity is None or str(integrity[0]).lower() != "ok":
                    raise StorageError(
                        "backup integrity verification failed",
                        path=resolved_destination,
                    )
                history = target.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
                schema_version = int(history[-1][0]) if history else 0
            finally:
                target.close()
        temporary.chmod(0o600)
        temporary.replace(resolved_destination)
    except (OSError, sqlite3.Error, StorageError) as error:
        temporary.unlink(missing_ok=True)
        if isinstance(error, StorageError):
            raise
        raise StorageError(
            f"database backup failed ({error.__class__.__name__})",
            path=resolved_destination,
        ) from error
    return StorageBackup(
        resolved_destination,
        resolved_destination.stat().st_size,
        schema_version,
    )
