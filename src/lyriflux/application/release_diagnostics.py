"""Privacy-bounded, machine-readable release diagnostics."""

from __future__ import annotations

import json
import platform
import sys
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from lyriflux.application.ports import DiagnosticCheck
from lyriflux.application.storage_diagnostics import StorageStatus


@dataclass(frozen=True, slots=True)
class ExportedCheck:
    """Doctor outcome without path-bearing free-form messages."""

    name: str
    status: str
    required: bool


@dataclass(frozen=True, slots=True)
class ExportedStorage:
    """Aggregate storage health without a database path or stored content."""

    exists: bool
    schema_version: int | None
    current_schema_version: int
    migration_status: str
    readable: bool
    writable: bool
    integrity_status: str
    error: bool
    counts: Mapping[str, int] | None


@dataclass(frozen=True, slots=True)
class ReleaseDiagnosticExport:
    """Versioned diagnostic payload safe to attach to a private bug report."""

    format_version: int
    generated_at: str
    lyriflux_version: str
    python_version: str
    platform: str
    machine: str
    checks: tuple[ExportedCheck, ...]
    storage: ExportedStorage
    desktop_integration_installed: bool
    dependencies: Mapping[str, str]

    def render_json(self) -> str:
        """Render stable indented JSON with no host/user/media identifiers."""

        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


def build_release_diagnostic_export(
    *,
    lyriflux_version: str,
    checks: Iterable[DiagnosticCheck],
    storage: StorageStatus,
    desktop_integration_installed: bool,
    dependencies: Mapping[str, str],
    generated_at: datetime | None = None,
    platform_name: str | None = None,
    machine: str | None = None,
    python_version: str | None = None,
) -> ReleaseDiagnosticExport:
    """Build an export while deliberately dropping free-form/path-bearing fields."""

    counts = None if storage.counts is None else asdict(storage.counts)
    timestamp = generated_at or datetime.now(UTC)
    return ReleaseDiagnosticExport(
        format_version=1,
        generated_at=timestamp.astimezone(UTC).isoformat(),
        lyriflux_version=lyriflux_version,
        python_version=python_version or platform.python_version(),
        platform=platform_name or sys.platform,
        machine=machine or platform.machine(),
        checks=tuple(
            ExportedCheck(check.name, check.status.value, check.required)
            for check in checks
        ),
        storage=ExportedStorage(
            exists=storage.exists,
            schema_version=storage.schema_version,
            current_schema_version=storage.current_schema_version,
            migration_status=storage.migration_status,
            readable=storage.readable,
            writable=storage.writable,
            integrity_status=storage.integrity_status,
            error=storage.error is not None,
            counts=counts,
        ),
        desktop_integration_installed=desktop_integration_installed,
        dependencies=dict(sorted(dependencies.items())),
    )
