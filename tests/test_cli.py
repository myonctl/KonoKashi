"""Behavior tests for the public command-line interface."""

from types import ModuleType

import pytest

from lyricflow import cli
from lyricflow.application.ports import DiagnosticCheck, DiagnosticStatus


def test_version_output(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == "lyricflow 0.1.0\n"


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
        "LyricFlow doctor\n"
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

    from lyricflow.infrastructure.diagnostics import collect_local_diagnostics

    checks = collect_local_diagnostics(
        platform_name="linux",
        environment={"DBUS_SESSION_BUS_ADDRESS": "unix:path=/session"},
        module_importer=unavailable,
        command_locator=lambda _name: None,
        directory_probe=lambda _path: None,
    )

    qtdbus = next(check for check in checks if check.name == "pyside6-qtdbus")
    assert qtdbus.status is DiagnosticStatus.FAILURE
