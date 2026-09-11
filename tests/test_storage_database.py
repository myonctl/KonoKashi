"""SQLite path, migration, transaction, and controlled-failure tests."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from konokashi.infrastructure.storage.bootstrap import open_storage_readonly
from konokashi.infrastructure.storage.errors import (
    StorageCorruptError,
    StorageError,
    StorageLockedError,
    StorageMigrationError,
    StoragePathError,
    UnsupportedSchemaError,
)
from konokashi.infrastructure.storage.migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
    Migration,
)
from konokashi.infrastructure.storage.paths import default_database_path
from konokashi.infrastructure.storage.sqlite import SQLiteDatabase


def test_default_database_path_uses_absolute_xdg_data_home_without_writes(
    tmp_path: Path,
) -> None:
    xdg = tmp_path / "Données utilisateur"

    path = default_database_path(
        environment={"XDG_DATA_HOME": str(xdg)}, home=tmp_path / "home"
    )

    assert path == xdg / "konokashi" / "konokashi.sqlite3"
    assert not path.exists()


def test_relative_xdg_data_home_is_ignored(tmp_path: Path) -> None:
    path = default_database_path(
        environment={"XDG_DATA_HOME": "relative"}, home=tmp_path
    )

    assert path == tmp_path / ".local/share/konokashi/konokashi.sqlite3"


def test_brand_new_unicode_database_migrates_in_order_and_reopens_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "Unicode space 日本語" / "lyrics data.sqlite3"
    database = SQLiteDatabase(path)

    previous_umask = os.umask(0)
    try:
        assert database.initialize() == CURRENT_SCHEMA_VERSION
    finally:
        os.umask(previous_umask)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with database.transaction() as connection:
        connection.execute("CREATE TABLE private_mode_probe(value INTEGER)")
        assert stat.S_IMODE(Path(f"{path}-journal").stat().st_mode) == 0o600
    first_history = database.migration_history()
    assert [item[0] for item in first_history] == list(
        range(1, CURRENT_SCHEMA_VERSION + 1)
    )
    path.chmod(0o644)

    def unexpected_transaction() -> None:
        raise AssertionError("current-schema initialization opened a write transaction")

    monkeypatch.setattr(database, "transaction", unexpected_transaction)
    assert database.initialize() == CURRENT_SCHEMA_VERSION
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert database.migration_history() == first_history


def test_readonly_repository_open_never_upgrades_a_pending_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pending.sqlite3"
    database = SQLiteDatabase(path)
    database.initialize(MIGRATIONS[:4])
    history = database.migration_history()

    with pytest.raises(UnsupportedSchemaError, match="read-only"):
        open_storage_readonly(path)

    assert database.migration_history() == history
    assert [item[0] for item in history] == [1, 2, 3, 4]
    with database.connection(readonly=True) as connection:
        timing_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'lyric_document_timing'"
        ).fetchone()
    assert timing_table is None


def test_readonly_repository_open_uses_current_schema_without_writes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "current.sqlite3"
    database = SQLiteDatabase(path)
    database.initialize()
    history = database.migration_history()

    repositories = open_storage_readonly(path)

    assert repositories.settings.get_player_selection().preferred_players == ()
    assert database.migration_history() == history


def test_published_stage_three_database_upgrades_without_losing_approved_match(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "stage-three.sqlite3")
    database.initialize(MIGRATIONS[:2])
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO source_identities(
                source_kind, persistence_scope, local_path, youtube_video_id,
                generic_service_name, generic_track_id, generic_media_url,
                created_at
            ) VALUES ('youtube', 'permanent', NULL, 'xa4WrgqI7q0', NULL, NULL,
                      NULL, '2026-08-12T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO lyrics_documents(
                document_id, document_kind, source_name, original_text,
                raw_text_checksum, provider_record_id, language, script,
                duration_ms, approval_state, retrieved_at
            ) VALUES ('approved-doc', 'plain', 'local', 'line', 'checksum',
                      NULL, NULL, NULL, NULL, 'approved',
                      '2026-08-12T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO lyrics_matches(
                source_identity_id, document_id, decision, provenance, updated_at
            ) VALUES (1, 'approved-doc', 'approved', 'user',
                      '2026-08-12T00:00:00+00:00')
            """
        )

    assert database.initialize() == CURRENT_SCHEMA_VERSION

    with database.connection(readonly=True) as connection:
        row = connection.execute(
            "SELECT document_id, decision, match_confidence FROM lyrics_matches"
        ).fetchone()
        evidence_table = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'lyrics_match_evidence'
            """
        ).fetchone()
    assert tuple(row) == ("approved-doc", "approved", "Approved")
    assert evidence_table[0] == "lyrics_match_evidence"
    assert [item[0] for item in database.migration_history()] == list(
        range(1, CURRENT_SCHEMA_VERSION + 1)
    )


def test_published_stage_four_database_upgrades_without_losing_original_lines(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "stage-four.sqlite3")
    database.initialize(MIGRATIONS[:3])
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO lyrics_documents(
                document_id, document_kind, source_name, original_text,
                raw_text_checksum, language, script, approval_state, retrieved_at
            ) VALUES ('doc', 'synced', 'fixture', '君の声', 'checksum', 'ja',
                      'Jpan', 'unreviewed', '2026-08-13T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO lyric_representations(
                document_id, representation_id, representation_kind, language,
                script, provenance, approval_state, position
            ) VALUES ('doc', 'original', 'original', 'ja', 'Jpan', 'provider',
                      'unreviewed', 0)
            """
        )
        connection.execute(
            """
            INSERT INTO lyric_lines(
                document_id, representation_id, line_id, position, line_text,
                start_ms, timing_provenance
            ) VALUES ('doc', 'original', 'line-0042', 0, '君の声', 91820,
                      'provider')
            """
        )

    assert database.initialize() == CURRENT_SCHEMA_VERSION

    with database.connection(readonly=True) as connection:
        original = connection.execute(
            "SELECT line_id, line_text, start_ms FROM lyric_lines"
        ).fetchone()
        stage5_tables = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name LIKE 'lyric_representation_%'
                """
            )
        }
    assert tuple(original) == ("line-0042", "君の声", 91_820)
    assert "lyric_representation_candidates" in stage5_tables
    assert "lyric_representation_decisions" in stage5_tables


def test_legacy_manual_timing_upgrades_to_user_edited_provenance(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "legacy-timing.sqlite3")
    database.initialize(MIGRATIONS[:12])
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO lyrics_documents(
                document_id, document_kind, source_name, approval_state, retrieved_at
            ) VALUES ('doc', 'synced', 'fixture', 'approved',
                      '2026-09-11T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO lyric_representations(
                document_id, representation_id, representation_kind, provenance,
                approval_state, position
            ) VALUES ('doc', 'original', 'original', 'user', 'approved', 0)
            """
        )
        connection.execute(
            """
            INSERT INTO lyric_lines(
                document_id, representation_id, line_id, position, line_text,
                start_ms, timing_provenance
            ) VALUES ('doc', 'original', 'line-1', 0, 'line', 1000, 'manual')
            """
        )

    assert database.initialize() == CURRENT_SCHEMA_VERSION

    with database.connection(readonly=True) as connection:
        document = connection.execute(
            "SELECT timing_level FROM lyrics_documents WHERE document_id = 'doc'"
        ).fetchone()
        line = connection.execute(
            "SELECT timing_provenance_detail FROM lyric_lines WHERE line_id = 'line-1'"
        ).fetchone()
    assert document[0] == "line"
    assert line[0] == "user-edited"


