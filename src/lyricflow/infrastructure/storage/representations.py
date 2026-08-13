"""SQLite persistence for Stage 5 candidates and line-level user decisions."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from lyricflow.domain.lyrics import ApprovalState, ContentProvenance, RepresentationKind
from lyricflow.domain.representations import (
    GenerationStatus,
    RepresentationCandidate,
    RepresentationDecision,
    RepresentationUncertainty,
)
from lyricflow.infrastructure.storage.errors import (
    InvalidStoredDataError,
    StorageValidationError,
)
from lyricflow.infrastructure.storage.sqlite import SQLiteDatabase


def _datetime_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StorageValidationError("representation timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _validate_kind(kind: RepresentationKind) -> None:
    if kind is RepresentationKind.ORIGINAL:
        raise StorageValidationError("original lines cannot be alternate candidates")


def _ensure_original_line(
    connection: sqlite3.Connection, document_id: str, source_line_id: str
) -> None:
    row = connection.execute(
        """
        SELECT 1
        FROM lyric_lines AS line
        JOIN lyric_representations AS representation
          ON representation.document_id = line.document_id
         AND representation.representation_id = line.representation_id
        WHERE line.document_id = ? AND line.line_id = ?
          AND representation.representation_kind = 'original'
        """,
        (document_id, source_line_id),
    ).fetchone()
    if row is None:
        raise StorageValidationError(
            "representation candidate references an unknown original line"
        )


def _validate_candidate(candidate: RepresentationCandidate) -> None:
    if (
        not candidate.candidate_id
        or not candidate.document_id
        or not candidate.source_line_id
    ):
        raise StorageValidationError("representation candidate IDs must not be blank")
    _validate_kind(candidate.kind)
    if not candidate.source_name.strip():
        raise StorageValidationError("representation source name must not be blank")
    if candidate.status is GenerationStatus.AVAILABLE and candidate.text is None:
        raise StorageValidationError("available representation text must be present")
    _datetime_text(candidate.created_at)
    _datetime_text(candidate.updated_at)


def _write_candidate(
    connection: sqlite3.Connection, candidate: RepresentationCandidate
) -> None:
    _validate_candidate(candidate)
    _ensure_original_line(connection, candidate.document_id, candidate.source_line_id)
    connection.execute(
        """
        INSERT INTO lyric_representation_candidates(
            candidate_id, document_id, source_line_id, representation_kind,
            candidate_status, candidate_text, language, script, provenance,
            source_name, source_version, approval_state, uncertainty,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(candidate_id) DO UPDATE SET
            document_id = excluded.document_id,
            source_line_id = excluded.source_line_id,
            representation_kind = excluded.representation_kind,
            candidate_status = excluded.candidate_status,
            candidate_text = excluded.candidate_text,
            language = excluded.language,
            script = excluded.script,
            provenance = excluded.provenance,
            source_name = excluded.source_name,
            source_version = excluded.source_version,
            approval_state = excluded.approval_state,
            uncertainty = excluded.uncertainty,
            updated_at = excluded.updated_at
        """,
        (
            candidate.candidate_id,
            candidate.document_id,
            candidate.source_line_id,
            candidate.kind.value,
            candidate.status.value,
            candidate.text,
            candidate.language,
            candidate.script,
            candidate.provenance.value,
            candidate.source_name,
            candidate.source_version,
            candidate.approval_state.value,
            candidate.uncertainty.value,
            _datetime_text(candidate.created_at),
            _datetime_text(candidate.updated_at),
        ),
    )
    connection.execute(
        "DELETE FROM lyric_representation_candidate_diagnostics WHERE candidate_id = ?",
        (candidate.candidate_id,),
    )
    connection.executemany(
        """
        INSERT INTO lyric_representation_candidate_diagnostics(
            candidate_id, position, diagnostic
        ) VALUES (?, ?, ?)
        """,
        (
            (candidate.candidate_id, position, diagnostic)
            for position, diagnostic in enumerate(candidate.diagnostics)
        ),
    )


class SQLiteRepresentationRepository:
    """Keep derived evidence separate from canonical original representation writes."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def candidates(self, document_id: str) -> tuple[RepresentationCandidate, ...]:
        """Reconstruct retained candidates and ordered diagnostics."""

        with self._database.connection(readonly=True) as connection:
            rows = connection.execute(
                """
                SELECT * FROM lyric_representation_candidates
                WHERE document_id = ?
                ORDER BY source_line_id, representation_kind, provenance,
                         source_name, candidate_id
                """,
                (document_id,),
            ).fetchall()
            diagnostic_rows = connection.execute(
                """
                SELECT diagnostic.candidate_id, diagnostic.diagnostic
                FROM lyric_representation_candidate_diagnostics AS diagnostic
                JOIN lyric_representation_candidates AS candidate
                  ON candidate.candidate_id = diagnostic.candidate_id
                WHERE candidate.document_id = ?
                ORDER BY diagnostic.candidate_id, diagnostic.position
                """,
                (document_id,),
            ).fetchall()
        diagnostics: dict[str, list[str]] = {}
        for row in diagnostic_rows:
            diagnostics.setdefault(str(row["candidate_id"]), []).append(
                str(row["diagnostic"])
            )
        try:
            return tuple(
                RepresentationCandidate(
                    candidate_id=str(row["candidate_id"]),
                    document_id=str(row["document_id"]),
                    source_line_id=str(row["source_line_id"]),
                    kind=RepresentationKind(str(row["representation_kind"])),
                    status=GenerationStatus(str(row["candidate_status"])),
                    provenance=ContentProvenance(str(row["provenance"])),
                    source_name=str(row["source_name"]),
                    source_version=(
                        None
                        if row["source_version"] is None
                        else str(row["source_version"])
                    ),
                    approval_state=ApprovalState(str(row["approval_state"])),
                    uncertainty=RepresentationUncertainty(str(row["uncertainty"])),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                    updated_at=datetime.fromisoformat(str(row["updated_at"])),
                    text=(
                        None
                        if row["candidate_text"] is None
                        else str(row["candidate_text"])
                    ),
                    language=None if row["language"] is None else str(row["language"]),
                    script=None if row["script"] is None else str(row["script"]),
                    diagnostics=tuple(diagnostics.get(str(row["candidate_id"]), [])),
                )
                for row in rows
            )
        except (TypeError, ValueError) as error:
            raise InvalidStoredDataError(
                "stored representation candidate is invalid"
            ) from error

    def decisions(self, document_id: str) -> tuple[RepresentationDecision, ...]:
        """Reconstruct user decisions without treating them as provider content."""

        with self._database.connection(readonly=True) as connection:
            rows = connection.execute(
                """
                SELECT * FROM lyric_representation_decisions
                WHERE document_id = ?
                ORDER BY source_line_id, representation_kind
                """,
                (document_id,),
            ).fetchall()
        try:
            return tuple(
                RepresentationDecision(
                    document_id=str(row["document_id"]),
                    source_line_id=str(row["source_line_id"]),
                    kind=RepresentationKind(str(row["representation_kind"])),
                    approval_state=ApprovalState(str(row["approval_state"])),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                    updated_at=datetime.fromisoformat(str(row["updated_at"])),
                    text=None
                    if row["decision_text"] is None
                    else str(row["decision_text"]),
                    based_on_candidate_id=(
                        None
                        if row["based_on_candidate_id"] is None
                        else str(row["based_on_candidate_id"])
                    ),
                )
                for row in rows
            )
        except (TypeError, ValueError) as error:
            raise InvalidStoredDataError(
                "stored representation decision is invalid"
            ) from error

    def put_candidates(self, candidates: tuple[RepresentationCandidate, ...]) -> None:
        """Atomically insert/update provider or imported explicitly aligned values."""

        if not candidates:
            return
        with self._database.transaction() as connection:
            for candidate in candidates:
                _write_candidate(connection, candidate)

    def replace_generated(
        self,
        document_id: str,
        source_line_id: str,
        kind: RepresentationKind,
        candidate: RepresentationCandidate,
    ) -> None:
        """Replace only generated evidence for the exact line and kind."""

        _validate_kind(kind)
        if (
            candidate.document_id != document_id
            or candidate.source_line_id != source_line_id
            or candidate.kind is not kind
            or candidate.provenance is not ContentProvenance.GENERATED
        ):
            raise StorageValidationError("replacement candidate scope does not match")
        with self._database.transaction() as connection:
            connection.execute(
                """
                DELETE FROM lyric_representation_candidates
                WHERE document_id = ? AND source_line_id = ?
                  AND representation_kind = ? AND provenance = 'generated'
                """,
                (document_id, source_line_id, kind.value),
            )
            _write_candidate(connection, candidate)

    def put_decision(self, decision: RepresentationDecision) -> None:
        """Save one exact line decision without touching neighboring lines."""

        _validate_kind(decision.kind)
        if decision.approval_state is not ApprovalState.REJECTED and not (
            decision.text and decision.text.strip()
        ):
            raise StorageValidationError(
                "non-rejected representation decision needs text"
            )
        _datetime_text(decision.created_at)
        _datetime_text(decision.updated_at)
        with self._database.transaction() as connection:
            _ensure_original_line(
                connection, decision.document_id, decision.source_line_id
            )
            connection.execute(
                """
                INSERT INTO lyric_representation_decisions(
                    document_id, source_line_id, representation_kind,
                    approval_state, decision_text, based_on_candidate_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id, source_line_id, representation_kind)
                DO UPDATE SET
                    approval_state = excluded.approval_state,
                    decision_text = excluded.decision_text,
                    based_on_candidate_id = excluded.based_on_candidate_id,
                    updated_at = excluded.updated_at
                """,
                (
                    decision.document_id,
                    decision.source_line_id,
                    decision.kind.value,
                    decision.approval_state.value,
                    decision.text,
                    decision.based_on_candidate_id,
                    _datetime_text(decision.created_at),
                    _datetime_text(decision.updated_at),
                ),
            )

    def delete_decision(
        self, document_id: str, source_line_id: str, kind: RepresentationKind
    ) -> bool:
        """Reset only one selected override/rejection."""

        _validate_kind(kind)
        with self._database.transaction() as connection:
            cursor = connection.execute(
                """
                DELETE FROM lyric_representation_decisions
                WHERE document_id = ? AND source_line_id = ?
                  AND representation_kind = ?
                """,
                (document_id, source_line_id, kind.value),
            )
            return cursor.rowcount > 0
