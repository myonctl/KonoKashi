"""Explicit user-local freedesktop.org desktop integration."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from konokashi import APPLICATION_ID

APP_ID = APPLICATION_ID


class DesktopIntegrationError(RuntimeError):
    """A controlled desktop integration failure."""


@dataclass(frozen=True, slots=True)
class DesktopIntegrationStatus:
    """Presence and ownership state for the two user-local integration files."""

    desktop_file: Path
    icon_file: Path
    desktop_installed: bool
    icon_installed: bool

    @property
    def installed(self) -> bool:
        """Return true only when both integration assets are present."""

        return self.desktop_installed and self.icon_installed


def user_data_home(
    environment: Mapping[str, str] | None = None, *, home: Path | None = None
) -> Path:
    """Resolve the XDG user data directory, ignoring invalid relative values."""

    resolved_environment = os.environ if environment is None else environment
    configured = resolved_environment.get("XDG_DATA_HOME")
    if configured and Path(configured).is_absolute():
        return Path(configured)
    return (Path.home() if home is None else home) / ".local" / "share"


def integration_status(data_home: Path) -> DesktopIntegrationStatus:
    """Inspect exact integration paths without modifying them."""

    desktop_file = data_home / "applications" / f"{APP_ID}.desktop"
    icon_file = data_home / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
    return DesktopIntegrationStatus(
        desktop_file,
        icon_file,
        desktop_file.is_file(),
        icon_file.is_file(),
    )


def _desktop_exec_argument(value: str) -> str:
    """Quote one desktop-entry Exec argument according to its reserved syntax."""

    if any(character in value for character in "\n\r\0"):
        raise DesktopIntegrationError("executable path contains a control character")
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
        .replace("%", "%%")
    )
    return f'"{escaped}"'


def _desktop_string(value: str) -> str:
    """Escape a desktop-entry string value without relying on shell quoting."""

    if "\0" in value:
        raise DesktopIntegrationError("desktop-entry value contains a null character")
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
        .replace(" ", "\\s")
    )


def _resource_bytes(name: str) -> bytes:
    return files("konokashi").joinpath("resources", name).read_bytes()


def _atomic_write(path: Path, payload: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def install_desktop_integration(
    executable: Path, data_home: Path
) -> DesktopIntegrationStatus:
    """Atomically install the packaged user-local launcher and scalable icon."""

    resolved_executable = executable.expanduser().resolve()
    if not resolved_executable.is_file() or not os.access(resolved_executable, os.X_OK):
        raise DesktopIntegrationError(
            "the KonoKashi executable could not be resolved to an executable file"
        )
    status = integration_status(data_home)
    template = _resource_bytes(f"{APP_ID}.desktop.in").decode("utf-8")
    rendered = (
        template.replace(
            "@KONOKASHI_EXECUTABLE@", _desktop_exec_argument(str(resolved_executable))
        )
        .replace(
            "@KONOKASHI_ICON@",
            _desktop_string(str(status.icon_file.expanduser().resolve())),
        )
        .encode("utf-8")
    )
    try:
        _atomic_write(status.icon_file, _resource_bytes(f"{APP_ID}.svg"))
        _atomic_write(status.desktop_file, rendered)
    except OSError as error:
        error_name = error.__class__.__name__
        raise DesktopIntegrationError(
            f"could not install user-local desktop integration ({error_name})"
        ) from error
    return integration_status(data_home)


def remove_desktop_integration(data_home: Path) -> DesktopIntegrationStatus:
    """Remove only the exact launcher and icon owned by KonoKashi."""

    status = integration_status(data_home)
    try:
        status.desktop_file.unlink(missing_ok=True)
        status.icon_file.unlink(missing_ok=True)
    except OSError as error:
        error_name = error.__class__.__name__
        raise DesktopIntegrationError(
            f"could not remove user-local desktop integration ({error_name})"
        ) from error
    return integration_status(data_home)


def current_executable(command: str) -> Path:
    """Resolve the invoked console script without assuming a particular installer."""

    located = shutil.which(command)
    if located is None:
        candidate = Path(command)
        if candidate.is_absolute():
            return candidate
        raise DesktopIntegrationError("could not locate the konokashi console script")
    return Path(located)