def test_stage_seven_database_adds_corrections_without_rewriting_source(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "stage-seven.sqlite3")
    database.initialize(MIGRATIONS[:13])
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO lyrics_documents(
                document_id, document_kind, source_name, original_text,
                approval_state, retrieved_at, timing_level
            ) VALUES ('doc', 'synced', 'fixture', 'provider line', 'approved',
                      '2026-09-12T00:00:00+00:00', 'line')
            """
        )
        connection.execute(
            """
            INSERT INTO lyric_representations(
                document_id, representation_id, representation_kind, provenance,
                approval_state, position
            ) VALUES ('doc', 'original', 'original', 'provider', 'approved', 0)
            """
        )
        connection.execute(
            """
            INSERT INTO lyric_lines(
                document_id, representation_id, line_id, position, line_text,
                start_ms, timing_provenance_detail
            ) VALUES ('doc', 'original', 'line-1', 0, 'provider line', 1000,
                      'provider')
            """
        )

    assert database.initialize() == CURRENT_SCHEMA_VERSION

    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO lyric_line_corrections(
                document_id, line_id, based_on_text, based_on_start_ms,
                corrected_text, corrected_start_ms, created_at, updated_at
            ) VALUES ('doc', 'line-1', 'provider line', 1000, 'local line', 1250,
                      '2026-09-12T00:01:00+00:00',
                      '2026-09-12T00:01:00+00:00')
            """
        )
    with database.connection(readonly=True) as connection:
        source = connection.execute(
            "SELECT line_text, start_ms FROM lyric_lines WHERE line_id = 'line-1'"
        ).fetchone()
        correction = connection.execute(
            """
            SELECT corrected_text, corrected_start_ms
            FROM lyric_line_corrections WHERE line_id = 'line-1'
            """
        ).fetchone()

    assert tuple(source) == ("provider line", 1000)
    assert tuple(correction) == ("local line", 1250)


