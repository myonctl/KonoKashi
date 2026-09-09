"""Release-gate upgrade from the last pre-packaging development schema."""

from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from konokashi.domain.lyrics import ApprovalState, ContentProvenance, RepresentationKind
from konokashi.domain.representations import GenerationStatus, RepresentationCandidate
from konokashi.infrastructure.storage.bootstrap import open_storage
from konokashi.infrastructure.storage.diagnostics import inspect_storage
from konokashi.infrastructure.storage.errors import StorageMigrationError
from konokashi.infrastructure.storage.migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
    Migration,
)
from konokashi.infrastructure.storage.sqlite import SQLiteDatabase

HISTORICAL_SCHEMA_10_FIXTURE = (
    Path(__file__).parent / "fixtures/storage/historical_schema_10.sqlite3"
)
HISTORICAL_MIGRATION_4_CHECKSUM = (
    "2e6f4476757ffdc21fa2d10a1ab1b203dbc7ab909210dca6c53f7d929662c25e"
)
HISTORICAL_SCHEMA_10_FIXTURE_SHA256 = (
    "a10857b1d203966afe9d641c112f059530bd283133a6923645ff8ebd2f41fa4d"
)


def test_schema_eight_upgrade_preserves_user_and_library_data(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "development-schema-8.sqlite3")
    database.initialize(MIGRATIONS[:8])
    timestamp = "2026-08-23T00:00:00+00:00"
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO source_identities(
                id, source_kind, persistence_scope, local_path, created_at
            ) VALUES (1, 'local-file', 'permanent', '/music/preserved.flac', ?)
            """,
            (timestamp,),
        )
        connection.execute(
            """
            INSERT INTO track_overrides(
                source_identity_id, approved_title, approved_album, provenance,
                created_at, updated_at
            ) VALUES (1, 'Preserved title', 'Preserved album', 'user-approved', ?, ?)
            """,
            (timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO track_override_artists(source_identity_id, position, artist)
            VALUES (1, 0, 'Preserved artist')
            """
        )
        connection.execute(
            """
            INSERT INTO lyrics_documents(
                document_id, document_kind, source_name, original_text,
                approval_state, retrieved_at
            ) VALUES ('preserved-doc', 'plain', 'fixture', '保留 original',
                      'approved', ?)
            """,
            (timestamp,),
        )
        connection.execute(
            """
            INSERT INTO lyric_representations(
                document_id, representation_id, representation_kind, provenance,
                approval_state, position
            ) VALUES ('preserved-doc', 'original', 'original', 'local',
                      'approved', 0)
            """
        )
        connection.execute(
            """
            INSERT INTO lyric_lines(
                document_id, representation_id, line_id, position, line_text
            ) VALUES ('preserved-doc', 'original', 'line-1', 0, '保留 original')
            """
        )
        connection.execute(
            """
            INSERT INTO lyric_document_timing(
                document_id, lyrics_display_delay_us, updated_at
            ) VALUES ('preserved-doc', 125000, ?)
            """,
            (timestamp,),
        )
        connection.execute(
            """
            INSERT INTO library_settings(
                settings_id, automatic_downloads, worker_count, updated_at
            ) VALUES (1, 0, 2, ?)
            """,
            (timestamp,),
        )
        connection.execute(
            """
            INSERT INTO library_roots(settings_id, position, root_path)
            VALUES (1, 0, '/music')
            """
        )

    assert database.initialize() == CURRENT_SCHEMA_VERSION

    with database.connection(readonly=True) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert (
            connection.execute("SELECT approved_title FROM track_overrides").fetchone()[
                0
            ]
            == "Preserved title"
        )
        assert (
            connection.execute(
                "SELECT line_text FROM lyric_lines WHERE line_id = 'line-1'"
            ).fetchone()[0]
            == "保留 original"
        )
        assert (
            connection.execute(
                "SELECT lyrics_display_delay_us FROM lyric_document_timing"
            ).fetchone()[0]
            == 125000
        )
        assert (
            connection.execute("SELECT root_path FROM library_roots").fetchone()[0]
            == "/music"
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM lyric_document_language_overrides"
            ).fetchone()[0]
            == 0
        )


