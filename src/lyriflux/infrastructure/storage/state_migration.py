"""Non-destructive migration from the legacy LyricFlow XDG namespace."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from lyriflux.infrastructure.storage.backup import backup_database
from lyriflux.infrastructure.storage.diagnostics import inspect_storage
from lyriflux.infrastructure.storage.errors import StorageError, StorageMigrationError
from lyriflux.infrastructure.storage.migrations import CURRENT_SCHEMA_VERSION
from lyriflux.infrastructure.storage.sqlite import SQLiteDatabase

LEGACY_APPLICATION_DIRECTORY = "lyricflow"
APPLICATION_DIRECTORY = "lyriflux"
LEGACY_DATABASE_FILENAME = "lyricflow.sqlite3"
DATABASE_FILENAME = "lyriflux.sqlite3"
MIGRATION_MARKER_FILENAME = ".lyriflux-legacy-migration.json"
_MIGRATION_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class NamespaceMigration:
    """One XDG namespace's resolved migration state."""

    kind: str
    legacy_directory: Path
    current_directory: Path
    state: str


def _xdg_home(kind: str, environment: Mapping[str, str], home: Path) -> Path:
    variable, fallback = {
        "config": ("XDG_CONFIG_HOME", home / ".config"),
        "data": ("XDG_DATA_HOME", home / ".local" / "share"),
        "cache": ("XDG_CACHE_HOME", home / ".cache"),
    }[kind]
    configured = environment.get(variable)
    if configured and Path(configured).is_absolute():
        return Path(configured)
    return fallback