def test_stage_six_rejected_match_is_backfilled_into_durable_history(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "stage-six-rejection.sqlite3")
    database.initialize(MIGRATIONS[:6])
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO source_identities(
                source_kind, persistence_scope, local_path, youtube_video_id,
                generic_service_name, generic_track_id, generic_media_url,
                created_at
            ) VALUES ('youtube', 'permanent', NULL, 'xa4WrgqI7q0', NULL, NULL,
                      NULL, '2026-08-23T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO lyrics_documents(
                document_id, document_kind, source_name, original_text,
                approval_state, retrieved_at
            ) VALUES ('wrong-doc', 'plain', 'LRCLIB', 'wrong lyrics',
                      'unreviewed', '2026-08-23T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO lyrics_matches(
                source_identity_id, document_id, decision, provenance, updated_at,
                match_confidence
            ) VALUES (1, 'wrong-doc', 'rejected', 'user',
                      '2026-08-23T00:01:00+00:00', 'High')
            """
        )
        connection.execute(
            """
            INSERT INTO lyrics_match_evidence(
                source_identity_id, position, evidence
            ) VALUES (1, 0, 'explicitly rejected by the user')
            """
        )

    assert database.initialize() == CURRENT_SCHEMA_VERSION

    with database.connection(readonly=True) as connection:
        rejection = connection.execute(
            """
            SELECT document_id, provenance, match_confidence
            FROM lyrics_match_rejections
            """
        ).fetchone()
        evidence = connection.execute(
            "SELECT evidence FROM lyrics_match_rejection_evidence"
        ).fetchone()
    assert tuple(rejection) == ("wrong-doc", "user", "High")
    assert evidence[0] == "explicitly rejected by the user"


def test_stage_nine_database_adds_language_overrides_without_changing_lyrics(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "stage-nine.sqlite3")
    database.initialize(MIGRATIONS[:8])
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO lyrics_documents(
                document_id, document_kind, source_name, original_text,
                approval_state, retrieved_at
            ) VALUES ('chinese-doc', 'plain', 'fixture', '阳光彩虹小白马',
                      'unreviewed', '2026-08-23T00:00:00+00:00')
            """
        )

    assert database.initialize() == CURRENT_SCHEMA_VERSION

    with database.connection(readonly=True) as connection:
        original = connection.execute(
            """
            SELECT original_text FROM lyrics_documents
            WHERE document_id = 'chinese-doc'
            """
        ).fetchone()[0]
        override_count = connection.execute(
            "SELECT COUNT(*) FROM lyric_document_language_overrides"
        ).fetchone()[0]
    assert original == "阳光彩虹小白马"
    assert override_count == 0


