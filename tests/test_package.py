"""Smoke tests for the Stage 0 package skeleton."""

import tomllib
from importlib import import_module
from importlib.resources import files
from pathlib import Path

import lyriflux


def test_package_version_is_exposed() -> None:
    assert lyriflux.__version__ == "1.0.0"
    assert lyriflux.PRODUCT_NAME == "LyriFlux"
    assert lyriflux.MACHINE_ID == "lyriflux"
    assert lyriflux.APPLICATION_ID == "io.github.myonctl.LyriFlux"


def test_required_package_skeleton_imports() -> None:
    modules = (
        "lyriflux.application",
        "lyriflux.application.ports",
        "lyriflux.domain",
        "lyriflux.domain.models",
        "lyriflux.infrastructure",
        "lyriflux.infrastructure.lyrics",
        "lyriflux.infrastructure.metadata",
        "lyriflux.infrastructure.mpris",
        "lyriflux.infrastructure.mpris.backend",
        "lyriflux.infrastructure.mpris.metadata_mapper",
        "lyriflux.infrastructure.mpris.player_registry",
        "lyriflux.infrastructure.mpris.qt_dbus_client",
        "lyriflux.infrastructure.mpris.qt_dbus_values",
        "lyriflux.infrastructure.storage",
        "lyriflux.presentation",
        "lyriflux.presentation.desktop",
    )

    for module in modules:
        assert import_module(module) is not None


def test_distribution_and_console_entry_point_are_canonically_named() -> None:
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]

    assert project["name"] == "lyriflux"
    assert project["license"] == "PolyForm-Noncommercial-1.0.0"
    assert project["scripts"] == {"lyriflux": "lyriflux.cli:main"}
    assert isinstance(project["dependencies"], list)
    assert project["urls"]["Source"] == "https://github.com/myonctl/LyriFlux"
    assert "lyricflow" not in project["scripts"]


def test_packaged_desktop_resources_use_current_application_identity() -> None:
    resources = files("lyriflux").joinpath("resources")

    assert resources.joinpath("io.github.myonctl.LyriFlux.desktop.in").is_file()
    assert resources.joinpath("io.github.myonctl.LyriFlux.metainfo.xml").is_file()
    assert resources.joinpath("io.github.myonctl.LyriFlux.svg").is_file()
