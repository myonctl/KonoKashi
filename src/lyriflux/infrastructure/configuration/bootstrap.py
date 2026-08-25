"""Composition and one-time migration for canonical settings."""

from __future__ import annotations

from pathlib import Path

from lyriflux.application.settings import (
    SettingOrigin,
    SettingValue,
    default_settings_snapshot,
    validate_settings_values,
)
from lyriflux.application.settings_service import CanonicalSettingsService
from lyriflux.infrastructure.configuration.paths import default_config_path
from lyriflux.infrastructure.configuration.toml_file import TomlSettingsFile
from lyriflux.infrastructure.storage.bootstrap import StorageRepositories


def resolved_config_path(
    *, database_path: Path | None = None, config_path: Path | None = None
) -> Path:
    """Use an isolated sibling config for explicitly isolated databases."""

    if config_path is not None:
        return config_path
    if database_path is not None:
        return database_path.parent / "config.toml"
    return default_config_path()


def open_settings(
    storage: StorageRepositories,
    *,
    config_path: Path | None = None,
    migrate: bool = True,
) -> CanonicalSettingsService:
    """Open canonical settings and idempotently import legacy SQLite values."""

    path = resolved_config_path(
        database_path=storage.database.path if config_path is None else None,
        config_path=config_path,
    )
    if config_path is None and storage.database.path == _default_database_path():
        path = default_config_path()
    adapter = TomlSettingsFile(path)
    already_migrated = migrate and storage.settings.canonical_config_migrated()
    if already_migrated:
        service = CanonicalSettingsService(adapter)
        service.initialize()
        return service
    player = storage.settings.get_player_selection()
    display = storage.settings.get_representation_display()
    interaction = storage.settings.get_desktop_interaction()
    library = storage.library.get_settings()
    legacy_values: dict[str, SettingValue] = {
        "players.preferred": player.preferred_players,
        "players.ignored": player.ignored_players,
        "lyrics.display.original": display.show_original,
        "lyrics.display.romanized": display.show_romanized,
        "lyrics.display.translated": display.show_translated,
        "desktop.lyrics.selectable": interaction.allow_lyric_selection,
        "library.roots": library.roots,
        "library.automatic_downloads": library.automatic_downloads,
        "library.metadata_workers": library.worker_count,
    }
    legacy = validate_settings_values(
        legacy_values,
        explicit_keys=frozenset(legacy_values),
        explicit_origin=SettingOrigin.LEGACY_MIGRATION,
        path=path,
    )
    service = CanonicalSettingsService(adapter, fallback=legacy)
    initial = service.initialize()
    if not migrate:
        return service
    if not initial.applied:
        return service

    defaults = default_settings_snapshot().plain_values()
    adapter.update_many(
        {
            key: value
            for key, value in legacy_values.items()
            if value != defaults[key]
            and initial.snapshot.resolved(key).origin is not SettingOrigin.CONFIG_FILE
        }
    )
    validated = service.reload()
    if validated.applied:
        storage.settings.mark_canonical_config_migrated()
        service = CanonicalSettingsService(adapter)
        service.initialize()
    return service


def _default_database_path() -> Path:
    from lyriflux.infrastructure.storage.paths import default_database_path

    return default_database_path()
