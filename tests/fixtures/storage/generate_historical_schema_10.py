"""Regenerate the synthetic database made by the accepted schema-10 registry."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from konokashi.infrastructure.storage.sqlite import SQLiteDatabase

HISTORICAL_COMMIT = "b7a33abd5600c6f31833cf67648ee4ddd042f920"
MIGRATIONS_PATH = "src/konokashi/infrastructure/storage/migrations/__init__.py"
EXPECTED_SCHEMA_10_CHECKSUMS = (
    "40ae408170640bda1816403f7e2c3f3a94f1398c2c669a8121409ddb96d23600",
    "e27fd4fa65f3eed5750986ff52dd1f80c86f0773539bdd451a73e284822538aa",
    "a38f35faf2bbc6160bfd9850e3d513dd1222b2276cc808b74eace96a5f83704c",
    "2e6f4476757ffdc21fa2d10a1ab1b203dbc7ab909210dca6c53f7d929662c25e",
    "7756aa1ed0c9273c815533a41f54653271cab4461b01f93a1e0d9649d205b411",
    "7a4272ccc133998eb25504d146cd348ccaa8e12bbbf73bac1d9f62524b1f22fe",
    "e5c047e4507dead3401d162963e1b94b38133a144cba23e02ded79cf64633c2e",
    "dfca70afa75a09aa75b8494c859e5e5ab6273aec191f67b2aff650d56ea790f4",
    "da1f12663d4986d9cbe2cc6fdf080a3eb6e1ca96a06526cc9c88b67c56ce8f47",
    "c58f44a9499edd83dc6075d662b62a48a5eb6e827b649b3f3226070d13d45f93",
)
FIXTURE_TIMESTAMP = "2026-09-08T12:00:00+00:00"


def _historical_migrations(repository: Path) -> tuple[object, ...]:
    result = subprocess.run(
        ["git", "show", f"{HISTORICAL_COMMIT}:{MIGRATIONS_PATH}"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    namespace: dict[str, object] = {}
    exec(
        compile(result.stdout, f"{HISTORICAL_COMMIT}:{MIGRATIONS_PATH}", "exec"),
        namespace,
    )
    migrations = tuple(namespace["MIGRATIONS"])  # type: ignore[arg-type]
    schema_10 = migrations[:10]
    checksums = tuple(migration.checksum for migration in schema_10)
    if checksums != EXPECTED_SCHEMA_10_CHECKSUMS:
        raise RuntimeError(
            "accepted historical migration identity changed unexpectedly"
        )
    return schema_10


def generate(target: Path) -> str:
    if target.exists():
        raise FileExistsError(f"refusing to overwrite {target}")
    repository = Path(__file__).resolve().parents[3]
    database = SQLiteDatabase(target)
    database.initialize(_historical_migrations(repository))  # type: ignore[arg-type]

    with database.transaction() as connection:
        connection.execute(
            "UPDATE schema_migrations SET applied_at = ?", (FIXTURE_TIMESTAMP,)
        )
        connection.execute(
            """
            INSERT INTO source_identities(
                id, source_kind, persistence_scope, youtube_video_id, created_at
            ) VALUES (1, 'youtube', 'permanent', 'AbCdEfGhI12', ?)
            """,
            (FIXTURE_TIMESTAMP,),
        )
        connection.execute(
            """
            INSERT INTO track_overrides(
                source_identity_id, approved_title, approved_album, provenance,
                created_at, updated_at
            ) VALUES (1, 'Synthetic Corrected Title', 'Fixture Album',
                      'user-approved', ?, ?)
            """,
            (FIXTURE_TIMESTAMP, FIXTURE_TIMESTAMP),
        )
        connection.execute(
            """
            INSERT INTO track_override_artists(source_identity_id, position, artist)
            VALUES (1, 0, 'Synthetic Artist')
            """
        )
        connection.execute(
            """
            INSERT INTO settings(settings_id, format_version, updated_at)
            VALUES (1, 1, ?)
            """,
            (FIXTURE_TIMESTAMP,),
        )
        connection.execute(
            """
            INSERT INTO player_setting_entries(
                settings_id, setting_kind, position, player_name
            ) VALUES (1, 'preferred', 0, 'fixture-player')
            """
        )
        connection.execute(
            """
            INSERT INTO lyrics_documents(
                document_id, document_kind, source_name, original_text,
                raw_text_checksum, provider_record_id, language, script,
                duration_ms, approval_state, retrieved_at, source_title,
                source_artist, source_album
            ) VALUES (
                'historical-doc', 'synced', 'FixtureProvider',
                '[00:01.00]星のテスト\n[00:02.00]合成データ',
                '506b18a82bbff7dc10c93a3a023ea336706413c0a6c10cd3f210f4076292b587',
                'fixture-record-10', 'ja', 'Jpan', 180000, 'approved', ?,
                'Synthetic Corrected Title', 'Synthetic Artist', 'Fixture Album'
            )
            """,
            (FIXTURE_TIMESTAMP,),
        )
        connection.execute(
            """
            INSERT INTO lyric_representations(
                document_id, representation_id, representation_kind, language,
                script, provenance, approval_state, position
            ) VALUES (
                'historical-doc', 'original', 'original', 'ja', 'Jpan',
                'provider', 'approved', 0
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO lyric_lines(
                document_id, representation_id, line_id, position, line_text,
                start_ms, timing_provenance
            ) VALUES ('historical-doc', 'original', ?, ?, ?, ?, 'provider')
            """,
            (
                ("line-1", 0, "星のテスト", 1000),
                ("line-2", 1, "合成データ", 2000),
            ),
        )
        connection.executemany(
            """
            INSERT INTO lyric_representation_candidates(
                candidate_id, document_id, source_line_id, representation_kind,
                candidate_status, candidate_text, language, script, provenance,
                source_name, source_version, approval_state, uncertainty,
                created_at, updated_at
            ) VALUES (?, 'historical-doc', 'line-1', ?, 'available', ?, ?, ?, ?,
                      ?, '1', ?, 'none', ?, ?)
            """,
            (
                (
                    "generated-reading",
                    "romanized",
                    "hoshi no tesuto",
                    "ja-Latn",
                    "Latn",
                    "generated",
                    "FixtureGenerator",
                    "unreviewed",
                    FIXTURE_TIMESTAMP,
                    FIXTURE_TIMESTAMP,
                ),
                (
                    "local-translation",
                    "translated",
                    "synthetic star test",
                    "en",
                    "Latn",
                    "local",
                    "FixtureImport",
                    "unreviewed",
                    FIXTURE_TIMESTAMP,
                    FIXTURE_TIMESTAMP,
                ),
            ),
        )
        connection.execute(
            """
            INSERT INTO lyric_representation_candidate_diagnostics(
                candidate_id, position, diagnostic
            ) VALUES ('generated-reading', 0, 'synthetic generated fixture')
            """
        )
        connection.execute(
            """
            INSERT INTO lyric_representation_decisions(
                document_id, source_line_id, representation_kind, approval_state,
                decision_text, based_on_candidate_id, created_at, updated_at
            ) VALUES (
                'historical-doc', 'line-1', 'translated', 'approved',
                'approved synthetic translation', 'local-translation', ?, ?
            )
            """,
            (FIXTURE_TIMESTAMP, FIXTURE_TIMESTAMP),
        )
        connection.execute(
            """
            INSERT INTO representation_display_settings(
                settings_id, show_original, show_romanized, show_translated, updated_at
            ) VALUES (1, 1, 1, 1, ?)
            """,
            (FIXTURE_TIMESTAMP,),
        )
        connection.execute(
            """
            INSERT INTO lyrics_matches(
                source_identity_id, document_id, decision, provenance, updated_at,
                match_confidence
            ) VALUES (1, 'historical-doc', 'approved', 'user', ?, 'Approved')
            """,
            (FIXTURE_TIMESTAMP,),
        )
        connection.execute(
            """
            INSERT INTO lyrics_match_evidence(source_identity_id, position, evidence)
            VALUES (1, 0, 'synthetic explicit approval')
            """
        )
        connection.execute(
            """
            INSERT INTO provider_cache(
                provider, cache_key, payload, retrieved_at, expires_at
            ) VALUES ('FixtureProvider', 'synthetic-cache-key', ?, ?, NULL)
            """,
            (b'{"fixture":true}', FIXTURE_TIMESTAMP),
        )
        connection.execute(
            """
            INSERT INTO lyric_document_timing(
                document_id, lyrics_display_delay_us, updated_at
            ) VALUES ('historical-doc', 125000, ?)
            """,
            (FIXTURE_TIMESTAMP,),
        )
        connection.execute(
            """
            INSERT INTO desktop_interaction_settings(
                settings_id, allow_lyric_selection, updated_at
            ) VALUES (1, 1, ?)
            """,
            (FIXTURE_TIMESTAMP,),
        )
        connection.execute(
            """
            INSERT INTO library_settings(
                settings_id, automatic_downloads, worker_count, updated_at
            ) VALUES (1, 0, 2, ?)
            """,
            (FIXTURE_TIMESTAMP,),
        )
        connection.execute(
            """
            INSERT INTO library_roots(settings_id, position, root_path)
            VALUES (1, 0, '/synthetic/library')
            """
        )
        connection.execute(
            """
            INSERT INTO library_scan_runs(
                scan_id, status, started_at, finished_at, discovered, processed,
                unchanged, moved, missing, review, downloaded, download_misses,
                errors
            ) VALUES (10, 'completed', ?, ?, 1, 1, 0, 0, 0, 0, 0, 0, 0)
            """,
            (FIXTURE_TIMESTAMP, FIXTURE_TIMESTAMP),
        )
        connection.execute(
            """
            INSERT INTO library_tracks(
                file_key, current_path, root_path, file_size, mtime_ns, title,
                artists_json, album, duration_us, metadata_source, confidence,
                state, lyrics_status, review_reason, first_seen_at, updated_at
            ) VALUES (
                'synthetic-device:10', '/synthetic/library/fixture.flac',
                '/synthetic/library', 1024, 1000000000, 'Synthetic Corrected Title',
                '["Synthetic Artist"]', 'Fixture Album', 180000000, 'tags',
                'Approved', 'ready', 'available', NULL, ?, ?
            )
            """,
            (FIXTURE_TIMESTAMP, FIXTURE_TIMESTAMP),
        )
        connection.execute(
            """
            INSERT INTO lyric_document_language_overrides(
                document_id, language_code, provenance, created_at, updated_at
            ) VALUES ('historical-doc', 'ja', 'user-approved', ?, ?)
            """,
            (FIXTURE_TIMESTAMP, FIXTURE_TIMESTAMP),
        )
        connection.execute(
            """
            INSERT INTO canonical_config_migrations(
                migration_id, config_schema_version, migrated_at
            ) VALUES (1, 1, ?)
            """,
            (FIXTURE_TIMESTAMP,),
        )

    with database.connection() as connection:
        connection.execute("VACUUM")
    target.chmod(0o644)
    return hashlib.sha256(target.read_bytes()).hexdigest()


if __name__ == "__main__":
    fixture = Path(__file__).with_name("historical_schema_10.sqlite3")
    print(f"created {fixture}")
    print(f"sha256 {generate(fixture)}")
