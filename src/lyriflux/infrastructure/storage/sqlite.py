"""SQLite connection ownership, transactions, and append-only migrations."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from lyriflux.infrastructure.storage.errors import (
    StorageCorruptError,
    StorageError,
    StorageLockedError,
    StorageMigrationError,
    StoragePathError,
    UnsupportedSchemaError,
)
from lyriflux.infrastructure.storage.migrations import MIGRATIONS, Migration

_BUSY_TIMEOUT_MS = 1_000


def utc_now_text() -> str:
    """Return a stable, timezone-aware UTC timestamp for persisted metadata."""

    return datetime.now(UTC).isoformat()


def _map_sqlite_error(error: sqlite3.Error, path: Path) -> StorageError:
    code = getattr(error, "sqlite_errorcode", None)
    message = str(error).lower()
    if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or "locked" in message:
        return StorageLockedError(
            "database is busy; close another writer and try again", path=path
        )
    if code in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB} or any(
        marker in message for marker in ("malformed", "not a database")
    ):
        return StorageCorruptError(
            "database is corrupt or not a SQLite database; it was not modified",
            path=path,
        )
    if code in {sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_READONLY}:
        return StoragePathError(
            "database path cannot be opened for the requested operation", path=path
        )
    return StorageError(f"database operation failed: {error}", path=path)


class SQLiteDatabase:
    """Open a fresh thread-owned SQLite connection for each operation."""

    def __init__(self, path: Path, *, busy_timeout_ms: int = _BUSY_TIMEOUT_MS) -> None:
        self.path = path
        self.busy_timeout_ms = busy_timeout_ms

    def _prepare_parent(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise StoragePathError(
                f"cannot create database parent directory: {error}", path=self.path
            ) from error

    def _create_private_file_if_missing(self) -> None:
        """Pre-create a new database privately without changing existing files."""

        descriptor: int | None = None
        try:
            descriptor = os.open(
                self.path,
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            os.fchmod(descriptor, 0o600)
        except FileExistsError:
            return
        except OSError as error:
            raise StoragePathError(
                f"cannot create private database file: {error}", path=self.path
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _secure_permissions(self) -> None:
        """Restrict a validated writable database and its transient sidecars."""

        for path in (
            self.path,
            Path(f"{self.path}-journal"),
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ):
            try:
                path.chmod(0o600)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise StoragePathError(
                    f"cannot restrict database permissions: {error}", path=path
                ) from error

    def _open(self, *, readonly: bool = False) -> sqlite3.Connection:
        try:
            if readonly:
                uri = f"{self.path.resolve().as_uri()}?mode=ro"
                connection = sqlite3.connect(
                    uri,
                    uri=True,
                    isolation_level=None,
                    timeout=self.busy_timeout_ms / 1_000,
                )
            else:
                connection = sqlite3.connect(
                    self.path,
                    isolation_level=None,
                    timeout=self.busy_timeout_ms / 1_000,
                )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms:d}")
            return connection
        except (OSError, sqlite3.Error) as error:
            if isinstance(error, sqlite3.Error):
                raise _map_sqlite_error(error, self.path) from error
            raise StoragePathError(
                f"cannot open database path: {error}", path=self.path
            ) from error

    @contextmanager
    def connection(self, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield one connection and always close it after the operation."""

        connection = self._open(readonly=readonly)
        try:
            yield connection
        except sqlite3.Error as error:
            raise _map_sqlite_error(error, self.path) from error
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run one explicit immediate transaction with rollback on every failure."""

        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def initialize(self, migrations: Sequence[Migration] = MIGRATIONS) -> int:
        """Create the parent and migrate sequentially to the supplied current schema."""

        self._prepare_parent()
        ordered = tuple(migrations)
        expected_versions = tuple(range(1, len(ordered) + 1))
        if tuple(item.version for item in ordered) != expected_versions:
            raise ValueError("migrations must be sequential and start at version 1")
        self._create_private_file_if_missing()
        if not self._history_table_exists():
            self._secure_permissions()
            self._ensure_history_table()
        applied = self.migration_history()
        by_version = {migration.version: migration for migration in ordered}
        if applied and applied[-1][0] > len(ordered):
            raise UnsupportedSchemaError(
                "database schema is newer than this LyriFlux build; upgrade the app",
                path=self.path,
            )
        for version, name, checksum, _applied_at in applied:
            expected = by_version.get(version)
            if (
                expected is None
                or expected.name != name
                or expected.checksum != checksum
            ):
                raise UnsupportedSchemaError(
                    "database migration history differs from this LyriFlux build",
                    path=self.path,
                )
        self._secure_permissions()
        current = applied[-1][0] if applied else 0
        for migration in ordered[current:]:
            self._apply_migration(migration)
        self._secure_permissions()
        return len(ordered)

    def _ensure_history_table(self) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )

    def _history_table_exists(self) -> bool:
        if not self.path.exists():
            return False
        with self.connection(readonly=True) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = ?
                """,
                ("schema_migrations",),
            ).fetchone()
        return row is not None

    def _apply_migration(self, migration: Migration) -> None:
        try:
            with self.transaction() as connection:
                for statement in migration.statements:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, name, checksum, applied_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        migration.version,
                        migration.name,
                        migration.checksum,
                        utc_now_text(),
                    ),
                )
        except StorageError as error:
            raise StorageMigrationError(
                f"migration {migration.version} ({migration.name}) failed: {error}",
                path=self.path,
            ) from error

    def migration_history(self) -> tuple[tuple[int, str, str, str], ...]:
        """Return inspectable ordered migration history."""

        try:
            with self.connection(readonly=True) as connection:
                rows = connection.execute(
                    """
                    SELECT version, name, checksum, applied_at
                    FROM schema_migrations ORDER BY version
                    """
                ).fetchall()
        except StorageError:
            raise
        return tuple(
            (
                int(row["version"]),
                str(row["name"]),
                str(row["checksum"]),
                str(row["applied_at"]),
            )
            for row in rows
        )
