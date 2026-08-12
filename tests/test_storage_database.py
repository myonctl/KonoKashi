"""SQLite path, migration, transaction, and controlled-failure tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lyricflow.infrastructure.storage.errors import (
    StorageCorruptError,
    StorageError,
    StorageLockedError,
    StorageMigrationError,
    StoragePathError,
    UnsupportedSchemaError,
)
from lyricflow.infrastructure.storage.migrations import (
    CURRENT_SCHEMA_VERSION,
    Migration,
)
from lyricflow.infrastructure.storage.paths import default_database_path
from lyricflow.infrastructure.storage.sqlite import SQLiteDatabase


def test_default_database_path_uses_absolute_xdg_data_home_without_writes(
    tmp_path: Path,
) -> None:
    xdg = tmp_path / "Données utilisateur"

    path = default_database_path(
        environment={"XDG_DATA_HOME": str(xdg)}, home=tmp_path / "home"
    )

    assert path == xdg / "lyricflow" / "lyricflow.sqlite3"
    assert not path.exists()


def test_relative_xdg_data_home_is_ignored(tmp_path: Path) -> None:
    path = default_database_path(
        environment={"XDG_DATA_HOME": "relative"}, home=tmp_path
    )

    assert path == tmp_path / ".local/share/lyricflow/lyricflow.sqlite3"


def test_brand_new_unicode_database_migrates_in_order_and_reopens_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "Unicode space 日本語" / "lyrics data.sqlite3"
    database = SQLiteDatabase(path)

    assert database.initialize() == CURRENT_SCHEMA_VERSION
    first_history = database.migration_history()
    assert [item[0] for item in first_history] == [1, 2]

    def unexpected_transaction() -> None:
        raise AssertionError("current-schema initialization opened a write transaction")

    monkeypatch.setattr(database, "transaction", unexpected_transaction)
    assert database.initialize() == CURRENT_SCHEMA_VERSION
    assert database.migration_history() == first_history


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

    with pytest.raises(StorageCorruptError):
        SQLiteDatabase(path).initialize()

    assert path.read_bytes() == payload


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
