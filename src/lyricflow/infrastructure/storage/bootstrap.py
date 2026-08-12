"""Explicit construction of SQLite-backed repositories without global state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lyricflow.infrastructure.storage.lyrics import SQLiteLyricsRepository
from lyricflow.infrastructure.storage.lyrics_matches import (
    SQLiteLyricsMatchRepository,
)
from lyricflow.infrastructure.storage.paths import default_database_path
from lyricflow.infrastructure.storage.provider_cache import (
    SQLiteProviderCacheRepository,
)
from lyricflow.infrastructure.storage.settings import SQLiteSettingsRepository
from lyricflow.infrastructure.storage.source_identities import (
    SQLiteSourceIdentityRepository,
)
from lyricflow.infrastructure.storage.sqlite import SQLiteDatabase
from lyricflow.infrastructure.storage.sqlite_track_overrides import (
    SQLiteTrackOverrideRepository,
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


def open_storage(path: Path | None = None) -> StorageRepositories:
    """Initialize current schema and construct replaceable repository adapters."""

    database = SQLiteDatabase(path or default_database_path())
    database.initialize()
    return StorageRepositories(
        database=database,
        source_identities=SQLiteSourceIdentityRepository(database),
        track_overrides=SQLiteTrackOverrideRepository(database),
        settings=SQLiteSettingsRepository(database),
        lyrics=SQLiteLyricsRepository(database),
        lyrics_matches=SQLiteLyricsMatchRepository(database),
        provider_cache=SQLiteProviderCacheRepository(database),
    )
