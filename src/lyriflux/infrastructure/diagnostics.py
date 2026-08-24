"""Local Linux desktop prerequisite checks used by ``lyriflux doctor``."""

from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType

from lyriflux.application.ports import DiagnosticCheck, DiagnosticStatus
from lyriflux.infrastructure.storage.errors import StorageMigrationError
from lyriflux.infrastructure.storage.state_migration import migrate_legacy_xdg_state

ModuleImporter = Callable[[str], ModuleType]
CommandLocator = Callable[[str], str | None]
DirectoryProbe = Callable[[Path], str | None]


def _probe_writable_directory(path: Path) -> str | None:
    """Create an app directory and verify a temporary file can be written."""

    try:
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            return "path exists but is not a directory"
        descriptor, probe_name = tempfile.mkstemp(prefix=".write-test-", dir=path)
        os.close(descriptor)
        Path(probe_name).unlink()
    except OSError as error:
        return f"{error.__class__.__name__}: {error.strerror or error}"
    return None


def _directory_paths(
    environment: Mapping[str, str], home: Path
) -> tuple[tuple[str, Path], ...]:
    defaults = {
        "config-dir": home / ".config",
        "data-dir": home / ".local" / "share",
        "cache-dir": home / ".cache",
    }
    variables = {
        "config-dir": "XDG_CONFIG_HOME",
        "data-dir": "XDG_DATA_HOME",
        "cache-dir": "XDG_CACHE_HOME",
    }
    return tuple(
        (
            name,
            Path(environment.get(variables[name]) or default) / "lyriflux",
        )
        for name, default in defaults.items()
    )


def collect_local_diagnostics(
    *,
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
    module_importer: ModuleImporter = importlib.import_module,
    command_locator: CommandLocator = shutil.which,
    directory_probe: DirectoryProbe = _probe_writable_directory,
) -> tuple[DiagnosticCheck, ...]:
    """Check Stage 0 local prerequisites without discovering media players."""

    resolved_platform = sys.platform if platform_name is None else platform_name
    resolved_environment = os.environ if environment is None else environment
    resolved_home = Path.home() if home is None else home
    checks: list[DiagnosticCheck] = []

    migration_problem: str | None = None
    try:
        migrate_legacy_xdg_state(
            environment=resolved_environment,
            home=resolved_home,
        )
    except StorageMigrationError as error:
        migration_problem = str(error)
        checks.append(
            DiagnosticCheck(
                "legacy-state-migration",
                DiagnosticStatus.FAILURE,
                str(error),
            )
        )

    if resolved_platform.startswith("linux"):
        checks.append(
            DiagnosticCheck("platform", DiagnosticStatus.OK, "Linux detected")
        )
    else:
        checks.append(
            DiagnosticCheck(
                "platform",
                DiagnosticStatus.FAILURE,
                f"Linux is required (detected {resolved_platform})",
            )
        )

    if resolved_environment.get("DBUS_SESSION_BUS_ADDRESS"):
        checks.append(
            DiagnosticCheck(
                "session-dbus",
                DiagnosticStatus.OK,
                "DBUS_SESSION_BUS_ADDRESS is set",
            )
        )
    else:
        checks.append(
            DiagnosticCheck(
                "session-dbus",
                DiagnosticStatus.FAILURE,
                "DBUS_SESSION_BUS_ADDRESS is not set",
            )
        )

    try:
        module_importer("PySide6.QtDBus")
    except (ImportError, OSError) as error:
        checks.append(
            DiagnosticCheck(
                "pyside6-qtdbus",
                DiagnosticStatus.FAILURE,
                f"PySide6.QtDBus is unavailable ({error.__class__.__name__})",
            )
        )
    else:
        checks.append(
            DiagnosticCheck(
                "pyside6-qtdbus",
                DiagnosticStatus.OK,
                "PySide6.QtDBus is importable",
            )
        )

    for name, path in _directory_paths(resolved_environment, resolved_home):
        if migration_problem is not None:
            checks.append(
                DiagnosticCheck(
                    name,
                    DiagnosticStatus.FAILURE,
                    "not probed because legacy-state migration requires attention",
                )
            )
            continue
        problem = directory_probe(path)
        if problem is None:
            checks.append(
                DiagnosticCheck(name, DiagnosticStatus.OK, f"writable ({path})")
            )
        else:
            checks.append(
                DiagnosticCheck(
                    name,
                    DiagnosticStatus.FAILURE,
                    f"not writable ({path}): {problem}",
                )
            )

    playerctl_path = command_locator("playerctl")
    if playerctl_path is None:
        checks.append(
            DiagnosticCheck(
                "playerctl",
                DiagnosticStatus.WARNING,
                "not found (optional debugging tool)",
                required=False,
            )
        )
    else:
        checks.append(
            DiagnosticCheck(
                "playerctl",
                DiagnosticStatus.OK,
                f"available for optional debugging ({playerctl_path})",
                required=False,
            )
        )

    return tuple(checks)
