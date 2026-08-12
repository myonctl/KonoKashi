"""Durable lyrics match decisions kept separate from provider response cache."""

from __future__ import annotations

from datetime import UTC, datetime

from lyricflow.domain.identity import PersistenceScope, SourceIdentity
from lyricflow.domain.lyrics import ContentProvenance, LyricsMatch, LyricsMatchDecision
from lyricflow.infrastructure.storage.errors import (
    InvalidStoredDataError,
    StorageValidationError,
)
from lyricflow.infrastructure.storage.source_identities import identity_id
from lyricflow.infrastructure.storage.sqlite import SQLiteDatabase


class SQLiteLyricsMatchRepository:
    """Persist the current approved/rejected/candidate decision per recording."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def get(self, source_identity: SourceIdentity) -> LyricsMatch | None:
        """Return one typed match decision."""

        with self._database.connection(readonly=True) as connection:
            source_id = identity_id(connection, source_identity, create=False)
            if source_id is None:
                return None
            row = connection.execute(
                "SELECT * FROM lyrics_matches WHERE source_identity_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return LyricsMatch(
                document_id=str(row["document_id"]),
                decision=LyricsMatchDecision(str(row["decision"])),
                provenance=ContentProvenance(str(row["provenance"])),
                updated_at=datetime.fromisoformat(str(row["updated_at"])),
            )
        except (TypeError, ValueError) as error:
            raise InvalidStoredDataError("stored lyrics match is invalid") from error

    def put(self, source_identity: SourceIdentity, match: LyricsMatch) -> None:
        """Atomically save or deliberately replace one match decision."""

        if source_identity.persistence_scope is not PersistenceScope.PERMANENT:
            raise StorageValidationError(
                "session-only generic identities cannot receive durable lyric matches"
            )
        if match.updated_at.tzinfo is None or match.updated_at.utcoffset() is None:
            raise StorageValidationError("match timestamp must be timezone-aware")
        with self._database.transaction() as connection:
            source_id = identity_id(connection, source_identity, create=True)
            if source_id is None:
                raise InvalidStoredDataError("stable source identity was not stored")
            connection.execute(
                """
                INSERT INTO lyrics_matches(
                    source_identity_id, document_id, decision, provenance, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_identity_id) DO UPDATE SET
                    document_id = excluded.document_id,
                    decision = excluded.decision,
                    provenance = excluded.provenance,
                    updated_at = excluded.updated_at
                """,
                (
                    source_id,
                    match.document_id,
                    match.decision.value,
                    match.provenance.value,
                    match.updated_at.astimezone(UTC).isoformat(),
                ),
            )

    def delete(self, source_identity: SourceIdentity) -> bool:
        """Explicitly reset one match decision."""

        with self._database.transaction() as connection:
            source_id = identity_id(connection, source_identity, create=False)
            if source_id is None:
                return False
            cursor = connection.execute(
                "DELETE FROM lyrics_matches WHERE source_identity_id = ?", (source_id,)
            )
            return cursor.rowcount > 0
