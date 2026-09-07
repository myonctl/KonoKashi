"""Explicit construction of SQLite-backed repositories without global state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from konokashi.infrastructure.storage.diagnostics import inspect_storage
from konokashi.infrastructure.storage.errors import UnsupportedSchemaError
from konokashi.infrastructure.storage.library import SQLiteLibraryRepository
from konokashi.infrastructure.storage.lyrics import SQLiteLyricsRepository
from konokashi.infrastructure.storage.lyrics_matches import (
    SQLiteLyricsMatchRepository,
)
from konokashi.infrastructure.storage.paths import default_database_path
from konokashi.infrastructure.storage.provider_cache import (
    SQLiteProviderCacheRepository,
)
from konokashi.infrastructure.storage.representations import (
    SQLiteRepresentationRepository,
)
from konokashi.infrastructure.storage.settings import SQLiteSettingsRepository
from konokashi.infrastructure.storage.source_identities import (
    SQLiteSourceIdentityRepository,
)
from konokashi.infrastructure.storage.sqlite import SQLiteDatabase
from konokashi.infrastructure.storage.sqlite_track_overrides import (
    SQLiteTrackOverrideRepository,
)
from konokashi.infrastructure.storage.timing_calibrations import (
    SQLiteTimingCalibrationRepository,
)


@dataclass(frozen=True, slots=True)
class StorageRepositories:
    """One explicit repository bundle sharing only an immutable database path."""

    database: SQLiteDatabase
    source_identities: SQLiteSourceIdentityRepository
    track_overrides: SQLiteTrackOverrideRepository
    settings: SQLiteSettingsRepository
    lyrics: SQLiteLyricsRepository
    lyrics_matches: SQLiteLyricsMatchRepository
    provider_cache: SQLiteProviderCacheRepository
    representations: SQLiteRepresentationRepository
    timing_calibrations: SQLiteTimingCalibrationRepository
    library: SQLiteLibraryRepository


def _repositories(database: SQLiteDatabase) -> StorageRepositories:
    return StorageRepositories(
        database=database,
        source_identities=SQLiteSourceIdentityRepository(database),
        track_overrides=SQLiteTrackOverrideRepository(database),
        settings=SQLiteSettingsRepository(database),
        lyrics=SQLiteLyricsRepository(database),
        lyrics_matches=SQLiteLyricsMatchRepository(database),
        provider_cache=SQLiteProviderCacheRepository(database),
        representations=SQLiteRepresentationRepository(database),
        timing_calibrations=SQLiteTimingCalibrationRepository(database),
        library=SQLiteLibraryRepository(database),
    )


def open_storage(path: Path | None = None) -> StorageRepositories:
    """Initialize current schema and construct replaceable repository adapters."""

    database = SQLiteDatabase(path or default_database_path())
    database.initialize()
    return _repositories(database)


def open_storage_readonly(path: Path | None = None) -> StorageRepositories:
    """Open existing current storage without creating or migrating anything."""

    resolved_path = path or default_database_path()
    status = inspect_storage(resolved_path)
    if status.migration_status != "current" or status.error is not None:
        raise UnsupportedSchemaError(
            "read-only repository access requires a healthy current schema; "
            "run `konokashi storage migrate` explicitly if desired",
            path=resolved_path,
        )
    return _repositories(SQLiteDatabase(resolved_path))
