"""Data-safe migration from LyricFlow to LyriFlux XDG state."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from lyriflux import cli
from lyriflux.application.settings import DesktopInteractionSettings
from lyriflux.infrastructure.storage.bootstrap import open_storage
from lyriflux.infrastructure.storage.diagnostics import inspect_storage
from lyriflux.infrastructure.storage.errors import StorageMigrationError
from lyriflux.infrastructure.storage.sqlite import SQLiteDatabase
from lyriflux.infrastructure.storage.state_migration import (
    DATABASE_FILENAME,
    LEGACY_DATABASE_FILENAME,
    MIGRATION_MARKER_FILENAME,
    migrate_legacy_xdg_state,
)


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }


def _seed_legacy_database(path: Path) -> None:
    database = SQLiteDatabase(path)
    database.initialize()
    timestamp = "2026-08-24T00:00:00+00:00"
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
                source_identity_id, approved_title, provenance, created_at, updated_at
            ) VALUES (1, 'Corrected title', 'user-approved', ?, ?)
            """,
            (timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO track_override_artists(source_identity_id, position, artist)
            VALUES (1, 0, 'Corrected artist')
            """
        )
        connection.execute(
            """
            INSERT INTO settings(settings_id, format_version, updated_at)
            VALUES (1, 1, ?)
            """,
            (timestamp,),
        )
        connection.execute(
            """
            INSERT INTO player_setting_entries(
                settings_id, setting_kind, position, player_name
            ) VALUES (1, 'preferred', 0, 'strawberry')
            """
        )
        connection.execute(
            """
            INSERT INTO lyrics_documents(
                document_id, document_kind, source_name, original_text,
                approval_state, retrieved_at
            ) VALUES ('preserved-doc', 'synced', 'fixture', '原文', 'approved', ?)
            """,
            (timestamp,),
        )
        connection.execute(
            """
            INSERT INTO lyric_representations(
                document_id, representation_id, representation_kind, provenance,
                approval_state, position
            ) VALUES ('preserved-doc', 'original', 'original', 'provider',
                      'approved', 0)
            """
        )
        connection.execute(
            """
            INSERT INTO lyric_lines(
                document_id, representation_id, line_id, position, line_text,
                start_ms, timing_provenance
            ) VALUES ('preserved-doc', 'original', 'line-1', 0, '原文', 1250,
                      'provider')
            """
        )
        connection.execute(
            """
            INSERT INTO lyric_representation_candidates(
                candidate_id, document_id, source_line_id, representation_kind,
                candidate_status, candidate_text, provenance, source_name,
                approval_state, uncertainty, created_at, updated_at
            ) VALUES ('candidate-1', 'preserved-doc', 'line-1', 'romanized',
                      'available', 'genbun', 'generated', 'fixture', 'unreviewed',
                      'none', ?, ?)
            """,
            (timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO lyric_representation_decisions(
                document_id, source_line_id, representation_kind, approval_state,
                decision_text, based_on_candidate_id, created_at, updated_at
            ) VALUES ('preserved-doc', 'line-1', 'romanized', 'approved',
                      'approved reading', 'candidate-1', ?, ?)
            """,
            (timestamp, timestamp),
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_neither_legacy_nor_current_state_is_created_by_detection(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)

    outcomes = migrate_legacy_xdg_state(environment=environment, home=tmp_path)

    assert [outcome.state for outcome in outcomes] == ["absent"] * 3
    assert not (tmp_path / "data" / "lyriflux").exists()
    assert not (tmp_path / "data" / "lyricflow").exists()


def test_new_state_only_is_used_without_legacy_artifacts(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    current = tmp_path / "data" / "lyriflux"
    current.mkdir(parents=True)
    SQLiteDatabase(current / DATABASE_FILENAME).initialize()

    outcomes = migrate_legacy_xdg_state(environment=environment, home=tmp_path)

    assert [outcome.state for outcome in outcomes] == ["absent", "current", "absent"]
    assert inspect_storage(current / DATABASE_FILENAME).integrity_status == "ok"


def test_legacy_state_migrates_non_destructively_and_is_idempotent(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    for kind in ("config", "data", "cache"):
        legacy = tmp_path / kind / "lyricflow"
        legacy.mkdir(parents=True)
        (legacy / f"{kind}.txt").write_text(f"preserved-{kind}", encoding="utf-8")
    legacy_database = tmp_path / "data" / "lyricflow" / LEGACY_DATABASE_FILENAME
    _seed_legacy_database(legacy_database)
    original_hash = _sha256(legacy_database)

    first = migrate_legacy_xdg_state(environment=environment, home=tmp_path)
    second = migrate_legacy_xdg_state(environment=environment, home=tmp_path)

    assert [outcome.state for outcome in first] == ["migrated"] * 3
    assert [outcome.state for outcome in second] == ["migrated"] * 3
    assert _sha256(legacy_database) == original_hash
    assert legacy_database.is_file()
    current_database = tmp_path / "data" / "lyriflux" / DATABASE_FILENAME
    assert current_database.is_file()
    assert not (current_database.parent / LEGACY_DATABASE_FILENAME).exists()
    assert inspect_storage(current_database).integrity_status == "ok"
    for kind in ("config", "data", "cache"):
        assert (tmp_path / kind / "lyricflow").is_dir()
        assert (tmp_path / kind / "lyriflux" / MIGRATION_MARKER_FILENAME).is_file()
        assert (tmp_path / kind / "lyriflux" / f"{kind}.txt").read_text() == (
            f"preserved-{kind}"
        )


def test_migration_preserves_settings_corrections_representations_timing_and_library(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    legacy_database = tmp_path / "data" / "lyricflow" / LEGACY_DATABASE_FILENAME
    _seed_legacy_database(legacy_database)

    migrate_legacy_xdg_state(environment=environment, home=tmp_path)
    current_database = tmp_path / "data" / "lyriflux" / DATABASE_FILENAME
    with SQLiteDatabase(current_database).connection(readonly=True) as connection:
        assert (
            connection.execute(
                "SELECT player_name FROM player_setting_entries"
            ).fetchone()[0]
            == "strawberry"
        )
        assert (
            connection.execute("SELECT approved_title FROM track_overrides").fetchone()[
                0
            ]
            == "Corrected title"
        )
        assert (
            connection.execute(
                "SELECT decision_text FROM lyric_representation_decisions"
            ).fetchone()[0]
            == "approved reading"
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
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_both_unrelated_namespaces_fail_without_overwrite(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    legacy = tmp_path / "data" / "lyricflow"
    current = tmp_path / "data" / "lyriflux"
    legacy.mkdir(parents=True)
    current.mkdir(parents=True)
    legacy_config = tmp_path / "config" / "lyricflow"
    legacy_config.mkdir(parents=True)
    (legacy_config / "settings.toml").write_text("preserved", encoding="utf-8")
    (legacy / "legacy.txt").write_text("legacy", encoding="utf-8")
    (current / "current.txt").write_text("current", encoding="utf-8")

    with pytest.raises(StorageMigrationError, match="both legacy LyricFlow"):
        migrate_legacy_xdg_state(environment=environment, home=tmp_path)

    assert (legacy / "legacy.txt").read_text() == "legacy"
    assert (current / "current.txt").read_text() == "current"
    assert not (current / MIGRATION_MARKER_FILENAME).exists()
    assert not (tmp_path / "config" / "lyriflux").exists()


def test_changed_legacy_state_after_migration_fails_safely(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    legacy = tmp_path / "cache" / "lyricflow"
    legacy.mkdir(parents=True)
    (legacy / "cache.txt").write_text("first", encoding="utf-8")
    migrate_legacy_xdg_state(environment=environment, home=tmp_path)
    (legacy / "cache.txt").write_text("changed after migration", encoding="utf-8")

    with pytest.raises(StorageMigrationError, match="both legacy LyricFlow"):
        migrate_legacy_xdg_state(environment=environment, home=tmp_path)

    assert (tmp_path / "cache" / "lyriflux" / "cache.txt").read_text() == "first"
    assert (legacy / "cache.txt").read_text() == "changed after migration"


def test_restart_and_normal_writes_use_only_the_current_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = _environment(tmp_path)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    legacy_database = tmp_path / "data" / "lyricflow" / LEGACY_DATABASE_FILENAME
    _seed_legacy_database(legacy_database)
    legacy_hash = _sha256(legacy_database)

    first = open_storage()
    first.settings.put_desktop_interaction(DesktopInteractionSettings(True))
    second = open_storage()

    assert second.database.path == tmp_path / "data" / "lyriflux" / DATABASE_FILENAME
    assert second.settings.get_desktop_interaction().allow_lyric_selection is True
    assert _sha256(legacy_database) == legacy_hash


def test_invalid_legacy_database_is_retained_and_not_published(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    legacy_database = tmp_path / "data" / "lyricflow" / LEGACY_DATABASE_FILENAME
    legacy_database.parent.mkdir(parents=True)
    legacy_database.write_bytes(b"not sqlite")

    with pytest.raises(StorageMigrationError, match="not healthy and current"):
        migrate_legacy_xdg_state(environment=environment, home=tmp_path)

    assert legacy_database.read_bytes() == b"not sqlite"
    assert not (tmp_path / "data" / "lyriflux").exists()


def test_storage_status_cli_migrates_legacy_state_before_reporting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    environment = _environment(tmp_path)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    legacy_database = tmp_path / "data" / "lyricflow" / LEGACY_DATABASE_FILENAME
    _seed_legacy_database(legacy_database)

    assert cli.main(["storage", "status"]) == 0

    output = capsys.readouterr().out
    assert "LyriFlux storage status" in output
    assert str(tmp_path / "data" / "lyriflux" / DATABASE_FILENAME) in output
    assert "integrity: ok" in output
    assert legacy_database.is_file()