def test_failed_migration_rolls_back_only_that_migration(tmp_path: Path) -> None:
    path = tmp_path / "failed.sqlite3"
    migrations = (
        Migration(1, "first", ("CREATE TABLE first_table(id INTEGER)",)),
        Migration(
            2,
            "broken second",
            (
                "CREATE TABLE half_applied(id INTEGER)",
                "INSERT INTO table_that_does_not_exist VALUES (1)",
            ),
        ),
    )
    database = SQLiteDatabase(path)

    with pytest.raises(StorageMigrationError, match="migration 2"):
        database.initialize(migrations)

    assert [item[0] for item in database.migration_history()] == [1]
    with database.connection(readonly=True) as connection:
        names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "first_table" in names
    assert "half_applied" not in names


def test_unknown_newer_schema_is_refused_without_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = SQLiteDatabase(tmp_path / "newer.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO schema_migrations(version, name, checksum, applied_at)
            VALUES (99, 'future', 'future', '2026-08-12T00:00:00+00:00')
            """
        )

    def unexpected_transaction() -> None:
        raise AssertionError("newer-schema refusal opened a write transaction")

    monkeypatch.setattr(database, "transaction", unexpected_transaction)

    with pytest.raises(UnsupportedSchemaError, match="newer"):
        database.initialize()

    assert database.migration_history()[-1][0] == 99


def test_changed_migration_history_is_refused(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "changed.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum = ? WHERE version = 1",
            ("changed",),
        )

    with pytest.raises(UnsupportedSchemaError, match="differs"):
        database.initialize()


def test_foreign_keys_are_enabled_on_every_connection(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "foreign-keys.sqlite3")
    database.initialize()

    with database.connection(readonly=True) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with (
        pytest.raises(StorageError, match="FOREIGN KEY"),
        database.transaction() as connection,
    ):
        connection.execute(
            """
            INSERT INTO track_overrides(
                source_identity_id, approved_title, approved_album, provenance,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                999,
                "Title",
                None,
                "user-approved",
                "2026-08-12T00:00:00+00:00",
                "2026-08-12T00:00:00+00:00",
            ),
        )


def test_transaction_rolls_back_failed_write(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "rollback.sqlite3")
    database.initialize()

    with (
        pytest.raises(RuntimeError, match="stop"),
        database.transaction() as connection,
    ):
        connection.execute(
            """
            INSERT INTO provider_cache(
                provider, cache_key, payload, retrieved_at, expires_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("provider", "key", b"payload", "2026-08-12T00:00:00+00:00", None),
        )
        raise RuntimeError("stop")

    with database.connection(readonly=True) as connection:
        count = connection.execute("SELECT COUNT(*) FROM provider_cache").fetchone()[0]
    assert count == 0


def test_corrupt_database_is_reported_and_never_deleted(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.sqlite3"
    payload = b"this is not sqlite"
    path.write_bytes(payload)
    original_mode = stat.S_IMODE(path.stat().st_mode)

    with pytest.raises(StorageCorruptError):
        SQLiteDatabase(path).initialize()

    assert path.read_bytes() == payload
    assert stat.S_IMODE(path.stat().st_mode) == original_mode


def test_locked_database_has_bounded_controlled_failure(tmp_path: Path) -> None:
    path = tmp_path / "locked.sqlite3"
    database = SQLiteDatabase(path, busy_timeout_ms=10)
    database.initialize()
    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(StorageLockedError), database.transaction():
            pass
    finally:
        blocker.rollback()
        blocker.close()


def test_unwritable_shape_is_reported_without_traceback(tmp_path: Path) -> None:
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("occupied", encoding="utf-8")

    with pytest.raises(StoragePathError, match="parent"):
        SQLiteDatabase(parent_file / "db.sqlite3").initialize()
