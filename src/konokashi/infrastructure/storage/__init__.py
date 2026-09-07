"""Local SQLite persistence adapters; application/domain code stays SQL-free."""

from konokashi.infrastructure.storage.lyrics import SQLiteLyricsRepository
from konokashi.infrastructure.storage.lyrics_matches import (
    SQLiteLyricsMatchRepository,
)
from konokashi.infrastructure.storage.provider_cache import (
    SQLiteProviderCacheRepository,
)
from konokashi.infrastructure.storage.settings import SQLiteSettingsRepository
from konokashi.infrastructure.storage.source_identities import (
    SQLiteSourceIdentityRepository,
)
from konokashi.infrastructure.storage.sqlite import SQLiteDatabase
from konokashi.infrastructure.storage.sqlite_track_overrides import (
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
