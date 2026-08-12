"""Typed durable settings for accepted Stage 2 player configuration."""

from __future__ import annotations

from lyricflow.application.settings import default_player_selection_config
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
