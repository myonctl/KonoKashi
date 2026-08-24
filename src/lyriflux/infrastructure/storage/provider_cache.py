"""Optional raw provider cache that never implies user approval."""

from __future__ import annotations

from datetime import UTC, datetime

from lyriflux.domain.lyrics import ProviderCacheEntry
from lyriflux.infrastructure.storage.errors import (
    InvalidStoredDataError,
    StorageValidationError,
)
from lyriflux.infrastructure.storage.sqlite import SQLiteDatabase


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StorageValidationError("provider cache timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


class SQLiteProviderCacheRepository:
    """Store bounded provider responses independently from match decisions."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def get(self, provider: str, cache_key: str) -> ProviderCacheEntry | None:
        """Return one typed cache entry."""

        with self._database.connection(readonly=True) as connection:
            row = connection.execute(
                """SELECT * FROM provider_cache
                   WHERE provider = ? AND cache_key = ?""",
                (provider, cache_key),
            ).fetchone()
        if row is None:
            return None
        try:
            expires_at = row["expires_at"]
            return ProviderCacheEntry(
                provider=str(row["provider"]),
                cache_key=str(row["cache_key"]),
                payload=bytes(row["payload"]),
                retrieved_at=datetime.fromisoformat(str(row["retrieved_at"])),
                expires_at=(
                    None
                    if expires_at is None
                    else datetime.fromisoformat(str(expires_at))
                ),
            )
        except (TypeError, ValueError) as error:
            raise InvalidStoredDataError(
                "stored provider cache entry is invalid"
            ) from error

    def put(self, entry: ProviderCacheEntry) -> None:
        """Atomically insert or replace one provider response."""

        if not entry.provider or not entry.cache_key:
            raise StorageValidationError("provider and cache key must not be blank")
        expires = None if entry.expires_at is None else _timestamp(entry.expires_at)
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO provider_cache(
                    provider, cache_key, payload, retrieved_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider, cache_key) DO UPDATE SET
                    payload = excluded.payload,
                    retrieved_at = excluded.retrieved_at,
                    expires_at = excluded.expires_at
                """,
                (
                    entry.provider,
                    entry.cache_key,
                    entry.payload,
                    _timestamp(entry.retrieved_at),
                    expires,
                ),
            )

    def delete(self, provider: str, cache_key: str) -> bool:
        """Remove only one optional cache response."""

        with self._database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM provider_cache WHERE provider = ? AND cache_key = ?",
                (provider, cache_key),
            )
            return cursor.rowcount > 0
