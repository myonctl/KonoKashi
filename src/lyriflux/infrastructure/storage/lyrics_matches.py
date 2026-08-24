"""Durable lyrics match decisions kept separate from provider response cache."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from lyriflux.domain.identity import PersistenceScope, SourceIdentity
from lyriflux.domain.lyrics import (
    ContentProvenance,
    LyricsMatch,
    LyricsMatchConfidence,
    LyricsMatchDecision,
)
from lyriflux.infrastructure.storage.errors import (
    InvalidStoredDataError,
    StorageValidationError,
)
from lyriflux.infrastructure.storage.source_identities import identity_id
from lyriflux.infrastructure.storage.sqlite import SQLiteDatabase


class SQLiteLyricsMatchRepository:
    """Persist current match decisions and durable per-document rejections."""

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
            evidence_rows = connection.execute(
                """
                SELECT evidence FROM lyrics_match_evidence
                WHERE source_identity_id = ? ORDER BY position
                """,
                (source_id,),
            ).fetchall()
        if row is None:
            return None
        return self._typed_match(row, evidence_rows)

    def put(self, source_identity: SourceIdentity, match: LyricsMatch) -> None:
        """Atomically save or deliberately replace one match decision."""

        self._validate(source_identity, match)
        with self._database.transaction() as connection:
            source_id = identity_id(connection, source_identity, create=True)
            if source_id is None:
                raise InvalidStoredDataError("stable source identity was not stored")
            connection.execute(
                """
                INSERT INTO lyrics_matches(
                    source_identity_id, document_id, decision, provenance, updated_at,
                    match_confidence
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_identity_id) DO UPDATE SET
                    document_id = excluded.document_id,
                    decision = excluded.decision,
                    provenance = excluded.provenance,
                    updated_at = excluded.updated_at,
                    match_confidence = excluded.match_confidence
                """,
                (
                    source_id,
                    match.document_id,
                    match.decision.value,
                    match.provenance.value,
                    match.updated_at.astimezone(UTC).isoformat(),
                    match.confidence.value,
                ),
            )
            connection.execute(
                "DELETE FROM lyrics_match_evidence WHERE source_identity_id = ?",
                (source_id,),
            )
            connection.executemany(
                """
                INSERT INTO lyrics_match_evidence(
                    source_identity_id, position, evidence
                ) VALUES (?, ?, ?)
                """,
                (
                    (source_id, position, evidence)
                    for position, evidence in enumerate(match.evidence)
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

    def rejections(self, source_identity: SourceIdentity) -> tuple[LyricsMatch, ...]:
        """Return every rejected document for a recording in stable order."""

        with self._database.connection(readonly=True) as connection:
            source_id = identity_id(connection, source_identity, create=False)
            if source_id is None:
                return ()
            rows = connection.execute(
                """
                SELECT document_id, provenance, rejected_at AS updated_at,
                       match_confidence
                FROM lyrics_match_rejections
                WHERE source_identity_id = ?
                ORDER BY rejected_at, document_id
                """,
                (source_id,),
            ).fetchall()
            evidence_rows = connection.execute(
                """
                SELECT document_id, evidence
                FROM lyrics_match_rejection_evidence
                WHERE source_identity_id = ?
                ORDER BY document_id, position
                """,
                (source_id,),
            ).fetchall()
        evidence_by_document: dict[str, list[str]] = {}
        for evidence_row in evidence_rows:
            evidence_by_document.setdefault(
                str(evidence_row["document_id"]), []
            ).append(str(evidence_row["evidence"]))
        return tuple(
            self._typed_match(
                row,
                tuple(evidence_by_document.get(str(row["document_id"]), ())),
                decision=LyricsMatchDecision.REJECTED,
            )
            for row in rows
        )

    def put_rejection(
        self, source_identity: SourceIdentity, match: LyricsMatch
    ) -> None:
        """Atomically save one rejection without erasing other match history."""

        self._validate(source_identity, match)
        if match.decision is not LyricsMatchDecision.REJECTED:
            raise StorageValidationError("rejection history requires a rejected match")
        with self._database.transaction() as connection:
            source_id = identity_id(connection, source_identity, create=True)
            if source_id is None:
                raise InvalidStoredDataError("stable source identity was not stored")
            connection.execute(
                """
                INSERT INTO lyrics_match_rejections(
                    source_identity_id, document_id, provenance, rejected_at,
                    match_confidence
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_identity_id, document_id) DO UPDATE SET
                    provenance = excluded.provenance,
                    rejected_at = excluded.rejected_at,
                    match_confidence = excluded.match_confidence
                """,
                (
                    source_id,
                    match.document_id,
                    match.provenance.value,
                    match.updated_at.astimezone(UTC).isoformat(),
                    match.confidence.value,
                ),
            )
            connection.execute(
                """
                DELETE FROM lyrics_match_rejection_evidence
                WHERE source_identity_id = ? AND document_id = ?
                """,
                (source_id, match.document_id),
            )
            connection.executemany(
                """
                INSERT INTO lyrics_match_rejection_evidence(
                    source_identity_id, document_id, position, evidence
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    (source_id, match.document_id, position, evidence)
                    for position, evidence in enumerate(match.evidence)
                ),
            )

    def delete_rejection(
        self, source_identity: SourceIdentity, document_id: str
    ) -> bool:
        """Explicitly reverse one document rejection."""

        with self._database.transaction() as connection:
            source_id = identity_id(connection, source_identity, create=False)
            if source_id is None:
                return False
            cursor = connection.execute(
                """
                DELETE FROM lyrics_match_rejections
                WHERE source_identity_id = ? AND document_id = ?
                """,
                (source_id, document_id),
            )
            return cursor.rowcount > 0

    def clear_rejections(self, source_identity: SourceIdentity) -> int:
        """Reset every rejected-document preference for one recording."""

        with self._database.transaction() as connection:
            source_id = identity_id(connection, source_identity, create=False)
            if source_id is None:
                return 0
            cursor = connection.execute(
                """
                DELETE FROM lyrics_match_rejections
                WHERE source_identity_id = ?
                """,
                (source_id,),
            )
            return cursor.rowcount

    @staticmethod
    def _validate(source_identity: SourceIdentity, match: LyricsMatch) -> None:
        if source_identity.persistence_scope is not PersistenceScope.PERMANENT:
            raise StorageValidationError(
                "session-only generic identities cannot receive durable lyric matches"
            )
        if match.updated_at.tzinfo is None or match.updated_at.utcoffset() is None:
            raise StorageValidationError("match timestamp must be timezone-aware")

    @staticmethod
    def _typed_match(
        row: sqlite3.Row,
        evidence_rows: Sequence[sqlite3.Row | str],
        *,
        decision: LyricsMatchDecision | None = None,
    ) -> LyricsMatch:
        try:
            evidence = tuple(
                str(item["evidence"]) if not isinstance(item, str) else item
                for item in evidence_rows
            )
            return LyricsMatch(
                document_id=str(row["document_id"]),
                decision=(
                    decision
                    if decision is not None
                    else LyricsMatchDecision(str(row["decision"]))
                ),
                provenance=ContentProvenance(str(row["provenance"])),
                updated_at=datetime.fromisoformat(str(row["updated_at"])),
                confidence=LyricsMatchConfidence(str(row["match_confidence"])),
                evidence=evidence,
            )
        except (TypeError, ValueError) as error:
            raise InvalidStoredDataError("stored lyrics match is invalid") from error
