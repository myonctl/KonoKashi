"""Composition and bounded filesystem observation for the settings TUI."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from lyriflux.application.settings_service import CanonicalSettingsService
from lyriflux.infrastructure.configuration.bootstrap import open_settings
from lyriflux.infrastructure.storage.bootstrap import open_storage


@dataclass(frozen=True, slots=True)
class ConfigSignature:
    """Cheap identity evidence for a path and its possible symlink target."""

    link: tuple[object, ...]
    target: tuple[object, ...] | None


def config_signature(path: Path) -> ConfigSignature:
    """Observe create/remove/replace and symlink-target changes without reading TOML."""

    try:
        link_stat = path.lstat()
    except FileNotFoundError:
        return ConfigSignature(("missing",), None)
    except OSError as error:
        return ConfigSignature(("error", error.errno, str(error)), None)
    link = _stat_identity(link_stat)
    if not stat.S_ISLNK(link_stat.st_mode):
        return ConfigSignature(link, link)
    try:
        link_target = os.readlink(path)
    except OSError as error:
        return ConfigSignature(link, ("error", error.errno, str(error)))
    try:
        target_stat = path.stat()
        target = (*_stat_identity(target_stat), link_target)
    except FileNotFoundError:
        target = ("missing-target", link_target)
    except OSError as error:
        target = ("error", error.errno, str(error))
    return ConfigSignature(link, target)


def open_settings_service(
    database_path: Path | None = None,
    config_path: Path | None = None,
) -> CanonicalSettingsService:
    """Construct the canonical service through the existing migration boundary."""

    storage = open_storage(database_path)
    return open_settings(storage, config_path=config_path)


def _stat_identity(value: os.stat_result) -> tuple[object, ...]:
    return (
        "present",
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )
