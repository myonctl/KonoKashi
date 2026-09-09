"""Smoke tests for the Stage 0 package skeleton."""

import tomllib
from importlib import import_module
from importlib.resources import files
from pathlib import Path

import konokashi


def test_package_version_is_exposed() -> None:
    assert konokashi.__version__ == "0.1.0b1"
    assert konokashi.DISPLAY_VERSION == "0.1.0-beta.1"
    assert konokashi.PRODUCT_NAME == "KonoKashi"
    assert konokashi.MACHINE_ID == "konokashi"
    assert konokashi.APPLICATION_ID == "io.github.myonctl.KonoKashi"


def test_required_package_skeleton_imports() -> None:
    modules = (
        "konokashi.application",
        "konokashi.application.ports",
        "konokashi.domain",
        "konokashi.domain.models",
        "konokashi.infrastructure",
        "konokashi.infrastructure.lyrics",
        "konokashi.infrastructure.metadata",
        "konokashi.infrastructure.mpris",
        "konokashi.infrastructure.mpris.backend",
        "konokashi.infrastructure.mpris.metadata_mapper",
        "konokashi.infrastructure.mpris.player_registry",
        "konokashi.infrastructure.mpris.qt_dbus_client",
        "konokashi.infrastructure.mpris.qt_dbus_values",
        "konokashi.infrastructure.storage",
        "konokashi.presentation",
        "konokashi.presentation.desktop",
    )

    for module in modules:
        assert import_module(module) is not None


def test_distribution_and_console_entry_point_are_canonically_named() -> None:
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]

    assert project["name"] == "konokashi"
    assert project["version"] == "0.1.0b1"
    assert "Development Status :: 4 - Beta" in project["classifiers"]
    assert "Private :: Do Not Upload" in project["classifiers"]
    assert project["license"] == "PolyForm-Noncommercial-1.0.0"
    assert project["scripts"] == {"konokashi": "konokashi.cli:main"}
    assert isinstance(project["dependencies"], list)
    assert project["urls"]["Source"] == "https://github.com/myonctl/KonoKashi"


def test_packaged_desktop_resources_use_current_application_identity() -> None:
    resources = files("konokashi").joinpath("resources")

    assert resources.joinpath("io.github.myonctl.KonoKashi.desktop.in").is_file()
    assert resources.joinpath("io.github.myonctl.KonoKashi.metainfo.xml").is_file()
    assert resources.joinpath("io.github.myonctl.KonoKashi.svg").is_file()
