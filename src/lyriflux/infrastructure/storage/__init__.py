"""Local SQLite persistence adapters; application/domain code stays SQL-free."""

from lyriflux.infrastructure.storage.lyrics import SQLiteLyricsRepository
from lyriflux.infrastructure.storage.lyrics_matches import (
    SQLiteLyricsMatchRepository,
)
from lyriflux.infrastructure.storage.provider_cache import (
    SQLiteProviderCacheRepository,
)
from lyriflux.infrastructure.storage.settings import SQLiteSettingsRepository
from lyriflux.infrastructure.storage.source_identities import (
    SQLiteSourceIdentityRepository,
)
from lyriflux.infrastructure.storage.sqlite import SQLiteDatabase
from lyriflux.infrastructure.storage.sqlite_track_overrides import (
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
