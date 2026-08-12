"""Local SQLite persistence adapters; application/domain code stays SQL-free."""

from lyricflow.infrastructure.storage.lyrics import SQLiteLyricsRepository
from lyricflow.infrastructure.storage.lyrics_matches import (
    SQLiteLyricsMatchRepository,
)
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

__all__ = [
    "SQLiteDatabase",
    "SQLiteLyricsMatchRepository",
    "SQLiteLyricsRepository",
    "SQLiteProviderCacheRepository",
    "SQLiteSettingsRepository",
    "SQLiteSourceIdentityRepository",
    "SQLiteTrackOverrideRepository",
]
