"""Durable user-approved track corrections behind the Stage 2 port."""

from __future__ import annotations

from lyricflow.domain.identity import PersistenceScope, SourceIdentity
from lyricflow.domain.tracks import ApprovedTrackIdentity
from lyricflow.infrastructure.storage.errors import (
    InvalidStoredDataError,
    StorageValidationError,
)
from lyricflow.infrastructure.storage.source_identities import identity_id
from lyricflow.infrastructure.storage.sqlite import SQLiteDatabase, utc_now_text


def _validate(approved_identity: ApprovedTrackIdentity) -> None:
    if not approved_identity.title.strip():
        raise StorageValidationError("approved title must not be blank")
    if not approved_identity.artists or any(
        not artist.strip() for artist in approved_identity.artists
    ):
        raise StorageValidationError(
            "at least one non-blank approved artist is required"
        )


class SQLiteTrackOverrideRepository:
    """Persist deliberate corrections only for approval-safe stable identities."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def get(self, source_identity: SourceIdentity) -> ApprovedTrackIdentity | None:
        """Return one approved correction without changing raw MPRIS evidence."""

        with self._database.connection(readonly=True) as connection:
            source_id = identity_id(connection, source_identity, create=False)
            if source_id is None:
                return None
            row = connection.execute(
                """
                SELECT approved_title, approved_album, provenance
                FROM track_overrides WHERE source_identity_id = ?
                """,
                (source_id,),
            ).fetchone()
            if row is None:
                return None
            if row["provenance"] != "user-approved":
                raise InvalidStoredDataError("track correction provenance is invalid")
            artists = connection.execute(
                """
                SELECT artist FROM track_override_artists
                WHERE source_identity_id = ? ORDER BY position
                """,
                (source_id,),
            ).fetchall()
            if not artists:
                raise InvalidStoredDataError("track correction has no approved artists")
            album = row["approved_album"]
            return ApprovedTrackIdentity(
                str(row["approved_title"]),
                tuple(str(item["artist"]) for item in artists),
                None if album is None else str(album),
            )

    def put(
        self,
        source_identity: SourceIdentity,
        approved_identity: ApprovedTrackIdentity,
    ) -> None:
        """Atomically create or deliberately replace one approved correction."""

        if source_identity.persistence_scope is not PersistenceScope.PERMANENT:
            raise StorageValidationError(
                "session-only generic identities cannot receive durable corrections"
            )
        _validate(approved_identity)
        now = utc_now_text()
        with self._database.transaction() as connection:
            source_id = identity_id(connection, source_identity, create=True)
            if source_id is None:
                raise InvalidStoredDataError("stable source identity was not stored")
            connection.execute(
                """
                INSERT INTO track_overrides(
                    source_identity_id, approved_title, approved_album, provenance,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'user-approved', ?, ?)
                ON CONFLICT(source_identity_id) DO UPDATE SET
                    approved_title = excluded.approved_title,
                    approved_album = excluded.approved_album,
                    provenance = excluded.provenance,
                    updated_at = excluded.updated_at
                """,
                (
                    source_id,
                    approved_identity.title,
                    approved_identity.album,
                    now,
                    now,
                ),
            )
            connection.execute(
                "DELETE FROM track_override_artists WHERE source_identity_id = ?",
                (source_id,),
            )
            connection.executemany(
                """
                INSERT INTO track_override_artists(source_identity_id, position, artist)
                VALUES (?, ?, ?)
                """,
                (
                    (source_id, position, artist)
                    for position, artist in enumerate(approved_identity.artists)
                ),
            )

    def delete(self, source_identity: SourceIdentity) -> bool:
        """Explicitly reset one correction without deleting the source identity."""

        with self._database.transaction() as connection:
            source_id = identity_id(connection, source_identity, create=False)
            if source_id is None:
                return False
            cursor = connection.execute(
                "DELETE FROM track_overrides WHERE source_identity_id = ?",
                (source_id,),
            )
            return cursor.rowcount > 0
