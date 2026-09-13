#!/usr/bin/env python3
"""Prepare AUR and Flatpak candidates from one reproducible source archive."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from scripts import build_release, prepare_aur, prepare_flatpak
else:
    import build_release
    import prepare_aur
    import prepare_flatpak

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PackagePreparationError(RuntimeError):
    """A controlled combined-package preparation failure."""


def prepare_package_candidates(output_directory: Path, *, force: bool = False) -> Path:
    """Build once and pass the exact resulting sdist to both package routes."""

    output = output_directory.expanduser().resolve()
    artifacts = build_release.build_release(output / "artifacts", force=force)
    source_archives = [path for path in artifacts if path.name.endswith(".tar.gz")]
    if len(source_archives) != 1:
        raise PackagePreparationError(
            "release builder did not return exactly one source distribution"
        )
    source = source_archives[0]
    prepare_aur.prepare_aur_candidate(
        output / "aur", force=force, source_archive=source
    )
    prepare_flatpak.prepare_flatpak_candidate(
        output / "flatpak", force=force, source_archive=source
    )
    print(f"shared package source: {source}")
    return source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "build" / "packages-current",
    )
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()
    try:
        prepare_package_candidates(arguments.output_dir, force=arguments.force)
    except (
        OSError,
        subprocess.CalledProcessError,
        build_release.ReleaseBuildError,
        prepare_aur.AurPreparationError,
        prepare_flatpak.FlatpakPreparationError,
        PackagePreparationError,
    ) as error:
        print(f"Package preparation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
