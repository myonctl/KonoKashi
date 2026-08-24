"""Structured SQLite persistence for Stage 2 source identities."""

from __future__ import annotations

import sqlite3
from typing import cast

from lyriflux.domain.identity import (
    GenericMprisIdentity,
    LocalFileIdentity,
    PersistenceScope,
    SourceIdentity,
    SourceKind,
    YouTubeIdentity,
)
from lyriflux.infrastructure.storage.errors import InvalidStoredDataError
from lyriflux.infrastructure.storage.sqlite import SQLiteDatabase, utc_now_text


def _find_row(
    connection: sqlite3.Connection, source_identity: SourceIdentity
) -> sqlite3.Row | None:
    if isinstance(source_identity, LocalFileIdentity):
        return cast(
            sqlite3.Row | None,
            connection.execute(
                """SELECT * FROM source_identities
               WHERE source_kind = ? AND local_path = ?""",
                (SourceKind.LOCAL_FILE.value, source_identity.canonical_path),
            ).fetchone(),
        )
    if isinstance(source_identity, YouTubeIdentity):
        return cast(
            sqlite3.Row | None,
            connection.execute(
                """SELECT * FROM source_identities
               WHERE source_kind = ? AND youtube_video_id = ?""",
                (SourceKind.YOUTUBE.value, source_identity.video_id),
            ).fetchone(),
        )
    return cast(
        sqlite3.Row | None,
        connection.execute(
            """
        SELECT * FROM source_identities
        WHERE source_kind = ?
          AND generic_service_name = ?
          AND generic_track_id IS ?
          AND generic_media_url IS ?
        """,
            (
                SourceKind.GENERIC_MPRIS.value,
                source_identity.service_name,
                source_identity.track_id,
                source_identity.media_url,
            ),
        ).fetchone(),
    )


def _row_to_identity(row: sqlite3.Row) -> SourceIdentity:
    try:
        kind = SourceKind(str(row["source_kind"]))
        scope = PersistenceScope(str(row["persistence_scope"]))
        if kind is SourceKind.LOCAL_FILE and scope is PersistenceScope.PERMANENT:
            return LocalFileIdentity(str(row["local_path"]))
        if kind is SourceKind.YOUTUBE and scope is PersistenceScope.PERMANENT:
            return YouTubeIdentity(str(row["youtube_video_id"]))
        if kind is SourceKind.GENERIC_MPRIS and scope is PersistenceScope.SESSION_ONLY:
            track_id = row["generic_track_id"]
            media_url = row["generic_media_url"]
            return GenericMprisIdentity(
                str(row["generic_service_name"]),
                None if track_id is None else str(track_id),
                None if media_url is None else str(media_url),
            )
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidStoredDataError("stored source identity is invalid") from error
    raise InvalidStoredDataError("stored source identity kind/scope is inconsistent")


def identity_id(
    connection: sqlite3.Connection,
    source_identity: SourceIdentity,
    *,
    create: bool,
) -> int | None:
    """Find or insert one structured source identity inside a caller transaction."""

    row = _find_row(connection, source_identity)
    if row is not None:
        return int(row["id"])
    if not create:
        return None
    values: tuple[object, ...]
    if isinstance(source_identity, LocalFileIdentity):
        values = (
            source_identity.kind.value,
            source_identity.persistence_scope.value,
            source_identity.canonical_path,
            None,
            None,
            None,
            None,
            utc_now_text(),
        )
    elif isinstance(source_identity, YouTubeIdentity):
        values = (
            source_identity.kind.value,
            source_identity.persistence_scope.value,
            None,
            source_identity.video_id,
            None,
            None,
            None,
            utc_now_text(),
        )
    else:
        values = (
            source_identity.kind.value,
            source_identity.persistence_scope.value,
            None,
            None,
            source_identity.service_name,
            source_identity.track_id,
            source_identity.media_url,
            utc_now_text(),
        )
    connection.execute(
        """
        INSERT INTO source_identities(
            source_kind, persistence_scope, local_path, youtube_video_id,
            generic_service_name, generic_track_id, generic_media_url, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        values,
    )
    row = _find_row(connection, source_identity)
    if row is None:
        raise InvalidStoredDataError("source identity could not be stored")
    return int(row["id"])


class SQLiteSourceIdentityRepository:
    """Round-trip typed identities without flattening their source semantics."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def put(self, source_identity: SourceIdentity) -> SourceIdentity:
        """Store an identity idempotently."""

        with self._database.transaction() as connection:
            identity_id(connection, source_identity, create=True)
            row = _find_row(connection, source_identity)
            if row is None:
                raise InvalidStoredDataError("source identity disappeared after insert")
            return _row_to_identity(row)

    def get(self, source_identity: SourceIdentity) -> SourceIdentity | None:
        """Return an exactly reconstructed identity."""

        with self._database.connection(readonly=True) as connection:
            row = _find_row(connection, source_identity)
            return None if row is None else _row_to_identity(row)
