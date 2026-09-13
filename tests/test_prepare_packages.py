"""Combined package-candidate source-identity regressions."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import prepare_packages


def test_both_package_routes_receive_the_same_exact_sdist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "packages"
    source = output / "artifacts" / "konokashi.tar.gz"
    received: list[Path] = []

    def fake_build_release(directory: Path, *, force: bool) -> tuple[Path, ...]:
        assert directory == output.resolve() / "artifacts"
        directory.mkdir(parents=True)
        source.write_bytes(b"shared release source")
        return (directory / "konokashi.whl", source)

    def fake_prepare(
        directory: Path, *, force: bool, source_archive: Path | None
    ) -> Path:
        assert directory.parent == output.resolve()
        assert source_archive is not None
        received.append(source_archive)
        return directory

    monkeypatch.setattr(
        prepare_packages.build_release, "build_release", fake_build_release
    )
    monkeypatch.setattr(
        prepare_packages.prepare_aur, "prepare_aur_candidate", fake_prepare
    )
    monkeypatch.setattr(
        prepare_packages.prepare_flatpak, "prepare_flatpak_candidate", fake_prepare
    )

    selected = prepare_packages.prepare_package_candidates(output)

    assert selected == source
    assert received == [source, source]
