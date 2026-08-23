"""Installed-distribution metadata for the bounded diagnostic export."""

from __future__ import annotations

import os
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_RUNTIME_DISTRIBUTIONS = (
    "PySide6",
    "cutlet",
    "fugashi",
    "httpx",
    "mutagen",
    "pypinyin",
    "PyICU",
    "unidic-lite",
)


def runtime_dependency_versions() -> dict[str, str]:
    """Return package versions without importing optional platform services."""

    versions: dict[str, str] = {}
    for distribution in _RUNTIME_DISTRIBUTIONS:
        try:
            versions[distribution] = version(distribution)
        except PackageNotFoundError:
            versions[distribution] = "not installed"
    return versions


def write_diagnostic_export(
    destination: Path, payload: str, *, overwrite: bool = False
) -> Path:
    """Atomically write one export, refusing replacement unless explicit."""

    resolved = destination.expanduser().resolve()
    if resolved.exists() and not overwrite:
        raise FileExistsError("diagnostic export destination already exists")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{resolved.name}.", suffix=".tmp", dir=resolved.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        temporary.replace(resolved)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return resolved
