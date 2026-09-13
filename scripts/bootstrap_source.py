#!/usr/bin/env python3
"""Build an isolated, user-owned KonoKashi source installation."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENVIRONMENT = PROJECT_ROOT / ".venv"


class BootstrapError(RuntimeError):
    """A controlled source-bootstrap failure."""


@dataclass(frozen=True, slots=True)
class Distribution:
    """Small, non-speculative subset of os-release identity."""

    identifier: str
    like: tuple[str, ...]
    display_name: str

    @property
    def arch_family(self) -> bool:
        return self.identifier in {"arch", "artix"} or "arch" in self.like


@dataclass(frozen=True, slots=True)
class SystemCheck:
    """One prerequisite result and its known Arch package owner."""

    label: str
    available: bool
    arch_packages: tuple[str, ...]
    detail: str
    optional: bool = False


def _parse_os_release_payload(payload: str) -> Distribution:
    values: dict[str, str] = {}
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    identifier = values.get("ID", "unknown").lower()
    like = tuple(item.lower() for item in values.get("ID_LIKE", "").split())
    display_name = values.get("PRETTY_NAME") or values.get("NAME") or identifier
    return Distribution(identifier, like, display_name)


def detect_distribution(path: Path = Path("/etc/os-release")) -> Distribution:
    try:
        payload = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return Distribution("unknown", (), "Unknown Linux distribution")
    return _parse_os_release_payload(payload)


def _command_available(command: str) -> bool:
    return shutil.which(command) is not None


def _pkg_config_has(module: str) -> bool:
    if not _command_available("pkg-config"):
        return False
    try:
        return (
            subprocess.run(
                ["pkg-config", "--exists", module],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )
    except OSError:
        return False


def _venv_available(python: Path) -> bool:
    return (
        subprocess.run(
            [str(python), "-m", "venv", "--help"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def _layer_shell_sdk_available() -> bool:
    layer_shell = any(
        (prefix / "include" / "LayerShellQt" / "Window").is_file()
        and (prefix / "lib" / "libLayerShellQtInterface.so").is_file()
        for prefix in (Path("/usr"), Path("/app"))
    )
    qt_headers = any(
        (root / "QtCore" / "QObject").is_file()
        for root in (Path("/usr/include/qt6"), Path("/usr/include"))
    )
    return layer_shell and qt_headers


def collect_system_checks(python: Path) -> tuple[SystemCheck, ...]:
    """Inspect only; package installation is deliberately outside this helper."""

    try:
        python_supported = (
            subprocess.run(
                [
                    str(python),
                    "-c",
                    (
                        "import sys; raise SystemExit("
                        "not ((3, 11) <= sys.version_info[:2] < (3, 15)))"
                    ),
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )
    except OSError:
        python_supported = False
    return (
        SystemCheck("Python 3.11-3.14", python_supported, ("python",), str(python)),
        SystemCheck(
            "Python venv module",
            python_supported and _venv_available(python),
            ("python",),
            "python -m venv",
        ),
        SystemCheck(
            "C++20 compiler",
            _command_available("c++"),
            ("gcc",),
            shutil.which("c++") or "c++ not found",
        ),
        SystemCheck(
            "pkg-config",
            _command_available("pkg-config"),
            ("pkgconf",),
            shutil.which("pkg-config") or "pkg-config not found",
        ),
        SystemCheck(
            "OpenSSL development files",
            _pkg_config_has("openssl"),
            ("openssl",),
            "pkg-config openssl",
        ),
        SystemCheck(
            "ICU development files",
            _pkg_config_has("icu-uc"),
            ("icu",),
            "pkg-config icu-uc",
        ),
        SystemCheck(
            "LayerShellQt development files",
            _layer_shell_sdk_available(),
            ("layer-shell-qt",),
            "enhanced locked Wayland overlay",
            optional=True,
        ),
    )


def _arch_install_command(
    checks: Iterable[SystemCheck], *, include_optional: bool = False
) -> str | None:
    packages = sorted(
        {
            package
            for check in checks
            if not check.available and (include_optional or not check.optional)
            for package in check.arch_packages
        }
    )
    if not packages:
        return None
    return shlex.join(("sudo", "pacman", "-S", "--needed", *packages))


def _validate_environment_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    forbidden = {Path("/"), Path.home().resolve(), PROJECT_ROOT.resolve()}
    if resolved in forbidden:
        raise BootstrapError("refusing to use a broad or project-root environment path")
    if resolved.exists() and not (resolved / "pyvenv.cfg").is_file():
        raise BootstrapError(
            "environment path already exists but is not a virtual environment: "
            f"{resolved}"
        )
    return resolved


def _run(command: list[str], *, environment: Mapping[str, str] | None = None) -> None:
    print(f"+ {shlex.join(command)}", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, env=environment, check=True)


def _desktop_choice(arguments: argparse.Namespace) -> bool:
    if arguments.desktop_integration is not None:
        return bool(arguments.desktop_integration)
    if not sys.stdin.isatty():
        return False
    answer = input("Install or refresh the user application launcher? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="supported Python interpreter used to create the environment",
    )
    parser.add_argument(
        "--venv",
        type=Path,
        default=DEFAULT_ENVIRONMENT,
        help="virtual-environment path (default: repository .venv)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="inspect prerequisites without creating or changing files",
    )
    parser.add_argument(
        "--dev", action="store_true", help="also install contributor tools"
    )
    desktop = parser.add_mutually_exclusive_group()
    desktop.add_argument(
        "--desktop-integration",
        dest="desktop_integration",
        action="store_true",
        help="install or refresh the user-local application launcher",
    )
    desktop.add_argument(
        "--no-desktop-integration",
        dest="desktop_integration",
        action="store_false",
        help="leave user-local application launcher files unchanged",
    )
    parser.set_defaults(desktop_integration=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    python = arguments.python.expanduser().resolve()
    distribution = detect_distribution()
    print(f"KonoKashi source setup — {distribution.display_name}")
    checks = collect_system_checks(python)
    for check in checks:
        state = (
            "optional-missing"
            if check.optional and not check.available
            else ("ok" if check.available else "missing")
        )
        print(f"[{state}] {check.label}: {check.detail}")
    missing = tuple(
        check for check in checks if not check.available and not check.optional
    )
    if missing:
        if distribution.arch_family:
            command = _arch_install_command(missing)
            if command is not None:
                print("Install the missing Arch/Artix packages yourself, then rerun:")
                print(f"  {command}")
        else:
            labels = ", ".join(check.label for check in missing)
            print(f"Missing system prerequisites: {labels}")
            print("No package command is guessed for this distribution.")
        return 2
    optional_missing = tuple(
        check for check in checks if not check.available and check.optional
    )
    if optional_missing and distribution.arch_family:
        command = _arch_install_command(optional_missing, include_optional=True)
        if command is not None:
            print("For the enhanced locked Wayland overlay, optionally install:")
            print(f"  {command}")
    if arguments.check_only:
        print("Prerequisite check passed; no files were changed.")
        return 0

    try:
        environment_path = _validate_environment_path(arguments.venv)
        install_desktop = _desktop_choice(arguments)
        if not environment_path.exists():
            _run([str(python), "-m", "venv", str(environment_path)])
        environment_python = environment_path / "bin" / "python"
        environment_command = environment_path / "bin" / "konokashi"
        if not environment_python.is_file():
            raise BootstrapError("virtual environment does not contain bin/python")
        install_target = f"{PROJECT_ROOT}[dev]" if arguments.dev else str(PROJECT_ROOT)
        build_environment = dict(os.environ)
        build_environment.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
        layer_shell_available = all(
            check.available
            for check in checks
            if check.label == "LayerShellQt development files"
        )
        build_environment["KONOKASHI_LAYER_SHELL"] = (
            "required" if layer_shell_available else "off"
        )
        _run(
            [
                str(environment_python),
                "-m",
                "pip",
                "install",
                "--upgrade",
                install_target,
            ],
            environment=build_environment,
        )
        smoke_import = (
            "import konokashi; "
            "from konokashi import _lrc_native, _playback_clock_native; "
        )
        if layer_shell_available:
            smoke_import += "from konokashi import _wayland_overlay_native; "
        smoke_import += "print('native smoke:', konokashi.__version__, 'ok')"
        _run([str(environment_python), "-c", smoke_import])
        if install_desktop:
            _run([str(environment_command), "desktop-integration", "install"])
        print("Running desktop prerequisite diagnostics:", flush=True)
        doctor = subprocess.run(
            [str(environment_command), "doctor"], cwd=PROJECT_ROOT, check=False
        )
        if doctor.returncode:
            print(
                "Installation smoke passed, but desktop diagnostics found a session "
                "prerequisite. Rerun doctor inside the target desktop session."
            )
        print("KonoKashi is ready.")
        print(f"Launch: {shlex.join((str(environment_command), 'desktop'))}")
        if not install_desktop:
            launcher_command = (
                str(environment_command),
                "desktop-integration",
                "install",
            )
            print(f"Optional launcher: {shlex.join(launcher_command)}")
        return 0
    except (BootstrapError, OSError, subprocess.CalledProcessError) as error:
        print(f"Setup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
