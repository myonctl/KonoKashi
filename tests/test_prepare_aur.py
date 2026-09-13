"""Current-checkout AUR preparation regressions."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts import prepare_aur
from scripts.prepare_aur import (
    CANDIDATE_SOURCE_SHA256,
    CANDIDATE_SOURCE_URL,
    AurPreparationError,
    prepare_aur_candidate,
    render_current_pkgbuild,
)


def test_current_pkgbuild_replaces_only_exact_candidate_source() -> None:
    canonical = (
        f"source=('archive::{CANDIDATE_SOURCE_URL}')\n"
        f"sha256sums=('{CANDIDATE_SOURCE_SHA256}')\n"
    )

    rendered = render_current_pkgbuild(
        canonical,
        source_name="konokashi.tar.gz",
        source_sha256="a" * 64,
    )

    assert rendered == (
        f"source=('archive::konokashi.tar.gz')\nsha256sums=('{('a' * 64)}')\n"
    )


@pytest.mark.parametrize(
    "pkgbuild",
    [
        "pkgname=example\n",
        f"source=('{CANDIDATE_SOURCE_URL}')\n",
        f"sha256sums=('{CANDIDATE_SOURCE_SHA256}')\n",
    ],
)
def test_current_pkgbuild_rejects_drifted_canonical_source(pkgbuild: str) -> None:
    with pytest.raises(AurPreparationError):
        render_current_pkgbuild(
            pkgbuild,
            source_name="konokashi.tar.gz",
            source_sha256="a" * 64,
        )


def test_candidate_uses_shared_source_and_generates_matching_srcinfo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packaging = tmp_path / "packaging"
    packaging.mkdir()
    (packaging / "PKGBUILD").write_text(
        f"source=('archive::{CANDIDATE_SOURCE_URL}')\n"
        f"sha256sums=('{CANDIDATE_SOURCE_SHA256}')\n",
        encoding="utf-8",
    )
    source = tmp_path / "konokashi.tar.gz"
    source.write_bytes(b"one exact source")

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert kwargs["cwd"] == (tmp_path / "candidate").resolve()
        return subprocess.CompletedProcess(args[0], 0, stdout=b"pkgbase = konokashi\n")

    monkeypatch.setattr(prepare_aur, "AUR_DIRECTORY", packaging)
    monkeypatch.setattr(prepare_aur.subprocess, "run", fake_run)

    pkgbuild = prepare_aur_candidate(tmp_path / "candidate", source_archive=source)

    assert pkgbuild.is_file()
    assert (pkgbuild.parent / source.name).read_bytes() == source.read_bytes()
    assert (pkgbuild.parent / ".SRCINFO").read_text(
        encoding="utf-8"
    ) == "pkgbase = konokashi\n"
    assert CANDIDATE_SOURCE_URL not in pkgbuild.read_text(encoding="utf-8")
    assert CANDIDATE_SOURCE_SHA256 not in pkgbuild.read_text(encoding="utf-8")
