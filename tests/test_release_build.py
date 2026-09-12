"""Release artifact identity and stale-build rejection tests."""

from __future__ import annotations

import tarfile
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from scripts.build_release import (
    REQUIRED_PYTHON_BOUNDS,
    REQUIRED_SDIST_SUFFIXES,
    REQUIRED_WHEEL_NATIVE_PREFIX,
    REQUIRED_WHEEL_SUFFIXES,
    ReleaseBuildError,
    _verify_contents,
)


def _write_artifacts(
    tmp_path: Path,
    *,
    unexpected: bool,
    native: bool = True,
    cpp_source: bool = False,
    python_bounds: frozenset[str] = REQUIRED_PYTHON_BOUNDS,
) -> dict[str, Path]:
    wheel = tmp_path / "konokashi-0.1.0b2-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name in REQUIRED_WHEEL_SUFFIXES:
            archive.writestr(name, b"current")
        if native:
            archive.writestr(
                f"{REQUIRED_WHEEL_NATIVE_PREFIX}cpython-test-linux.so", b"native"
            )
        if cpp_source:
            archive.writestr("konokashi/native/playback_clock.cpp", b"source")
        archive.writestr(
            "konokashi-0.1.0b2.dist-info/METADATA",
            "Metadata-Version: 2.4\n"
            f"Requires-Python: {','.join(sorted(python_bounds))}\n",
        )
        if unexpected:
            archive.writestr("obsolete_package/__init__.py", b"unexpected")

    source = tmp_path / "konokashi-0.1.0b2.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        for suffix in REQUIRED_SDIST_SUFFIXES:
            name = f"konokashi-0.1.0b2/{suffix}"
            info = tarfile.TarInfo(name)
            info.size = len(b"current")
            archive.addfile(info, BytesIO(b"current"))
        if unexpected:
            info = tarfile.TarInfo("konokashi-0.1.0b2/src/obsolete_package/__init__.py")
            info.size = len(b"unexpected")
            archive.addfile(info, BytesIO(b"unexpected"))
    return {wheel.name: wheel, source.name: source}


def test_release_content_check_accepts_only_current_namespace(tmp_path: Path) -> None:
    _verify_contents(_write_artifacts(tmp_path, unexpected=False))


def test_release_content_check_rejects_unexpected_namespace(tmp_path: Path) -> None:
    with pytest.raises(ReleaseBuildError, match="unexpected package namespace"):
        _verify_contents(_write_artifacts(tmp_path, unexpected=True))


def test_release_content_check_requires_native_playback_clock(tmp_path: Path) -> None:
    with pytest.raises(ReleaseBuildError, match="native PlaybackClock"):
        _verify_contents(_write_artifacts(tmp_path, unexpected=False, native=False))


def test_release_content_check_rejects_cpp_source_in_wheel(tmp_path: Path) -> None:
    with pytest.raises(ReleaseBuildError, match="must not contain C\\+\\+ source"):
        _verify_contents(_write_artifacts(tmp_path, unexpected=False, cpp_source=True))


def test_release_content_check_rejects_unadvertised_python_range(
    tmp_path: Path,
) -> None:
    artifacts = _write_artifacts(
        tmp_path,
        unexpected=False,
        python_bounds=frozenset({">=3.12"}),
    )

    with pytest.raises(ReleaseBuildError, match="Requires-Python"):
        _verify_contents(artifacts)
