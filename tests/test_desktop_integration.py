"""User-local freedesktop integration behavior and data-preserving removal."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyriflux import cli
from lyriflux.infrastructure.desktop_integration import (
    APP_ID,
    LEGACY_APP_ID,
    DesktopIntegrationError,
    install_desktop_integration,
    integration_status,
    remove_desktop_integration,
    user_data_home,
)


def test_xdg_data_home_requires_an_absolute_value(tmp_path: Path) -> None:
    home = tmp_path / "home"

    assert user_data_home({}, home=home) == home / ".local" / "share"
    assert user_data_home({"XDG_DATA_HOME": "relative"}, home=home) == (
        home / ".local" / "share"
    )
    assert user_data_home({"XDG_DATA_HOME": str(tmp_path / "data")}, home=home) == (
        tmp_path / "data"
    )


def test_install_and_remove_touch_only_owned_integration_files(tmp_path: Path) -> None:
    executable = tmp_path / "venv with spaces" / "bin" / "lyriflux"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    data_home = tmp_path / "data home"
    database = data_home / "lyriflux" / "lyriflux.sqlite3"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"private-data")

    installed = install_desktop_integration(executable, data_home)

    assert installed.installed
    assert installed.desktop_file.name == f"{APP_ID}.desktop"
    desktop = installed.desktop_file.read_text(encoding="utf-8")
    assert f'Exec="{executable.resolve()}" desktop' in desktop
    assert "TryExec=" not in desktop
    escaped_icon = str(installed.icon_file.resolve()).replace(" ", "\\s")
    assert f"Icon={escaped_icon}" in desktop
    assert "Icon=io.github.myonctl.LyriFlux" not in desktop
    assert installed.icon_file.read_text(encoding="utf-8").startswith("<svg")

    removed = remove_desktop_integration(data_home)

    assert not removed.desktop_installed
    assert not removed.icon_installed
    assert database.read_bytes() == b"private-data"


def test_install_removes_only_the_known_legacy_launcher_and_icon(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "bin" / "lyriflux"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    data_home = tmp_path / "data"
    legacy_desktop = data_home / "applications" / f"{LEGACY_APP_ID}.desktop"
    legacy_icon = (
        data_home / "icons" / "hicolor" / "scalable" / "apps" / f"{LEGACY_APP_ID}.svg"
    )
    legacy_desktop.parent.mkdir(parents=True)
    legacy_icon.parent.mkdir(parents=True)
    legacy_desktop.write_text(
        "[Desktop Entry]\nName=LyricFlow\nExec=/legacy/bin/lyricflow desktop\n",
        encoding="utf-8",
    )
    legacy_icon.write_text("legacy icon", encoding="utf-8")

    status = install_desktop_integration(executable, data_home)

    assert status.installed
    assert not status.legacy_desktop_installed
    assert not status.legacy_icon_installed
    assert not legacy_desktop.exists()
    assert not legacy_icon.exists()


def test_install_retains_an_unrecognized_legacy_path(tmp_path: Path) -> None:
    executable = tmp_path / "bin" / "lyriflux"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    data_home = tmp_path / "data"
    legacy_desktop = data_home / "applications" / f"{LEGACY_APP_ID}.desktop"
    legacy_desktop.parent.mkdir(parents=True)
    legacy_desktop.write_text(
        "[Desktop Entry]\nName=Unrelated application\nExec=/unrelated\n",
        encoding="utf-8",
    )

    with pytest.raises(DesktopIntegrationError, match="does not match"):
        install_desktop_integration(executable, data_home)

    assert legacy_desktop.is_file()


def test_desktop_integration_cli_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    executable = tmp_path / "bin" / "lyriflux"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    assert cli.main(["desktop-integration", "install"], executable_path=executable) == 0
    capsys.readouterr()
    assert cli.main(["desktop-integration", "status"]) == 0
    assert "desktop installed: yes" in capsys.readouterr().out
    assert cli.main(["desktop-integration", "remove"]) == 0
    assert "application data modified: no" in capsys.readouterr().out
    assert not integration_status(tmp_path / "xdg").installed
