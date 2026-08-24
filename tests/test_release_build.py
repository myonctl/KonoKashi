"""Release artifact identity and stale-build rejection tests."""

from __future__ import annotations

import tarfile
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from scripts.build_release import (
    REQUIRED_SDIST_SUFFIXES,
    REQUIRED_WHEEL_SUFFIXES,
    ReleaseBuildError,
    _verify_contents,
)


def _write_artifacts(tmp_path: Path, *, legacy: bool) -> dict[str, Path]:
    wheel = tmp_path / "lyriflux-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name in REQUIRED_WHEEL_SUFFIXES:
            archive.writestr(name, b"current")
        if legacy:
            archive.writestr("lyricflow/__init__.py", b"legacy")

    source = tmp_path / "lyriflux-1.0.0.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        for suffix in REQUIRED_SDIST_SUFFIXES:
            name = f"lyriflux-1.0.0/{suffix}"
            info = tarfile.TarInfo(name)
            info.size = len(b"current")
            archive.addfile(info, BytesIO(b"current"))
        if legacy:
            info = tarfile.TarInfo("lyriflux-1.0.0/src/lyricflow/__init__.py")
            info.size = len(b"legacy")
            archive.addfile(info, BytesIO(b"legacy"))
    return {wheel.name: wheel, source.name: source}


def test_release_content_check_accepts_only_current_namespace(tmp_path: Path) -> None:
    _verify_contents(_write_artifacts(tmp_path, legacy=False))


def test_release_content_check_rejects_stale_legacy_namespace(tmp_path: Path) -> None:
    with pytest.raises(ReleaseBuildError, match="legacy lyricflow package"):
        _verify_contents(_write_artifacts(tmp_path, legacy=True))
