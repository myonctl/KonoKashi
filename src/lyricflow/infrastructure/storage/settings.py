"""Typed durable settings for accepted Stage 2 player configuration."""

from __future__ import annotations

from lyricflow.application.settings import (
    default_player_selection_config,
    default_representation_display_settings,
)
from lyricflow.domain.representations import RepresentationDisplaySettings
from lyricflow.domain.tracks import PlayerSelectionConfig
from lyricflow.infrastructure.storage.errors import (
    InvalidStoredDataError,
    StorageValidationError,
)
from lyricflow.infrastructure.storage.sqlite import SQLiteDatabase, utc_now_text


def _validate_names(names: tuple[str, ...], label: str) -> None:
    if any(not name.strip() for name in names):
        raise StorageValidationError(f"{label} player names must not be blank")
    if len(set(names)) != len(names):
        raise StorageValidationError(
            f"{label} player names must not contain duplicates"
        )


class SQLiteSettingsRepository:
    """Store only the currently accepted preferred/ignored player subset."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def get_player_selection(self) -> PlayerSelectionConfig:
        """Return durable player settings or a separately defined default."""

        with self._database.connection(readonly=True) as connection:
            header = connection.execute(
                "SELECT format_version FROM settings WHERE settings_id = 1"
            ).fetchone()
            if header is None:
                return default_player_selection_config()
            if int(header["format_version"]) != 1:
                raise InvalidStoredDataError(
                    "stored player settings use an unsupported format"
                )
            rows = connection.execute(
                """
                SELECT setting_kind, player_name
                FROM player_setting_entries
                WHERE settings_id = 1
                ORDER BY setting_kind, position
                """
            ).fetchall()
        preferred: list[str] = []
        ignored: list[str] = []
        for row in rows:
            kind = str(row["setting_kind"])
            if kind == "preferred":
                preferred.append(str(row["player_name"]))
            elif kind == "ignored":
                ignored.append(str(row["player_name"]))
            else:
                raise InvalidStoredDataError("stored player setting kind is invalid")
        return PlayerSelectionConfig(tuple(preferred), tuple(ignored))

    def put_player_selection(self, config: PlayerSelectionConfig) -> None:
        """Atomically replace preferred and ignored player configuration."""

        _validate_names(config.preferred_players, "preferred")
        _validate_names(config.ignored_players, "ignored")
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO settings(settings_id, format_version, updated_at)
                VALUES (1, 1, ?)
                ON CONFLICT(settings_id) DO UPDATE SET
                    format_version = excluded.format_version,
                    updated_at = excluded.updated_at
                """,
                (utc_now_text(),),
            )
            connection.execute(
                "DELETE FROM player_setting_entries WHERE settings_id = 1"
            )
            entries = [
                (1, "preferred", position, player)
                for position, player in enumerate(config.preferred_players)
            ]
            entries.extend(
                (1, "ignored", position, player)
                for position, player in enumerate(config.ignored_players)
            )
            connection.executemany(
                """
                INSERT INTO player_setting_entries(
                    settings_id, setting_kind, position, player_name
                ) VALUES (?, ?, ?, ?)
                """,
                entries,
            )

    def get_representation_display(self) -> RepresentationDisplaySettings:
        """Return durable layer toggles or the Stage 5 defaults."""

        with self._database.connection(readonly=True) as connection:
            row = connection.execute(
                """
                SELECT show_original, show_romanized, show_translated
                FROM representation_display_settings WHERE settings_id = 1
                """
            ).fetchone()
        if row is None:
            return default_representation_display_settings()
        values = tuple(
            row[name] for name in ("show_original", "show_romanized", "show_translated")
        )
        if any(value not in (0, 1) for value in values):
            raise InvalidStoredDataError(
                "stored representation display setting is invalid"
            )
        return RepresentationDisplaySettings(*(bool(value) for value in values))

    def put_representation_display(
        self, settings: RepresentationDisplaySettings
    ) -> None:
        """Atomically persist independent layer toggles."""

        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO representation_display_settings(
                    settings_id, show_original, show_romanized,
                    show_translated, updated_at
                ) VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(settings_id) DO UPDATE SET
                    show_original = excluded.show_original,
                    show_romanized = excluded.show_romanized,
                    show_translated = excluded.show_translated,
                    updated_at = excluded.updated_at
                """,
                (
                    int(settings.show_original),
                    int(settings.show_romanized),
                    int(settings.show_translated),
                    utc_now_text(),
                ),
            )
