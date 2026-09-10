#!/usr/bin/env python3
"""Prepare a reproducible current-checkout sdist for local Flatpak validation."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if TYPE_CHECKING or __package__:
    from scripts import build_release as release_builder
else:
    import build_release as release_builder

FLATPAK_DIRECTORY = PROJECT_ROOT / "packaging" / "flatpak"
MANIFEST_NAME = "io.github.myonctl.KonoKashi.yaml"
DEPENDENCY_MANIFEST_NAME = "python3-dependencies.json"
SUPPORT_DIRECTORIES = ("portal-parent-bridge",)
PUBLISHED_SOURCE_URL = (
    "https://github.com/myonctl/KonoKashi/releases/download/"
    "v0.1.0-beta.1/konokashi-0.1.0b1.tar.gz"
)
PUBLISHED_SOURCE_SHA256 = (
    "c0727503df631741a1ebe1d8cdb1836363eb727c1930fea0e4ce921b327734d8"
)


class FlatpakPreparationError(RuntimeError):
    """A controlled current-candidate preparation failure."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def render_current_manifest(
    manifest: str,
    *,
    source_uri: str,
    source_sha256: str,
) -> str:
    """Replace only the release sdist source in the canonical manifest."""

    if manifest.count(PUBLISHED_SOURCE_URL) != 1:
        raise FlatpakPreparationError(
            "canonical Flatpak manifest does not contain its exact published source URL"
        )
    if manifest.count(PUBLISHED_SOURCE_SHA256) != 1:
        raise FlatpakPreparationError(
            "canonical Flatpak manifest does not contain its exact published "
            "source hash"
        )
    return manifest.replace(PUBLISHED_SOURCE_URL, source_uri).replace(
        PUBLISHED_SOURCE_SHA256,
        source_sha256,
    )


def _replace_file(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def prepare_flatpak_candidate(
    output_directory: Path,
    *,
    force: bool = False,
) -> Path:
    """Build the current sdist and write a local-only derived manifest."""

    output = output_directory.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / MANIFEST_NAME
    dependency_path = output / DEPENDENCY_MANIFEST_NAME
    support_paths = tuple(output / name for name in SUPPORT_DIRECTORIES)
    if not force and (
        manifest_path.exists()
        or dependency_path.exists()
        or any(path.exists() for path in support_paths)
    ):
        raise FlatpakPreparationError(
            "Flatpak preparation output already exists; pass --force to replace it"
        )
    artifacts = release_builder.build_release(output / "artifacts", force=force)
    source_archives = [path for path in artifacts if path.name.endswith(".tar.gz")]
    if len(source_archives) != 1:
        raise FlatpakPreparationError(
            "release builder did not return exactly one source distribution"
        )
    source = source_archives[0]
    canonical_manifest = (FLATPAK_DIRECTORY / MANIFEST_NAME).read_text(encoding="utf-8")
    derived_manifest = render_current_manifest(
        canonical_manifest,
        source_uri=source.as_uri(),
        source_sha256=_sha256(source),
    )
    _replace_file(manifest_path, derived_manifest.encode("utf-8"))
    dependency_source = FLATPAK_DIRECTORY / DEPENDENCY_MANIFEST_NAME
    temporary_dependency = dependency_path.with_name(f".{dependency_path.name}.tmp")
    shutil.copyfile(dependency_source, temporary_dependency)
    temporary_dependency.replace(dependency_path)
    for name, destination in zip(SUPPORT_DIRECTORIES, support_paths, strict=True):
        source_directory = FLATPAK_DIRECTORY / name
        temporary_directory = destination.with_name(f".{destination.name}.tmp")
        if temporary_directory.exists():
            shutil.rmtree(temporary_directory)
        shutil.copytree(source_directory, temporary_directory)
        if destination.exists():
            shutil.rmtree(destination)
        temporary_directory.replace(destination)
    print(f"current Flatpak manifest: {manifest_path}")
    print(f"current Flatpak source: {_sha256(source)}  {source}")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "build" / "flatpak-current",
    )
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()
    try:
        prepare_flatpak_candidate(arguments.output_dir, force=arguments.force)
    except (
        OSError,
        subprocess.CalledProcessError,
        release_builder.ReleaseBuildError,
        FlatpakPreparationError,
    ) as error:
        print(f"Flatpak preparation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
