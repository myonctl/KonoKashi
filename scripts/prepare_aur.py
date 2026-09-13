#!/usr/bin/env python3
"""Prepare an unpublished AUR recipe from one exact current source archive."""

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

AUR_DIRECTORY = PROJECT_ROOT / "packaging" / "aur"
PKGBUILD_NAME = "PKGBUILD"
SRCINFO_NAME = ".SRCINFO"
CANDIDATE_SOURCE_URL = (
    "https://github.com/myonctl/KonoKashi/releases/download/"
    "v0.1.0-beta.2/konokashi-0.1.0b2.tar.gz"
)
CANDIDATE_SOURCE_SHA256 = "0" * 64


class AurPreparationError(RuntimeError):
    """A controlled current-candidate preparation failure."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def render_current_pkgbuild(
    pkgbuild: str,
    *,
    source_name: str,
    source_sha256: str,
) -> str:
    """Replace only the canonical fail-closed source and checksum."""

    if pkgbuild.count(CANDIDATE_SOURCE_URL) != 1:
        raise AurPreparationError(
            "canonical PKGBUILD does not contain its exact candidate source URL"
        )
    if pkgbuild.count(CANDIDATE_SOURCE_SHA256) != 1:
        raise AurPreparationError(
            "canonical PKGBUILD does not contain its exact candidate source hash"
        )
    if Path(source_name).name != source_name:
        raise AurPreparationError("package source name must be a plain file name")
    return pkgbuild.replace(CANDIDATE_SOURCE_URL, source_name).replace(
        CANDIDATE_SOURCE_SHA256,
        source_sha256,
    )


def _replace_file(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _source_from_release(output: Path, *, force: bool) -> Path:
    artifacts = release_builder.build_release(output / "artifacts", force=force)
    source_archives = [path for path in artifacts if path.name.endswith(".tar.gz")]
    if len(source_archives) != 1:
        raise AurPreparationError(
            "release builder did not return exactly one source distribution"
        )
    return source_archives[0]


def prepare_aur_candidate(
    output_directory: Path,
    *,
    force: bool = False,
    source_archive: Path | None = None,
) -> Path:
    """Write a local-only recipe and matching .SRCINFO from one sdist."""

    output = output_directory.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = (
        _source_from_release(output, force=force)
        if source_archive is None
        else source_archive.expanduser().resolve()
    )
    if not source.is_file() or not source.name.endswith(".tar.gz"):
        raise AurPreparationError(
            "shared package source must be an existing .tar.gz archive"
        )
    archive_path = output / source.name
    pkgbuild_path = output / PKGBUILD_NAME
    srcinfo_path = output / SRCINFO_NAME
    if not force and any(
        path.exists() for path in (archive_path, pkgbuild_path, srcinfo_path)
    ):
        raise AurPreparationError(
            "AUR preparation output already exists; pass --force to replace it"
        )
    canonical = (AUR_DIRECTORY / PKGBUILD_NAME).read_text(encoding="utf-8")
    rendered = render_current_pkgbuild(
        canonical,
        source_name=source.name,
        source_sha256=_sha256(source),
    )
    temporary_archive = archive_path.with_name(f".{archive_path.name}.tmp")
    shutil.copyfile(source, temporary_archive)
    temporary_archive.replace(archive_path)
    _replace_file(pkgbuild_path, rendered.encode("utf-8"))
    source_info = subprocess.run(
        ["makepkg", "--printsrcinfo"],
        cwd=output,
        check=True,
        capture_output=True,
    ).stdout
    _replace_file(srcinfo_path, source_info)
    print(f"current AUR recipe: {pkgbuild_path}")
    print(f"current AUR source: {_sha256(source)}  {archive_path}")
    return pkgbuild_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "build" / "aur-current",
    )
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()
    try:
        prepare_aur_candidate(arguments.output_dir, force=arguments.force)
    except (
        OSError,
        subprocess.CalledProcessError,
        release_builder.ReleaseBuildError,
        AurPreparationError,
    ) as error:
        print(f"AUR preparation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