def test_historical_schema_ten_upgrade_preserves_representative_data_and_restarts(
    tmp_path: Path,
) -> None:
    assert (
        hashlib.sha256(HISTORICAL_SCHEMA_10_FIXTURE.read_bytes()).hexdigest()
        == HISTORICAL_SCHEMA_10_FIXTURE_SHA256
    )
    path = tmp_path / "historical-schema-10.sqlite3"
    shutil.copyfile(HISTORICAL_SCHEMA_10_FIXTURE, path)
    database = SQLiteDatabase(path)

    before = inspect_storage(path)
    assert before.schema_version == 10
    assert before.migration_status == "pending"
    assert before.integrity_status == "ok"
    assert MIGRATIONS[3].checksum == HISTORICAL_MIGRATION_4_CHECKSUM
    assert database.initialize() == CURRENT_SCHEMA_VERSION

    after = inspect_storage(path)
    assert after.schema_version == CURRENT_SCHEMA_VERSION
    assert after.migration_status == "current"
    assert after.integrity_status == "ok"
    with database.connection(readonly=True) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert tuple(
            connection.execute(
                """
                SELECT source_identities.youtube_video_id,
                       track_overrides.approved_title,
                       track_override_artists.artist
                FROM source_identities
                JOIN track_overrides
                  ON track_overrides.source_identity_id = source_identities.id
                JOIN track_override_artists
                  ON track_override_artists.source_identity_id = source_identities.id
                """
            ).fetchone()
        ) == ("AbCdEfGhI12", "Synthetic Corrected Title", "Synthetic Artist")
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM lyric_lines WHERE document_id = 'historical-doc'"
            ).fetchone()[0]
            == 2
        )
        assert tuple(
            connection.execute(
                """
                SELECT lyrics_matches.decision, lyrics_matches.match_confidence,
                       lyrics_documents.provider_record_id
                FROM lyrics_matches
                JOIN lyrics_documents USING (document_id)
                WHERE lyrics_matches.source_identity_id = 1
                """
            ).fetchone()
        ) == ("approved", "Approved", "fixture-record-10")
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM lyric_representation_candidates"
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT decision_text FROM lyric_representation_decisions"
            ).fetchone()[0]
            == "approved synthetic translation"
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM provider_cache").fetchone()[0] == 1
        )
        assert tuple(
            connection.execute(
                "SELECT status, discovered, processed FROM library_scan_runs"
            ).fetchone()
        ) == ("completed", 1, 1)
        assert (
            connection.execute("SELECT COUNT(*) FROM library_tracks").fetchone()[0] == 1
        )

    repositories = open_storage(path)
    existing = repositories.representations.candidates("historical-doc")
    generated = next(
        item for item in existing if item.candidate_id == "generated-reading"
    )
    assert generated.text == "hoshi no tesuto"
    assert generated.diagnostics == ("synthetic generated fixture",)

    now = datetime.now(UTC)
    repositories.representations.put_candidates(
        (
            RepresentationCandidate(
                "empty",
                "historical-doc",
                "line-1",
                RepresentationKind.ROMANIZED,
                GenerationStatus.EMPTY,
                ContentProvenance.GENERATED,
                "fixture",
                "2",
                ApprovalState.UNREVIEWED,
                generated.uncertainty,
                now,
                now,
                diagnostics=("engine generated empty text",),
            ),
        )
    )
    assert {
        candidate.status
        for candidate in repositories.representations.candidates("historical-doc")
    } == {GenerationStatus.AVAILABLE, GenerationStatus.EMPTY}

    reopened = SQLiteDatabase(path)
    assert reopened.initialize() == CURRENT_SCHEMA_VERSION
    assert inspect_storage(path).migration_status == "current"
    with reopened.connection(readonly=True) as connection:
        assert (
            connection.execute(
                """
                SELECT COUNT(*) FROM lyrics_documents
                WHERE document_id = 'historical-doc'
                """
            ).fetchone()[0]
            == 1
        )


def test_interrupted_historical_schema_ten_upgrade_rolls_back_atomically(
    tmp_path: Path,
) -> None:
    path = tmp_path / "interrupted-historical-schema-10.sqlite3"
    shutil.copyfile(HISTORICAL_SCHEMA_10_FIXTURE, path)
    database = SQLiteDatabase(path)
    broken_migration = Migration(
        11,
        "simulated interrupted migration",
        (
            "CREATE TABLE migration_interruption_probe(value INTEGER)",
            "INSERT INTO missing_interruption_target VALUES (1)",
        ),
    )

    with pytest.raises(StorageMigrationError, match="migration 11"):
        database.initialize((*MIGRATIONS[:10], broken_migration))

    assert database.migration_history()[-1][0] == 10
    with database.connection(readonly=True) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert (
            connection.execute(
                """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table' AND name = 'migration_interruption_probe'
            """
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                """
                SELECT COUNT(*) FROM lyrics_documents
                WHERE document_id = 'historical-doc'
                """
            ).fetchone()[0]
            == 1
        )

    assert database.initialize() == CURRENT_SCHEMA_VERSION
