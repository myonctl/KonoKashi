"""Normal storage diagnostics and developer persistence harness tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyricflow import cli


def test_storage_status_missing_then_migrate_then_current(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "data with spaces" / "lyricflow.sqlite3"

    assert cli.main(["storage", "status"], database_path=path) == 0
    missing = capsys.readouterr().out
    assert "exists: no" in missing
    assert "not initialized" in missing

    assert cli.main(["storage", "migrate"], database_path=path) == 0
    capsys.readouterr()
    assert cli.main(["storage", "status"], database_path=path) == 0
    current = capsys.readouterr().out
    assert "exists: yes" in current
    assert "migration status: current" in current
    assert "integrity: ok" in current
    assert "approved track corrections: 0" in current
    assert "representation candidates: 0" in current
    assert "representation decisions: 0" in current
    assert "lyric language overrides: 0" in current
    assert "lyric match rejections: 0" in current


def test_storage_settings_persist_across_fresh_cli_calls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "settings.sqlite3"

    assert (
        cli.main(
            [
                "storage",
                "settings",
                "set",
                "--prefer",
                "strawberry",
                "--ignore",
                "firefox",
            ],
            database_path=path,
        )
        == 0
    )
    capsys.readouterr()

    assert cli.main(["storage", "settings", "show"], database_path=path) == 0
    output = capsys.readouterr().out
    assert "preferred players: strawberry" in output
    assert "ignored players: firefox" in output


def test_multilingual_display_settings_persist_across_cli_calls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "display.sqlite3"

    assert cli.main(["storage", "display", "show"], database_path=path) == 0
    defaults = capsys.readouterr().out
    assert "show original: on" in defaults
    assert "show romanized/transliterated: on" in defaults
    assert "show translated: off" in defaults

    assert (
        cli.main(
            [
                "storage",
                "display",
                "set",
                "--original",
                "off",
                "--translated",
                "on",
            ],
            database_path=path,
        )
        == 0
    )
    capsys.readouterr()
    assert cli.main(["storage", "display", "show"], database_path=path) == 0
    persisted = capsys.readouterr().out
    assert "show original: off" in persisted
    assert "show romanized/transliterated: on" in persisted
    assert "show translated: on" in persisted


def test_corrupt_storage_status_is_controlled_and_keeps_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "corrupt.sqlite3"
    payload = b"not sqlite"
    path.write_bytes(payload)

    assert cli.main(["storage", "status"], database_path=path) == 1
    captured = capsys.readouterr()
    assert "error:" in captured.out
    assert "Traceback" not in captured.out + captured.err
    assert path.read_bytes() == payload
