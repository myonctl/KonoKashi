#!/usr/bin/env python3
"""Build, compare, inspect, and publish local LyriFlux release artifacts."""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ID = "io.github.myonctl.LyriFlux"
REQUIRED_WHEEL_SUFFIXES = (
    "lyriflux/cli.py",
    f"lyriflux/resources/{APP_ID}.desktop.in",
    f"lyriflux/resources/{APP_ID}.svg",
)
REQUIRED_SDIST_SUFFIXES = (
    "LICENSE",
    "README.md",
    "pyproject.toml",
    f"src/lyriflux/resources/{APP_ID}.desktop.in",
    f"src/lyriflux/resources/{APP_ID}.svg",
)
FORBIDDEN_WHEEL_PREFIXES = ("lyricflow/",)
FORBIDDEN_SDIST_PARTS = ("/src/lyricflow/",)


class ReleaseBuildError(RuntimeError):
    """A controlled release artifact failure."""


def _source_date_epoch(environment: dict[str, str]) -> str:
    supplied = environment.get("SOURCE_DATE_EPOCH")
    if supplied is None:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        supplied = result.stdout.strip()
    if not supplied.isascii() or not supplied.isdigit() or int(supplied) <= 0:
        raise ReleaseBuildError("SOURCE_DATE_EPOCH must be a positive Unix timestamp")
    return supplied


def _canonicalize_sdist(path: Path, epoch: int) -> None:
    """Normalize gzip/tar metadata that setuptools does not source-date clamp."""

    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        payloads: dict[str, bytes] = {}
        for member in members:
            if not member.isfile():
                continue
            stream = archive.extractfile(member)
            if stream is None:
                raise ReleaseBuildError(f"could not read source member {member.name}")
            payloads[member.name] = stream.read()
    temporary = path.with_name(f".{path.name}.normalized")
    with (
        temporary.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as zipped,
        tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as out,
    ):
        for member in sorted(members, key=lambda item: item.name):
            normalized = copy.copy(member)
            normalized.uid = 0
            normalized.gid = 0
            normalized.uname = ""
            normalized.gname = ""
            normalized.mtime = epoch
            normalized.pax_headers = {}
            if normalized.isfile():
                out.addfile(normalized, BytesIO(payloads[normalized.name]))
            else:
                out.addfile(normalized)
    temporary.replace(path)


def _build(output: Path, environment: dict[str, str], epoch: int) -> None:
    with tempfile.TemporaryDirectory(prefix="lyriflux-release-source-") as source_name:
        source = Path(source_name) / "source"
        shutil.copytree(
            PROJECT_ROOT,
            source,
            ignore=shutil.ignore_patterns(
                ".git",
                ".venv",
                ".mypy_cache",
                ".pytest_cache",
                ".ruff_cache",
                "__pycache__",
                "*.egg-info",
                "build",
                "dist",
            ),
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--no-isolation",
                "--sdist",
                "--wheel",
                "--outdir",
                str(output),
                str(source),
            ],
            cwd=source,
            env=environment,
            check=True,
        )
    source = next(output.glob("*.tar.gz"), None)
    if source is None:
        raise ReleaseBuildError("build did not produce a source distribution")
    _canonicalize_sdist(source, epoch)


def _artifacts(directory: Path) -> dict[str, Path]:
    artifacts = {
        path.name: path
        for path in directory.iterdir()
        if path.suffix == ".whl" or path.name.endswith(".tar.gz")
    }
    if len(artifacts) != 2:
        raise ReleaseBuildError(
            "expected exactly one wheel and one source distribution"
        )
    return artifacts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_contents(artifacts: dict[str, Path]) -> None:
    wheel = next(path for name, path in artifacts.items() if name.endswith(".whl"))
    source = next(path for name, path in artifacts.items() if name.endswith(".tar.gz"))
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = set(archive.namelist())
    missing_wheel = [
        suffix
        for suffix in REQUIRED_WHEEL_SUFFIXES
        if not any(name.endswith(suffix) for name in wheel_names)
    ]
    with tarfile.open(source, "r:gz") as archive:
        source_names = set(archive.getnames())
    missing_source = [
        suffix
        for suffix in REQUIRED_SDIST_SUFFIXES
        if not any(name.endswith(suffix) for name in source_names)
    ]
    if missing_wheel or missing_source:
        missing = ", ".join((*missing_wheel, *missing_source))
        raise ReleaseBuildError(
            f"release artifacts are missing required files: {missing}"
        )
    forbidden = sorted(
        name for name in wheel_names if name.startswith(FORBIDDEN_WHEEL_PREFIXES)
    ) + sorted(
        name
        for name in source_names
        if any(part in f"/{name}" for part in FORBIDDEN_SDIST_PARTS)
    )
    if forbidden:
        raise ReleaseBuildError(
            "release artifacts contain the legacy lyricflow package: "
            + ", ".join(forbidden[:5])
        )


def build_release(output_directory: Path, *, force: bool = False) -> tuple[Path, ...]:
    """Build twice, require byte identity, inspect, then copy both artifacts."""

    environment = dict(os.environ)
    epoch = _source_date_epoch(environment)
    environment.update(
        {
            "SOURCE_DATE_EPOCH": epoch,
            "PYTHONHASHSEED": "0",
            "TZ": "UTC",
            "LC_ALL": "C.UTF-8",
        }
    )
    with (
        tempfile.TemporaryDirectory(prefix="lyriflux-release-a-") as first_name,
        tempfile.TemporaryDirectory(prefix="lyriflux-release-b-") as second_name,
    ):
        first_directory = Path(first_name)
        second_directory = Path(second_name)
        _build(first_directory, environment, int(epoch))
        _build(second_directory, environment, int(epoch))
        first = _artifacts(first_directory)
        second = _artifacts(second_directory)
        if first.keys() != second.keys():
            raise ReleaseBuildError("repeated builds produced different artifact names")
        mismatched = [
            name for name in first if _sha256(first[name]) != _sha256(second[name])
        ]
        if mismatched:
            raise ReleaseBuildError(
                "repeated builds were not byte-identical: " + ", ".join(mismatched)
            )
        _verify_contents(first)
        resolved_output = output_directory.expanduser().resolve()
        resolved_output.mkdir(parents=True, exist_ok=True)
        destinations = tuple(resolved_output / name for name in sorted(first))
        existing = [path for path in destinations if path.exists()]
        if existing and not force:
            raise ReleaseBuildError(
                "release output already exists; pass --force to replace exact artifacts"
            )
        for destination in destinations:
            temporary = destination.with_name(f".{destination.name}.tmp")
            shutil.copyfile(first[destination.name], temporary)
            temporary.replace(destination)
        print(f"SOURCE_DATE_EPOCH={epoch}")
        for destination in destinations:
            print(f"{_sha256(destination)}  {destination}")
        print("reproducible comparison: pass")
        print("required artifact contents: pass")
        return destinations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "dist")
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()
    try:
        build_release(arguments.output_dir, force=arguments.force)
    except (OSError, subprocess.CalledProcessError, ReleaseBuildError) as error:
        print(f"Release build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
