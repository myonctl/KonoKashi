"""Source-bootstrap policy and safety regressions."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.bootstrap_source import (
    BootstrapError,
    SystemCheck,
    _arch_install_command,
    _parse_os_release_payload,
    _validate_environment_path,
)


def test_os_release_identifies_arch_and_artix_families() -> None:
    arch = _parse_os_release_payload('ID=arch\nPRETTY_NAME="Arch Linux"\n')
    artix = _parse_os_release_payload(
        'ID=artix\nID_LIKE="arch"\nPRETTY_NAME="Artix Linux"\n'
    )
    unrelated = _parse_os_release_payload("ID=fedora\nID_LIKE='rhel centos'\n")

    assert arch.arch_family
    assert artix.arch_family
    assert artix.display_name == "Artix Linux"
    assert not unrelated.arch_family


def test_arch_command_is_exact_deduplicated_and_never_executed() -> None:
    checks = (
        SystemCheck("compiler", False, ("gcc",), "missing"),
        SystemCheck("crypto", False, ("openssl",), "missing"),
        SystemCheck("same owner", False, ("gcc",), "missing"),
        SystemCheck("optional", False, ("layer-shell-qt",), "missing", True),
    )

    assert _arch_install_command(checks) == "sudo pacman -S --needed gcc openssl"
    assert (
        _arch_install_command(checks, include_optional=True)
        == "sudo pacman -S --needed gcc layer-shell-qt openssl"
    )


def test_environment_path_refuses_existing_non_venv(tmp_path: Path) -> None:
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "user-data").write_text("preserve", encoding="utf-8")

    with pytest.raises(BootstrapError, match="not a virtual environment"):
        _validate_environment_path(occupied)

    assert (occupied / "user-data").read_text(encoding="utf-8") == "preserve"


def test_environment_path_accepts_new_and_existing_venv(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh"
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "pyvenv.cfg").write_text("home = /test\n", encoding="utf-8")

    assert _validate_environment_path(fresh) == fresh.resolve()
    assert _validate_environment_path(existing) == existing.resolve()


def test_shell_entrypoint_only_delegates_to_selected_python() -> None:
    script = (Path(__file__).parents[1] / "setup.sh").read_text(encoding="utf-8")

    assert "scripts/bootstrap_source.py" in script
    assert 'exec "$bootstrap_python"' in script
    assert "sudo" not in script
    assert "pacman" not in script
