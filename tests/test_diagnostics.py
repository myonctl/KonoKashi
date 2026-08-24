"""Unit tests for local prerequisite collection and doctor policy."""

from pathlib import Path
from types import ModuleType

from lyriflux.application.diagnostics import build_doctor_report
from lyriflux.application.ports import DiagnosticStatus
from lyriflux.infrastructure.diagnostics import collect_local_diagnostics


def _available(_name: str) -> ModuleType:
    return ModuleType("PySide6.QtDBus")


def test_all_required_local_prerequisites_pass(tmp_path: Path) -> None:
    probed: list[Path] = []

    def record_probe(path: Path) -> None:
        probed.append(path)
        return None

    checks = collect_local_diagnostics(
        platform_name="linux",
        environment={
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/session",
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
        },
        home=tmp_path / "home",
        module_importer=_available,
        command_locator=lambda _name: "/usr/bin/playerctl",
        directory_probe=record_probe,
    )

    assert [check.name for check in checks] == [
        "platform",
        "session-dbus",
        "pyside6-qtdbus",
        "config-dir",
        "data-dir",
        "cache-dir",
        "playerctl",
    ]
    assert all(check.status is DiagnosticStatus.OK for check in checks)
    assert probed == [
        tmp_path / "config" / "lyriflux",
        tmp_path / "data" / "lyriflux",
        tmp_path / "cache" / "lyriflux",
    ]
    assert build_doctor_report(checks).exit_code == 0


def test_optional_playerctl_warning_does_not_fail_doctor(tmp_path: Path) -> None:
    checks = collect_local_diagnostics(
        platform_name="linux",
        environment={"DBUS_SESSION_BUS_ADDRESS": "unix:path=/session"},
        home=tmp_path,
        module_importer=_available,
        command_locator=lambda _name: None,
        directory_probe=lambda _path: None,
    )

    report = build_doctor_report(checks)
    playerctl = next(check for check in checks if check.name == "playerctl")
    assert playerctl.status is DiagnosticStatus.WARNING
    assert report.exit_code == 0


def test_each_missing_required_prerequisite_fails_doctor(tmp_path: Path) -> None:
    def unavailable(_name: str) -> ModuleType:
        raise ImportError

    def fail_cache(path: Path) -> str | None:
        return "permission denied" if path.name == "lyriflux" else None

    checks = collect_local_diagnostics(
        platform_name="darwin",
        environment={},
        home=tmp_path,
        module_importer=unavailable,
        command_locator=lambda _name: None,
        directory_probe=fail_cache,
    )

    failures = {
        check.name for check in checks if check.status is DiagnosticStatus.FAILURE
    }
    assert {"platform", "session-dbus", "pyside6-qtdbus"} <= failures
    assert {"config-dir", "data-dir", "cache-dir"} <= failures
    assert build_doctor_report(checks).exit_code == 1


def test_real_directory_probe_creates_writable_app_directories(tmp_path: Path) -> None:
    checks = collect_local_diagnostics(
        platform_name="linux",
        environment={
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/session",
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
        },
        module_importer=_available,
        command_locator=lambda _name: None,
    )

    directory_checks = [check for check in checks if check.name.endswith("-dir")]
    assert all(check.status is DiagnosticStatus.OK for check in directory_checks)
    assert (tmp_path / "config" / "lyriflux").is_dir()
    assert (tmp_path / "data" / "lyriflux").is_dir()
    assert (tmp_path / "cache" / "lyriflux").is_dir()


def test_doctor_reports_dual_state_conflict_without_creating_more_state(
    tmp_path: Path,
) -> None:
    environment = {
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/session",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    (tmp_path / "data" / "lyricflow").mkdir(parents=True)
    (tmp_path / "data" / "lyriflux").mkdir(parents=True)

    checks = collect_local_diagnostics(
        platform_name="linux",
        environment=environment,
        home=tmp_path,
        module_importer=_available,
        command_locator=lambda _name: None,
    )

    migration = next(
        check for check in checks if check.name == "legacy-state-migration"
    )
    assert migration.status is DiagnosticStatus.FAILURE
    assert "both legacy LyricFlow and current LyriFlux state exist" in migration.message
    assert not (tmp_path / "config" / "lyriflux").exists()
    assert not (tmp_path / "cache" / "lyriflux").exists()
