"""Append-only SQLite migrations retained as durable project history."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True, slots=True)
class Migration:
    """One sequential atomic schema change."""

    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        """Detect edits to a migration that a database already applied."""

        payload = "\n-- statement --\n".join(self.statements).encode()
        return sha256(payload).hexdigest()


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        1,
        "source identities, approved overrides, and player settings",
        (
            """
            CREATE TABLE source_identities (
                id INTEGER PRIMARY KEY,
                source_kind TEXT NOT NULL CHECK (
                    source_kind IN ('local-file', 'youtube', 'generic-mpris')
                ),
                persistence_scope TEXT NOT NULL CHECK (
                    persistence_scope IN ('permanent', 'session-only')
                ),
                local_path TEXT,
                youtube_video_id TEXT,
                generic_service_name TEXT,
                generic_track_id TEXT,
                generic_media_url TEXT,
                created_at TEXT NOT NULL,
                CHECK (
                    (source_kind = 'local-file'
                     AND persistence_scope = 'permanent'
                     AND local_path IS NOT NULL
                     AND youtube_video_id IS NULL
                     AND generic_service_name IS NULL)
                    OR
                    (source_kind = 'youtube'
                     AND persistence_scope = 'permanent'
                     AND local_path IS NULL
                     AND youtube_video_id IS NOT NULL
                     AND generic_service_name IS NULL)
                    OR
                    (source_kind = 'generic-mpris'
                     AND persistence_scope = 'session-only'
                     AND local_path IS NULL
                     AND youtube_video_id IS NULL
                     AND generic_service_name IS NOT NULL)
                )
            )
            """,
            """
            CREATE UNIQUE INDEX source_identities_local_unique
            ON source_identities(local_path)
            WHERE source_kind = 'local-file'
            """,
            """
            CREATE UNIQUE INDEX source_identities_youtube_unique
            ON source_identities(youtube_video_id)
            WHERE source_kind = 'youtube'
            """,
            """
            CREATE UNIQUE INDEX source_identities_generic_unique
            ON source_identities(
                generic_service_name,
                (generic_track_id IS NULL),
                generic_track_id,
                (generic_media_url IS NULL),
                generic_media_url
            )
            WHERE source_kind = 'generic-mpris'
            """,
            """
            CREATE TABLE track_overrides (
                source_identity_id INTEGER PRIMARY KEY
                    REFERENCES source_identities(id) ON DELETE CASCADE,
                approved_title TEXT NOT NULL CHECK (length(approved_title) > 0),
                approved_album TEXT,
                provenance TEXT NOT NULL CHECK (provenance = 'user-approved'),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE track_override_artists (
                source_identity_id INTEGER NOT NULL
                    REFERENCES track_overrides(source_identity_id) ON DELETE CASCADE,
                position INTEGER NOT NULL CHECK (position >= 0),
                artist TEXT NOT NULL CHECK (length(artist) > 0),
                PRIMARY KEY (source_identity_id, position)
            )
            """,
            """
            CREATE TABLE settings (
                settings_id INTEGER PRIMARY KEY CHECK (settings_id = 1),
                format_version INTEGER NOT NULL CHECK (format_version = 1),
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE player_setting_entries (
                settings_id INTEGER NOT NULL
                    REFERENCES settings(settings_id) ON DELETE CASCADE,
                setting_kind TEXT NOT NULL CHECK (
                    setting_kind IN ('preferred', 'ignored')
                ),
                position INTEGER NOT NULL CHECK (position >= 0),
                player_name TEXT NOT NULL CHECK (length(player_name) > 0),
                PRIMARY KEY (settings_id, setting_kind, position),
                UNIQUE (settings_id, setting_kind, player_name)
            )
            """,
        ),
    ),
    Migration(
        2,
        "provider-neutral lyric documents, matches, and provider cache",
        (
            """
            CREATE TABLE lyrics_documents (
                document_id TEXT PRIMARY KEY,
                document_kind TEXT NOT NULL CHECK (
                    document_kind IN ('plain', 'synced', 'instrumental')
                ),
                source_name TEXT NOT NULL,
                original_text TEXT,
                raw_text_checksum TEXT,
                provider_record_id TEXT,
                language TEXT,
                script TEXT,
                duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
                approval_state TEXT NOT NULL CHECK (
                    approval_state IN ('unreviewed', 'approved', 'rejected')
                ),
                retrieved_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE lyric_representations (
                document_id TEXT NOT NULL
                    REFERENCES lyrics_documents(document_id) ON DELETE CASCADE,
                representation_id TEXT NOT NULL,
                representation_kind TEXT NOT NULL CHECK (
                    representation_kind IN (
                        'original', 'romanized', 'transliterated', 'translated'
                    )
                ),
                language TEXT,
                script TEXT,
                provenance TEXT NOT NULL CHECK (
                    provenance IN ('provider', 'local', 'imported', 'generated', 'user')
                ),
                approval_state TEXT NOT NULL CHECK (
                    approval_state IN ('unreviewed', 'approved', 'rejected')
                ),
                generator_name TEXT,
                generator_version TEXT,
                position INTEGER NOT NULL CHECK (position >= 0),
                PRIMARY KEY (document_id, representation_id),
                UNIQUE (document_id, position)
            )
            """,
            """
            CREATE TABLE lyric_lines (
                document_id TEXT NOT NULL,
                representation_id TEXT NOT NULL,
                line_id TEXT NOT NULL,
                position INTEGER NOT NULL CHECK (position >= 0),
                line_text TEXT NOT NULL,
                start_ms INTEGER CHECK (start_ms IS NULL OR start_ms >= 0),
                end_ms INTEGER CHECK (end_ms IS NULL OR end_ms >= 0),
                timing_provenance TEXT CHECK (
                    timing_provenance IS NULL
                    OR timing_provenance IN ('provider', 'generated', 'manual')
                ),
                source_line_id TEXT,
                PRIMARY KEY (document_id, line_id),
                UNIQUE (document_id, representation_id, position),
                FOREIGN KEY (document_id, representation_id)
                    REFERENCES lyric_representations(document_id, representation_id)
                    ON DELETE CASCADE,
                CHECK (end_ms IS NULL OR start_ms IS NULL OR end_ms >= start_ms)
            )
            """,
            """
            CREATE TABLE lyric_line_alignments (
                document_id TEXT NOT NULL,
                source_line_id TEXT NOT NULL,
                target_line_id TEXT NOT NULL,
                position INTEGER NOT NULL CHECK (position >= 0),
                PRIMARY KEY (document_id, source_line_id, target_line_id),
                UNIQUE (document_id, position),
                FOREIGN KEY (document_id, source_line_id)
                    REFERENCES lyric_lines(document_id, line_id) ON DELETE CASCADE,
                FOREIGN KEY (document_id, target_line_id)
                    REFERENCES lyric_lines(document_id, line_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE lyrics_matches (
                source_identity_id INTEGER PRIMARY KEY
                    REFERENCES source_identities(id) ON DELETE CASCADE,
                document_id TEXT NOT NULL
                    REFERENCES lyrics_documents(document_id) ON DELETE RESTRICT,
                decision TEXT NOT NULL CHECK (
                    decision IN ('candidate', 'approved', 'rejected')
                ),
                provenance TEXT NOT NULL CHECK (
                    provenance IN ('provider', 'local', 'imported', 'generated', 'user')
                ),
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE provider_cache (
                provider TEXT NOT NULL,
                cache_key TEXT NOT NULL,
                payload BLOB NOT NULL,
                retrieved_at TEXT NOT NULL,
                expires_at TEXT,
                PRIMARY KEY (provider, cache_key)
            )
            """,
        ),
    ),
    Migration(
        3,
        "lyrics match confidence, evidence, and source metadata",
        (
            "ALTER TABLE lyrics_documents ADD COLUMN source_title TEXT",
            "ALTER TABLE lyrics_documents ADD COLUMN source_artist TEXT",
            "ALTER TABLE lyrics_documents ADD COLUMN source_album TEXT",
            """
            ALTER TABLE lyrics_matches ADD COLUMN match_confidence TEXT NOT NULL
            DEFAULT 'Low' CHECK (
                match_confidence IN ('Approved', 'High', 'Medium', 'Low')
            )
            """,
            """
            UPDATE lyrics_matches SET match_confidence = 'Approved'
            WHERE decision = 'approved'
            """,
            """
            CREATE TABLE lyrics_match_evidence (
                source_identity_id INTEGER NOT NULL
                    REFERENCES lyrics_matches(source_identity_id) ON DELETE CASCADE,
                position INTEGER NOT NULL CHECK (position >= 0),
                evidence TEXT NOT NULL CHECK (length(evidence) > 0),
                PRIMARY KEY (source_identity_id, position)
            )
            """,
            """
            CREATE UNIQUE INDEX lyrics_provider_record_unique
            ON lyrics_documents(source_name, provider_record_id)
            WHERE provider_record_id IS NOT NULL
            """,
        ),
    ),
)

CURRENT_SCHEMA_VERSION = MIGRATIONS[-1].version
