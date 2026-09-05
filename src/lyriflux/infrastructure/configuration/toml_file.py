"""Bounded, comment-preserving, atomic TOML configuration persistence."""

from __future__ import annotations

import fcntl
import os
import stat
import tempfile
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock

import tomlkit
from tomlkit.container import Container
from tomlkit.items import Table

from lyriflux.application.settings import SettingsDiagnostic, SettingValue
from lyriflux.application.settings_service import SettingsFileError

_MAX_CONFIG_BYTES = 1_048_576
_PROCESS_LOCKS_GUARD = Lock()
_PROCESS_LOCKS: dict[Path, RLock] = {}


def _process_lock(path: Path) -> RLock:
    normalized = Path(os.path.abspath(path))
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(normalized, RLock())


class TomlSettingsFile:
    """Read and edit the supported TOML subset without discarding user layout."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._transaction_depth = 0

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> Mapping[str, object]:
        target = self._read_target()
        if target is None:
            return {}
        try:
            payload = target.read_bytes()
        except OSError as error:
            raise self._error(f"Could not read file: {error}") from error
        if len(payload) > _MAX_CONFIG_BYTES:
            raise self._error(
                f"File exceeds the {_MAX_CONFIG_BYTES}-byte safety limit."
            )
        try:
            return tomllib.loads(payload.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise self._error("File must be valid UTF-8.") from error
        except tomllib.TOMLDecodeError as error:
            raise self._error(f"Invalid TOML: {error}") from error

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Serialize one read/validate/write cycle across threads and processes."""

        target = self._read_target() or self._path
        lock_path = target.parent / f".{target.name}.lock"
        with _process_lock(lock_path):
            if self._transaction_depth:
                self._transaction_depth += 1
                try:
                    yield
                finally:
                    self._transaction_depth -= 1
                return

            descriptor: int | None = None
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(
                    lock_path,
                    os.O_RDWR
                    | os.O_CREAT
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise OSError("settings lock path is not a regular file")
                os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except OSError as error:
                if descriptor is not None:
                    os.close(descriptor)
                raise self._error(f"Could not lock configuration: {error}") from error

            assert descriptor is not None
            self._transaction_depth = 1
            try:
                yield
            finally:
                self._transaction_depth = 0
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    def update_many(self, values: Mapping[str, SettingValue]) -> bool:
        with self.transaction():
            document = self._editable_document()
            changed = False
            for key, value in values.items():
                parts = key.split(".")
                table: Container | Table = document
                for name in parts[:-1]:
                    child = table.get(name)
                    if child is None:
                        child = tomlkit.table()
                        table.add(name, child)
                    if not isinstance(child, (Table, Container)):
                        raise self._error(
                            f"Cannot create `{key}` because `{name}` is not a table."
                        )
                    table = child
                encoded: bool | int | list[str]
                encoded = list(value) if isinstance(value, tuple) else value
                if table.get(parts[-1]) != encoded:
                    table[parts[-1]] = encoded
                    changed = True
            if changed:
                self._write(document.as_string())
            return changed

    def reset(self, key: str) -> bool:
        with self.transaction():
            document = self._editable_document()
            parents: list[tuple[Container | Table, str, Container | Table]] = []
            table: Container | Table = document
            parts = key.split(".")
            for name in parts[:-1]:
                child = table.get(name)
                if not isinstance(child, (Table, Container)):
                    return False
                parents.append((table, name, child))
                table = child
            if parts[-1] not in table:
                return False
            del table[parts[-1]]
            for parent, name, child in reversed(parents):
                if len(child) != 0:
                    break
                del parent[name]
            self._write(document.as_string())
            return True

    def render(self, values: Mapping[str, SettingValue]) -> str:
        document = tomlkit.document()
        document.add("schema_version", 1)
        self._populate(document, values)
        return document.as_string()

    def _editable_document(self) -> tomlkit.TOMLDocument:
        target = self._read_target()
        if target is None:
            document = tomlkit.document()
            document.add("schema_version", 1)
            return document
        try:
            payload = target.read_bytes()
            if len(payload) > _MAX_CONFIG_BYTES:
                raise self._error(
                    f"File exceeds the {_MAX_CONFIG_BYTES}-byte safety limit."
                )
            return tomlkit.parse(payload.decode("utf-8"))
        except SettingsFileError:
            raise
        except (OSError, UnicodeError, tomlkit.exceptions.ParseError) as error:
            raise self._error(f"Could not edit valid UTF-8 TOML: {error}") from error

    def _read_target(self) -> Path | None:
        try:
            if self._path.is_symlink():
                target = self._path.resolve(strict=True)
                mode = target.stat().st_mode
                if not stat.S_ISREG(mode):
                    raise self._error("Symbolic-link target must be a regular file.")
                return target
            if not self._path.exists():
                return None
            if not stat.S_ISREG(self._path.stat().st_mode):
                raise self._error("Path must be a regular file.")
            return self._path
        except FileNotFoundError as error:
            raise self._error("Symbolic link is dangling.") from error
        except OSError as error:
            raise self._error(f"Could not inspect path: {error}") from error

    def _write(self, content: str) -> None:
        payload = content.encode("utf-8")
        if len(payload) > _MAX_CONFIG_BYTES:
            raise self._error(
                f"Result exceeds the {_MAX_CONFIG_BYTES}-byte safety limit."
            )
        target = self._read_target() or self._path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.", dir=target.parent
            )
            temporary = Path(temporary_name)
            descriptor_open = True
            try:
                os.fchmod(descriptor, mode)
                with os.fdopen(descriptor, "wb") as stream:
                    descriptor_open = False
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
                directory = os.open(
                    target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                )
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if descriptor_open:
                    os.close(descriptor)
                temporary.unlink(missing_ok=True)
        except OSError as error:
            raise self._error(f"Could not write atomically: {error}") from error

    def _populate(
        self, document: tomlkit.TOMLDocument, values: Mapping[str, SettingValue]
    ) -> None:
        for key, value in values.items():
            parts = key.split(".")
            table: Container | Table = document
            for name in parts[:-1]:
                child = table.get(name)
                if child is None:
                    child = tomlkit.table()
                    table.add(name, child)
                if not isinstance(child, (Table, Container)):
                    raise self._error(f"Cannot render table `{name}`.")
                table = child
            encoded = list(value) if isinstance(value, tuple) else value
            table[parts[-1]] = encoded

    def _error(self, message: str) -> SettingsFileError:
        return SettingsFileError(SettingsDiagnostic(message, path=self._path))
