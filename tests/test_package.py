"""Smoke tests for the Stage 0 package skeleton."""

from importlib import import_module

import lyricflow


def test_package_version_is_exposed() -> None:
    assert lyricflow.__version__ == "0.1.0"


def test_required_package_skeleton_imports() -> None:
    modules = (
        "lyricflow.application",
        "lyricflow.application.ports",
        "lyricflow.domain",
        "lyricflow.domain.models",
        "lyricflow.infrastructure",
        "lyricflow.infrastructure.lyrics",
        "lyricflow.infrastructure.metadata",
        "lyricflow.infrastructure.mpris",
        "lyricflow.infrastructure.mpris.backend",
        "lyricflow.infrastructure.mpris.metadata_mapper",
        "lyricflow.infrastructure.mpris.player_registry",
        "lyricflow.infrastructure.mpris.qt_dbus_client",
        "lyricflow.infrastructure.mpris.qt_dbus_values",
        "lyricflow.infrastructure.storage",
        "lyricflow.presentation",
        "lyricflow.presentation.desktop",
    )

    for module in modules:
        assert import_module(module) is not None
