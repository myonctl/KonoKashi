"""Release-gate upgrade from the last pre-packaging development schema."""

from __future__ import annotations

from pathlib import Path

from lyricflow.infrastructure.storage.migrations import MIGRATIONS
from lyricflow.infrastructure.storage.sqlite import SQLiteDatabase


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

    assert database.initialize() == 9

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