def xdg_state_directories(
    *,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> tuple[tuple[str, Path, Path], ...]:
    """Return legacy/current config, data, and cache directories without writes."""

    values = os.environ if environment is None else environment
    resolved_home = Path.home() if home is None else home
    return tuple(
        (
            kind,
            _xdg_home(kind, values, resolved_home) / LEGACY_APPLICATION_DIRECTORY,
            _xdg_home(kind, values, resolved_home) / APPLICATION_DIRECTORY,
        )
        for kind in ("config", "data", "cache")
    )


def _directory_fingerprint(directory: Path) -> str:
    """Fingerprint names and stable metadata while rejecting unsafe entries."""

    if directory.is_symlink() or not directory.is_dir():
        raise StorageMigrationError(
            "legacy state path is not a normal directory; it was not migrated",
            path=directory,
        )
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise StorageMigrationError(
                "legacy state contains a symbolic link; it was not migrated",
                path=path,
            )
        metadata = path.stat()
        if path.is_dir():
            entry_kind = "d"
        elif stat.S_ISREG(metadata.st_mode):
            entry_kind = "f"
        else:
            raise StorageMigrationError(
                "legacy state contains a non-regular entry; it was not migrated",
                path=path,
            )
        relative = path.relative_to(directory).as_posix()
        digest.update(
            f"{entry_kind}\0{relative}\0{metadata.st_size}\0"
            f"{metadata.st_mtime_ns}\0{stat.S_IMODE(metadata.st_mode)}\n".encode()
        )
    return digest.hexdigest()


def _marker_payload(kind: str, fingerprint: str) -> dict[str, object]:
    return {
        "format_version": _MIGRATION_FORMAT_VERSION,
        "legacy_namespace": LEGACY_APPLICATION_DIRECTORY,
        "current_namespace": APPLICATION_DIRECTORY,
        "kind": kind,
        "legacy_fingerprint": fingerprint,
        "legacy_retained": True,
    }


def _is_verified_prior_migration(
    kind: str, legacy: Path, current: Path, fingerprint: str
) -> bool:
    marker = current / MIGRATION_MARKER_FILENAME
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return payload == _marker_payload(kind, fingerprint) and legacy.is_dir()


_USER_DATA_TABLES = (
    "source_identities",
    "track_overrides",
    "lyrics_documents",
    "lyrics_matches",
    "lyrics_match_rejections",
    "provider_cache",
    "lyric_representation_candidates",
    "lyric_representation_decisions",
    "lyric_document_language_overrides",
    "lyric_document_timing",
    "audio_output_calibrations",
    "library_roots",
    "library_tracks",
    "library_scan_runs",
)


def _durable_counts(path: Path) -> dict[str, int]:
    with SQLiteDatabase(path).connection(readonly=True) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        return {
            table: int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for table in _USER_DATA_TABLES
            if table in tables
        }


def _validate_legacy_database(path: Path) -> dict[str, int]:
    status = inspect_storage(path)
    if (
        status.error is not None
        or status.schema_version
        not in {
            CURRENT_SCHEMA_VERSION,
            CURRENT_SCHEMA_VERSION - 1,
        }
        or status.integrity_status != "ok"
    ):
        raise StorageMigrationError(
            "legacy LyricFlow database is not healthy and current or safely "
            "migratable; both the legacy state and any existing LyriFlux state "
            "were left unchanged",
            path=path,
        )
    return _durable_counts(path)


def _prepare_data_copy(legacy: Path, temporary: Path) -> None:
    legacy_database = legacy / LEGACY_DATABASE_FILENAME
    if not legacy_database.exists():
        return
    expected_counts = _validate_legacy_database(legacy_database)
    for suffix in ("", "-journal", "-shm", "-wal"):
        (temporary / f"{LEGACY_DATABASE_FILENAME}{suffix}").unlink(missing_ok=True)
    current_database = temporary / DATABASE_FILENAME
    backup_database(SQLiteDatabase(legacy_database), current_database)
    SQLiteDatabase(current_database).initialize()
    migrated = inspect_storage(current_database)
    if (
        migrated.error is not None
        or migrated.migration_status != "current"
        or migrated.integrity_status != "ok"
        or _durable_counts(current_database) != expected_counts
    ):
        raise StorageMigrationError(
            "migrated LyriFlux database did not validate; legacy state was retained",
            path=current_database,
        )


def _migrate_namespace(kind: str, legacy: Path, current: Path) -> None:
    fingerprint = _directory_fingerprint(legacy)
    current.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{APPLICATION_DIRECTORY}-migration-", dir=current.parent
        )
    )
    temporary.rmdir()
    try:
        shutil.copytree(legacy, temporary, copy_function=shutil.copy2)
        if kind == "data":
            _prepare_data_copy(legacy, temporary)
        if _directory_fingerprint(legacy) != fingerprint:
            raise StorageMigrationError(
                "legacy state changed during migration; no LyriFlux state was "
                "published",
                path=legacy,
            )
        marker = temporary / MIGRATION_MARKER_FILENAME
        marker.write_text(
            json.dumps(_marker_payload(kind, fingerprint), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        if current.exists():
            raise StorageMigrationError(
                "LyriFlux state appeared during migration; neither state was "
                "overwritten",
                path=current,
            )
        temporary.replace(current)
    except (OSError, StorageError) as error:
        shutil.rmtree(temporary, ignore_errors=True)
        if isinstance(error, StorageMigrationError):
            raise
        raise StorageMigrationError(
            f"legacy state migration failed ({error.__class__.__name__}); "
            "legacy state was retained",
            path=legacy,
        ) from error


def migrate_legacy_xdg_state(
    *,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> tuple[NamespaceMigration, ...]:
    """Copy legacy XDG state once and diagnose ambiguous dual-state layouts."""

    directories = xdg_state_directories(environment=environment, home=home)
    for kind, legacy, current in directories:
        if not legacy.exists():
            continue
        fingerprint = _directory_fingerprint(legacy)
        if current.exists() and not _is_verified_prior_migration(
            kind, legacy, current, fingerprint
        ):
            raise StorageMigrationError(
                "both legacy LyricFlow and current LyriFlux state exist without "
                "a matching migration marker; neither state was changed",
                path=current,
            )

    outcomes: list[NamespaceMigration] = []
    for kind, legacy, current in directories:
        legacy_exists = legacy.exists()
        current_exists = current.exists()
        if not legacy_exists and not current_exists:
            state = "absent"
        elif not legacy_exists:
            state = "current"
        elif not current_exists:
            _migrate_namespace(kind, legacy, current)
            state = "migrated"
        else:
            state = "migrated"
        outcomes.append(NamespaceMigration(kind, legacy, current, state))
    return tuple(outcomes)
