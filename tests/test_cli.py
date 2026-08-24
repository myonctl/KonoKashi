"""Behavior tests for the public command-line interface."""

from pathlib import Path
from types import ModuleType

import pytest

from lyriflux import cli
from lyriflux.application.ports import DiagnosticCheck, DiagnosticStatus
from lyriflux.presentation.desktop import app as desktop_app


def test_version_output(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == "lyriflux 1.0.0\n"


def test_desktop_command_dispatches_to_the_qt_entry_point(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: list[tuple[list[str], object]] = []

    def launch(argv: list[str], *, database_path: Path | None) -> int:
        observed.append((argv, database_path))
        return 23

    monkeypatch.setattr(desktop_app, "run_desktop", launch)

    assert cli.main(["desktop"], database_path=tmp_path / "desktop.sqlite3") == 23
    assert observed == [(["lyriflux"], tmp_path / "desktop.sqlite3")]


def test_desktop_startup_failure_is_controlled(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(_argv: list[str], *, database_path: Path | None) -> int:
        raise RuntimeError(f"controlled {database_path}")

    monkeypatch.setattr(desktop_app, "run_desktop", fail)

    assert cli.main(["desktop"]) == 1
    captured = capsys.readouterr()
    assert "Unable to start" in captured.err
    assert "diagnostics export" in captured.err
    assert "Traceback" not in captured.err


def test_doctor_success_output_and_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checks = (
        DiagnosticCheck("platform", DiagnosticStatus.OK, "Linux detected"),
        DiagnosticCheck(
            "playerctl",
            DiagnosticStatus.WARNING,
            "not found (optional debugging tool)",
            required=False,
        ),
    )
    monkeypatch.setattr(cli, "collect_local_diagnostics", lambda: checks)

    assert cli.main(["doctor"]) == 0
    assert capsys.readouterr().out == (
        "LyriFlux doctor\n"
        "[OK] platform: Linux detected\n"
        "[WARN] playerctl: not found (optional debugging tool)\n"
        "Summary: 1 passed, 1 warning, 0 failures\n"
    )


def test_doctor_failure_output_and_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checks = (
        DiagnosticCheck(
            "session-dbus",
            DiagnosticStatus.FAILURE,
            "DBUS_SESSION_BUS_ADDRESS is not set",
        ),
    )
    monkeypatch.setattr(cli, "collect_local_diagnostics", lambda: checks)

    assert cli.main(["doctor"]) == 1
    assert "[FAIL] session-dbus" in capsys.readouterr().out


def test_qtdbus_import_failure_is_reported() -> None:
    def unavailable(_name: str) -> ModuleType:
        raise ImportError("missing")

    from lyriflux.infrastructure.diagnostics import collect_local_diagnostics

    checks = collect_local_diagnostics(
        platform_name="linux",
        environment={"DBUS_SESSION_BUS_ADDRESS": "unix:path=/session"},
        module_importer=unavailable,
        command_locator=lambda _name: None,
        directory_probe=lambda _path: None,
    )

    qtdbus = next(check for check in checks if check.name == "pyside6-qtdbus")
    assert qtdbus.status is DiagnosticStatus.FAILURE
