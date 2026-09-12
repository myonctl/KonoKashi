"""Privacy, schema, and atomic-output checks for diagnostic exports."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from konokashi import cli
from konokashi.application.ports import DiagnosticCheck, DiagnosticStatus
from konokashi.application.release_diagnostics import (
    build_release_diagnostic_export,
)
from konokashi.application.storage_diagnostics import (
    LibraryScanDiagnostics,
    StorageStatus,
)


def test_export_drops_paths_free_form_errors_and_private_content() -> None:
    private = "/home/person/Music/secret-track.flac"
    report = build_release_diagnostic_export(
        konokashi_version="0.1.0b2",
        checks=(
            DiagnosticCheck(
                "data-dir", DiagnosticStatus.FAILURE, f"not writable ({private})"
            ),
        ),
        storage=StorageStatus(
            path="/home/person/.local/share/konokashi/konokashi.sqlite3",
            exists=True,
            current_schema_version=9,
            schema_version=9,
            migration_status="current",
            readable=False,
            writable=False,
            integrity_status="not checked",
            latest_library_scan=LibraryScanDiagnostics(
                status="completed-with-errors",
                discovered=10,
                processed=9,
                unchanged=0,
                moved=0,
                missing=0,
                review=1,
                downloaded=0,
                download_misses=1,
                errors=2,
                error_categories={"metadata-read": 1, "download": 1},
            ),
            error=f"failed near {private}",
        ),
        desktop_integration_installed=False,
        dependencies={"PySide6": "6.9.0"},
        generated_at=datetime(2026, 8, 23, tzinfo=UTC),
        platform_name="linux",
        machine="x86_64",
        python_version="3.14.1",
    )

    payload = report.render_json()
    decoded = json.loads(payload)
    assert decoded["format_version"] == 1
    assert decoded["storage"]["error"] is True
    assert decoded["storage"]["latest_library_scan"] == {
        "discovered": 10,
        "download_misses": 1,
        "downloaded": 0,
        "error_categories": {"download": 1, "metadata-read": 1},
        "errors": 2,
        "missing": 0,
        "moved": 0,
        "processed": 9,
        "review": 1,
        "status": "completed-with-errors",
        "unchanged": 0,
    }
    assert decoded["checks"] == [
        {"name": "data-dir", "required": True, "status": "FAIL"}
    ]
    assert private not in payload
    assert "/home/person" not in payload


def test_cli_export_writes_private_file_and_requires_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "collect_local_diagnostics",
        lambda: (DiagnosticCheck("platform", DiagnosticStatus.OK, "private"),),
    )
    output = tmp_path / "reports" / "diagnostics.json"

    assert (
        cli.main(
            ["diagnostics", "export", "--output", str(output)],
            database_path=tmp_path / "missing.sqlite3",
        )
        == 0
    )
    capsys.readouterr()
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(output.read_text(encoding="utf-8"))["format_version"] == 1

    assert (
        cli.main(
            ["diagnostics", "export", "--output", str(output)],
            database_path=tmp_path / "missing.sqlite3",
        )
        == 1
    )
    assert "already exists" in capsys.readouterr().err
    assert (
        cli.main(
            ["diagnostics", "export", "--output", str(output), "--force"],
            database_path=tmp_path / "missing.sqlite3",
        )
        == 0
    )
