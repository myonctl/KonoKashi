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
    REQUIRED_WHEEL_NATIVE_MODULES,
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
    private_evaluation_in: str | None = None,
    python_bounds: frozenset[str] = REQUIRED_PYTHON_BOUNDS,
) -> dict[str, Path]:
    wheel = tmp_path / "konokashi-0.1.0b2-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name in REQUIRED_WHEEL_SUFFIXES:
            archive.writestr(name, b"current")
        if native:
            for prefix in REQUIRED_WHEEL_NATIVE_MODULES.values():
                archive.writestr(f"{prefix}cpython-test-linux.so", b"native")
        if cpp_source:
            archive.writestr("konokashi/native/playback_clock.cpp", b"source")
            archive.writestr("konokashi/native/lrc_parser.hpp", b"source")
        if private_evaluation_in == "wheel":
            archive.writestr(
                "konokashi/.matching-evaluation-private/case.json", b"private"
            )
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
        if private_evaluation_in == "sdist":
            info = tarfile.TarInfo(
                "konokashi-0.1.0b2/.matching-evaluation-private/case.json"
            )
            info.size = len(b"private")
            archive.addfile(info, BytesIO(b"private"))
    return {wheel.name: wheel, source.name: source}


def test_release_content_check_accepts_only_current_namespace(tmp_path: Path) -> None:
    _verify_contents(_write_artifacts(tmp_path, unexpected=False))


def test_release_content_check_rejects_unexpected_namespace(tmp_path: Path) -> None:
    with pytest.raises(ReleaseBuildError, match="unexpected package namespace"):
        _verify_contents(_write_artifacts(tmp_path, unexpected=True))


def test_release_content_check_requires_every_native_module(tmp_path: Path) -> None:
    with pytest.raises(ReleaseBuildError, match=r"native (?:PlaybackClock|LRC parser)"):
        _verify_contents(_write_artifacts(tmp_path, unexpected=False, native=False))


def test_release_content_check_rejects_cpp_source_in_wheel(tmp_path: Path) -> None:
    with pytest.raises(ReleaseBuildError, match="must not contain C\\+\\+ source"):
        _verify_contents(_write_artifacts(tmp_path, unexpected=False, cpp_source=True))


@pytest.mark.parametrize("artifact", ("wheel", "sdist"))
def test_release_content_check_rejects_private_evaluation_data(
    tmp_path: Path,
    artifact: str,
) -> None:
    with pytest.raises(ReleaseBuildError, match="private evaluation data"):
        _verify_contents(
            _write_artifacts(
                tmp_path,
                unexpected=False,
                private_evaluation_in=artifact,
            )
        )


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
