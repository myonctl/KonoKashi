"""User-local freedesktop integration behavior and data-preserving removal."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyricflow import cli
from lyricflow.infrastructure.desktop_integration import (
    APP_ID,
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
    executable = tmp_path / "venv with spaces" / "bin" / "lyricflow"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    data_home = tmp_path / "data"
    database = data_home / "lyricflow" / "lyricflow.sqlite3"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"private-data")

    installed = install_desktop_integration(executable, data_home)

    assert installed.installed
    assert installed.desktop_file.name == f"{APP_ID}.desktop"
    desktop = installed.desktop_file.read_text(encoding="utf-8")
    assert f'Exec="{executable.resolve()}" desktop' in desktop
    assert "TryExec=" not in desktop
    assert "Icon=io.github.myonctl.LyricFlow" in desktop
    assert installed.icon_file.read_text(encoding="utf-8").startswith("<svg")

    removed = remove_desktop_integration(data_home)

    assert not removed.desktop_installed
    assert not removed.icon_installed
    assert database.read_bytes() == b"private-data"


def test_desktop_integration_cli_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    executable = tmp_path / "bin" / "lyricflow"
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
