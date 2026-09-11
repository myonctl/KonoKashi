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
    Migration(
        4,
        "line representation candidates, decisions, and display settings",
        (
            """
            CREATE TABLE lyric_representation_candidates (
                candidate_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL
                    REFERENCES lyrics_documents(document_id) ON DELETE CASCADE,
                source_line_id TEXT NOT NULL,
                representation_kind TEXT NOT NULL CHECK (
                    representation_kind IN ('romanized', 'transliterated', 'translated')
                ),
                candidate_status TEXT NOT NULL CHECK (
                    candidate_status IN ('available', 'unavailable', 'failed')
                ),
                candidate_text TEXT,
                language TEXT,
                script TEXT,
                provenance TEXT NOT NULL CHECK (
                    provenance IN ('provider', 'local', 'imported', 'generated', 'user')
                ),
                source_name TEXT NOT NULL CHECK (length(source_name) > 0),
                source_version TEXT,
                approval_state TEXT NOT NULL CHECK (
                    approval_state IN ('unreviewed', 'approved', 'rejected')
                ),
                uncertainty TEXT NOT NULL CHECK (
                    uncertainty IN ('none', 'ambiguous')
                ),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (
                    (candidate_status = 'available' AND candidate_text IS NOT NULL)
                    OR (candidate_status != 'available')
                )
            )
            """,
            """
            CREATE INDEX lyric_representation_candidates_lookup
            ON lyric_representation_candidates(
                document_id, source_line_id, representation_kind, provenance
            )
            """,
            """
            CREATE TABLE lyric_representation_candidate_diagnostics (
                candidate_id TEXT NOT NULL
                    REFERENCES lyric_representation_candidates(candidate_id)
                    ON DELETE CASCADE,
                position INTEGER NOT NULL CHECK (position >= 0),
                diagnostic TEXT NOT NULL CHECK (length(diagnostic) > 0),
                PRIMARY KEY (candidate_id, position)
            )
            """,
            """
            CREATE TABLE lyric_representation_decisions (
                document_id TEXT NOT NULL
                    REFERENCES lyrics_documents(document_id) ON DELETE CASCADE,
                source_line_id TEXT NOT NULL,
                representation_kind TEXT NOT NULL CHECK (
                    representation_kind IN ('romanized', 'transliterated', 'translated')
                ),
                approval_state TEXT NOT NULL CHECK (
                    approval_state IN ('unreviewed', 'approved', 'rejected')
                ),
                decision_text TEXT,
                based_on_candidate_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (document_id, source_line_id, representation_kind),
                CHECK (
                    approval_state = 'rejected'
                    OR (decision_text IS NOT NULL AND length(decision_text) > 0)
                )
            )
            """,
            """
            CREATE TABLE representation_display_settings (
                settings_id INTEGER PRIMARY KEY CHECK (settings_id = 1),
                show_original INTEGER NOT NULL CHECK (show_original IN (0, 1)),
                show_romanized INTEGER NOT NULL CHECK (show_romanized IN (0, 1)),
                show_translated INTEGER NOT NULL CHECK (show_translated IN (0, 1)),
                updated_at TEXT NOT NULL
            )
            """,
        ),
    ),
    Migration(
        5,
        "document timing delay and output-device residual calibration",
        (
            """
            CREATE TABLE lyric_document_timing (
                document_id TEXT PRIMARY KEY
                    REFERENCES lyrics_documents(document_id) ON DELETE CASCADE,
                lyrics_display_delay_us INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE audio_output_calibrations (
                device_key TEXT PRIMARY KEY CHECK (length(device_key) > 0),
                device_label TEXT NOT NULL CHECK (length(device_label) > 0),
                residual_delay_us INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        ),
    ),
    Migration(
        6,
        "desktop interaction settings",
        (
            """
            CREATE TABLE desktop_interaction_settings (
                settings_id INTEGER PRIMARY KEY CHECK (settings_id = 1),
                allow_lyric_selection INTEGER NOT NULL CHECK (
                    allow_lyric_selection IN (0, 1)
                ),
                updated_at TEXT NOT NULL
            )
            """,
        ),
    ),
    Migration(
        7,
        "durable lyrics match rejection history",
        (
            """
            CREATE TABLE lyrics_match_rejections (
                source_identity_id INTEGER NOT NULL
                    REFERENCES source_identities(id) ON DELETE CASCADE,
                document_id TEXT NOT NULL
                    REFERENCES lyrics_documents(document_id) ON DELETE CASCADE,
                provenance TEXT NOT NULL CHECK (
                    provenance IN ('provider', 'local', 'imported', 'generated', 'user')
                ),
                rejected_at TEXT NOT NULL,
                match_confidence TEXT NOT NULL CHECK (
                    match_confidence IN ('Approved', 'High', 'Medium', 'Low')
                ),
                PRIMARY KEY (source_identity_id, document_id)
            )
            """,
            """
            CREATE TABLE lyrics_match_rejection_evidence (
                source_identity_id INTEGER NOT NULL,
                document_id TEXT NOT NULL,
                position INTEGER NOT NULL CHECK (position >= 0),
                evidence TEXT NOT NULL CHECK (length(evidence) > 0),
                PRIMARY KEY (source_identity_id, document_id, position),
                FOREIGN KEY (source_identity_id, document_id)
                    REFERENCES lyrics_match_rejections(
                        source_identity_id, document_id
                    ) ON DELETE CASCADE
            )
            """,
            """
            INSERT INTO lyrics_match_rejections(
                source_identity_id, document_id, provenance, rejected_at,
                match_confidence
            )
            SELECT source_identity_id, document_id, provenance, updated_at,
                   match_confidence
            FROM lyrics_matches
            WHERE decision = 'rejected'
            """,
            """
            INSERT INTO lyrics_match_rejection_evidence(
                source_identity_id, document_id, position, evidence
            )
            SELECT matches.source_identity_id, matches.document_id,
                   evidence.position, evidence.evidence
            FROM lyrics_matches AS matches
            JOIN lyrics_match_evidence AS evidence
              ON evidence.source_identity_id = matches.source_identity_id
            WHERE matches.decision = 'rejected'
            """,
        ),
    ),
    Migration(
        8,
        "music-library settings, resumable scan index, and review queue",
        (
            """
            CREATE TABLE library_settings (
                settings_id INTEGER PRIMARY KEY CHECK (settings_id = 1),
                automatic_downloads INTEGER NOT NULL CHECK (
                    automatic_downloads IN (0, 1)
                ),
                worker_count INTEGER NOT NULL CHECK (worker_count BETWEEN 1 AND 8),
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE library_roots (
                settings_id INTEGER NOT NULL
                    REFERENCES library_settings(settings_id) ON DELETE CASCADE,
                position INTEGER NOT NULL CHECK (position >= 0),
                root_path TEXT NOT NULL CHECK (length(root_path) > 0),
                PRIMARY KEY (settings_id, position),
                UNIQUE (root_path)
            )
            """,
            """
            CREATE TABLE library_scan_runs (
                scan_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL CHECK (
                    status IN ('running', 'completed', 'cancelled', 'failed')
                ),
                started_at TEXT NOT NULL,
                finished_at TEXT,
                discovered INTEGER NOT NULL DEFAULT 0,
                processed INTEGER NOT NULL DEFAULT 0,
                unchanged INTEGER NOT NULL DEFAULT 0,
                moved INTEGER NOT NULL DEFAULT 0,
                missing INTEGER NOT NULL DEFAULT 0,
                review INTEGER NOT NULL DEFAULT 0,
                downloaded INTEGER NOT NULL DEFAULT 0,
                download_misses INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE TABLE library_tracks (
                file_key TEXT PRIMARY KEY CHECK (length(file_key) > 0),
                current_path TEXT NOT NULL CHECK (length(current_path) > 0),
                root_path TEXT NOT NULL CHECK (length(root_path) > 0),
                file_size INTEGER NOT NULL CHECK (file_size >= 0),
                mtime_ns INTEGER NOT NULL CHECK (mtime_ns >= 0),
                title TEXT,
                artists_json TEXT NOT NULL,
                album TEXT,
                duration_us INTEGER,
                metadata_source TEXT NOT NULL CHECK (
                    metadata_source IN (
                        'tags', 'filename', 'tags-and-filename', 'unreadable'
                    )
                ),
                confidence TEXT NOT NULL CHECK (
                    confidence IN ('Approved', 'High', 'Medium', 'Low')
                ),
                state TEXT NOT NULL CHECK (state IN ('ready', 'review', 'missing')),
                lyrics_status TEXT NOT NULL,
                review_reason TEXT,
                first_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX library_tracks_root_state
            ON library_tracks(root_path, state)
            """,
        ),
    ),
    Migration(
        9,
        "user-approved lyric document language routing",
        (
            """
            CREATE TABLE lyric_document_language_overrides (
                document_id TEXT PRIMARY KEY
                    REFERENCES lyrics_documents(document_id) ON DELETE CASCADE,
                language_code TEXT NOT NULL CHECK (language_code IN ('zh', 'ja')),
                provenance TEXT NOT NULL CHECK (provenance = 'user-approved'),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        ),
    ),
    Migration(
        10,
        "canonical configuration migration marker",
        (
            """
            CREATE TABLE canonical_config_migrations (
                migration_id INTEGER PRIMARY KEY CHECK (migration_id = 1),
                config_schema_version INTEGER NOT NULL CHECK (
                    config_schema_version = 1
                ),
                migrated_at TEXT NOT NULL
            )
            """,
        ),
    ),
    Migration(
        11,
        "distinguish empty representation generation results",
        (
            """
            ALTER TABLE lyric_representation_candidate_diagnostics
            RENAME TO lyric_representation_candidate_diagnostics_v10
            """,
            """
            ALTER TABLE lyric_representation_candidates
            RENAME TO lyric_representation_candidates_v10
            """,
            "DROP INDEX IF EXISTS lyric_representation_candidates_lookup",
            """
            CREATE TABLE lyric_representation_candidates (
                candidate_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL
                    REFERENCES lyrics_documents(document_id) ON DELETE CASCADE,
                source_line_id TEXT NOT NULL,
                representation_kind TEXT NOT NULL CHECK (
                    representation_kind IN ('romanized', 'transliterated', 'translated')
                ),
                candidate_status TEXT NOT NULL CHECK (
                    candidate_status IN (
                        'available', 'generated-empty', 'unavailable', 'failed'
                    )
                ),
                candidate_text TEXT,
                language TEXT,
                script TEXT,
                provenance TEXT NOT NULL CHECK (
                    provenance IN ('provider', 'local', 'imported', 'generated', 'user')
                ),
                source_name TEXT NOT NULL CHECK (length(source_name) > 0),
                source_version TEXT,
                approval_state TEXT NOT NULL CHECK (
                    approval_state IN ('unreviewed', 'approved', 'rejected')
                ),
                uncertainty TEXT NOT NULL CHECK (
                    uncertainty IN ('none', 'ambiguous')
                ),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (
                    (candidate_status = 'available' AND candidate_text IS NOT NULL)
                    OR (candidate_status != 'available')
                )
            )
            """,
            """
            INSERT INTO lyric_representation_candidates(
                candidate_id, document_id, source_line_id, representation_kind,
                candidate_status, candidate_text, language, script, provenance,
                source_name, source_version, approval_state, uncertainty,
                created_at, updated_at
            )
            SELECT candidate_id, document_id, source_line_id, representation_kind,
                   candidate_status, candidate_text, language, script, provenance,
                   source_name, source_version, approval_state, uncertainty,
                   created_at, updated_at
            FROM lyric_representation_candidates_v10
            """,
            """
            CREATE INDEX IF NOT EXISTS lyric_representation_candidates_lookup
            ON lyric_representation_candidates(
                document_id, source_line_id, representation_kind, provenance
            )
            """,
            """
            CREATE TABLE lyric_representation_candidate_diagnostics (
                candidate_id TEXT NOT NULL
                    REFERENCES lyric_representation_candidates(candidate_id)
                    ON DELETE CASCADE,
                position INTEGER NOT NULL CHECK (position >= 0),
                diagnostic TEXT NOT NULL CHECK (length(diagnostic) > 0),
                PRIMARY KEY (candidate_id, position)
            )
            """,
            """
            INSERT INTO lyric_representation_candidate_diagnostics(
                candidate_id, position, diagnostic
            )
            SELECT candidate_id, position, diagnostic
            FROM lyric_representation_candidate_diagnostics_v10
            """,
            "DROP TABLE lyric_representation_candidate_diagnostics_v10",
            "DROP TABLE lyric_representation_candidates_v10",
        ),
    ),
    Migration(
        12,
        "truthful library scan outcomes and privacy-safe error counts",
        (
            "ALTER TABLE library_scan_runs RENAME TO library_scan_runs_v11",
            """
            CREATE TABLE library_scan_runs (
                scan_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL CHECK (
                    status IN (
                        'running', 'completed', 'completed-with-errors',
                        'cancelled', 'failed'
                    )
                ),
                started_at TEXT NOT NULL,
                finished_at TEXT,
                discovered INTEGER NOT NULL DEFAULT 0,
                processed INTEGER NOT NULL DEFAULT 0,
                unchanged INTEGER NOT NULL DEFAULT 0,
                moved INTEGER NOT NULL DEFAULT 0,
                missing INTEGER NOT NULL DEFAULT 0,
                review INTEGER NOT NULL DEFAULT 0,
                downloaded INTEGER NOT NULL DEFAULT 0,
                download_misses INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0
            )
            """,
            """
            INSERT INTO library_scan_runs(
                scan_id, status, started_at, finished_at, discovered, processed,
                unchanged, moved, missing, review, downloaded, download_misses,
                errors
            )
            SELECT scan_id, status, started_at, finished_at, discovered, processed,
                   unchanged, moved, missing, review, downloaded, download_misses,
                   errors
            FROM library_scan_runs_v11
            """,
            "DROP TABLE library_scan_runs_v11",
            """
            CREATE TABLE library_scan_error_counts (
                scan_id INTEGER NOT NULL
                    REFERENCES library_scan_runs(scan_id) ON DELETE CASCADE,
                category TEXT NOT NULL CHECK (
                    category IN (
                        'root-unavailable', 'directory-read', 'file-inspection',
                        'metadata-read', 'download', 'storage', 'unknown'
                    )
                ),
                error_count INTEGER NOT NULL CHECK (error_count > 0),
                PRIMARY KEY (scan_id, category)
            )
            """,
        ),
    ),
    Migration(
        13,
        "rich lyric timing hierarchy and provenance",
        (
            """
            ALTER TABLE lyrics_documents ADD COLUMN timing_level TEXT NOT NULL
            DEFAULT 'unsynchronized' CHECK (
                timing_level IN ('unsynchronized', 'line', 'word', 'element')
            )
            """,
            """
            UPDATE lyrics_documents SET timing_level = 'line'
            WHERE document_kind = 'synced'
              AND EXISTS (
                  SELECT 1 FROM lyric_lines
                  WHERE lyric_lines.document_id = lyrics_documents.document_id
                    AND lyric_lines.start_ms IS NOT NULL
              )
            """,
            """
            ALTER TABLE lyric_lines ADD COLUMN timing_provenance_detail TEXT
            CHECK (
                timing_provenance_detail IS NULL
                OR timing_provenance_detail IN (
                    'provider', 'imported', 'user-approved', 'user-edited',
                    'generated', 'estimated'
                )
            )
            """,
            """
            UPDATE lyric_lines SET timing_provenance_detail = CASE
                WHEN timing_provenance = 'manual' THEN 'user-edited'
                ELSE timing_provenance
            END
            WHERE timing_provenance IS NOT NULL
            """,
            """
            CREATE TABLE lyric_timing_segments (
                document_id TEXT NOT NULL,
                line_id TEXT NOT NULL,
                segment_id TEXT NOT NULL,
                position INTEGER NOT NULL CHECK (position >= 0),
                segment_text TEXT NOT NULL,
                start_ms INTEGER NOT NULL CHECK (start_ms >= 0),
                end_ms INTEGER CHECK (end_ms IS NULL OR end_ms >= 0),
                timing_unit TEXT NOT NULL CHECK (
                    timing_unit IN (
                        'word', 'syllable', 'grapheme', 'provider-element'
                    )
                ),
                timing_provenance TEXT CHECK (
                    timing_provenance IS NULL
                    OR timing_provenance IN (
                        'provider', 'imported', 'user-approved', 'user-edited',
                        'generated', 'estimated'
                    )
                ),
                parent_segment_id TEXT,
                provider_unit TEXT,
                PRIMARY KEY (document_id, segment_id),
                UNIQUE (document_id, line_id, position),
                FOREIGN KEY (document_id, line_id)
                    REFERENCES lyric_lines(document_id, line_id) ON DELETE CASCADE,
                FOREIGN KEY (document_id, parent_segment_id)
                    REFERENCES lyric_timing_segments(document_id, segment_id)
                    DEFERRABLE INITIALLY DEFERRED,
                CHECK (end_ms IS NULL OR end_ms >= start_ms),
                CHECK (parent_segment_id IS NULL OR parent_segment_id != segment_id),
                CHECK (
                    timing_unit = 'provider-element'
                    OR provider_unit IS NULL
                )
            )
            """,
        ),
    ),
    Migration(
        14,
        "immutable-source lyric line corrections",
        (
            """
            CREATE TABLE lyric_line_corrections (
                document_id TEXT NOT NULL
                    REFERENCES lyrics_documents(document_id) ON DELETE CASCADE,
                line_id TEXT NOT NULL,
                based_on_text TEXT NOT NULL,
                based_on_start_ms INTEGER CHECK (
                    based_on_start_ms IS NULL OR based_on_start_ms >= 0
                ),
                corrected_text TEXT CHECK (
                    corrected_text IS NULL
                    OR (
                        length(corrected_text) <= 10000
                        AND instr(corrected_text, char(0)) = 0
                        AND instr(corrected_text, char(10)) = 0
                        AND instr(corrected_text, char(13)) = 0
                    )
                ),
                corrected_start_ms INTEGER CHECK (
                    corrected_start_ms IS NULL
                    OR (
                        corrected_start_ms >= 0
                        AND corrected_start_ms <= 604800000
                    )
                ),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (document_id, line_id),
                CHECK (
                    corrected_text IS NOT NULL
                    OR corrected_start_ms IS NOT NULL
                )
            )
            """,
        ),
    ),
)

CURRENT_SCHEMA_VERSION = MIGRATIONS[-1].version
