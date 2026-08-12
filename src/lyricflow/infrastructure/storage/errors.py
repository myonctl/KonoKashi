"""Controlled persistence failures exposed by SQLite infrastructure."""

from __future__ import annotations

from pathlib import Path


class StorageError(RuntimeError):
    """Base class for expected local-storage failures."""

    def __init__(self, message: str, *, path: Path | None = None) -> None:
        super().__init__(message)
        self.path = path


class StoragePathError(StorageError):
    """The database path cannot be created, opened, or written."""


class StorageLockedError(StorageError):
    """The database remained locked beyond its bounded busy timeout."""


class StorageCorruptError(StorageError):
    """SQLite reports malformed or corrupt database content."""


class StorageMigrationError(StorageError):
    """A schema migration could not be applied atomically."""


class UnsupportedSchemaError(StorageError):
    """The database schema is newer or differs from known migration history."""


class InvalidStoredDataError(StorageError):
    """Persisted values cannot be reconstructed as typed application values."""


class StorageValidationError(StorageError):
    """A requested durable write violates product persistence policy."""
