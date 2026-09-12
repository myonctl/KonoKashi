"""Current-checkout Flatpak preparation regressions."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import prepare_flatpak
from scripts.prepare_flatpak import (
    CANDIDATE_SOURCE_SHA256,
    CANDIDATE_SOURCE_URL,
    DEPENDENCY_MANIFEST_NAME,
    MANIFEST_NAME,
    SUPPORT_DIRECTORIES,
    FlatpakPreparationError,
    prepare_flatpak_candidate,
    render_current_manifest,
)


def test_current_manifest_replaces_only_exact_candidate_source() -> None:
    canonical = "\n".join(
        (
            "id: example",
            f"url: {CANDIDATE_SOURCE_URL}",
            f"sha256: {CANDIDATE_SOURCE_SHA256}",
            "",
        )
    )

    rendered = render_current_manifest(
        canonical,
        source_uri="file:///tmp/candidate.tar.gz",
        source_sha256="a" * 64,
    )

    assert rendered == (
        f"id: example\nurl: file:///tmp/candidate.tar.gz\nsha256: {'a' * 64}\n"
    )


@pytest.mark.parametrize(
    "manifest",
    [
        "id: example\n",
        f"url: {CANDIDATE_SOURCE_URL}\n",
        f"sha256: {CANDIDATE_SOURCE_SHA256}\n",
        f"url: {CANDIDATE_SOURCE_URL}\nurl: {CANDIDATE_SOURCE_URL}\n"
        f"sha256: {CANDIDATE_SOURCE_SHA256}\n",
    ],
)
def test_current_manifest_rejects_drifted_or_ambiguous_canonical_source(
    manifest: str,
) -> None:
    with pytest.raises(FlatpakPreparationError):
        render_current_manifest(
            manifest,
            source_uri="file:///tmp/candidate.tar.gz",
            source_sha256="a" * 64,
        )


def test_generated_manifest_names_stay_colocated_for_builder_includes() -> None:
    output = Path("build/flatpak-current")

    assert output / MANIFEST_NAME == Path(
        "build/flatpak-current/io.github.myonctl.KonoKashi.yaml"
    )
    assert output / DEPENDENCY_MANIFEST_NAME == Path(
        "build/flatpak-current/python3-dependencies.json"
    )
    assert tuple(output / name for name in SUPPORT_DIRECTORIES) == (
        Path("build/flatpak-current/portal-parent-bridge"),
    )


def test_candidate_copies_colocated_manifest_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packaging = tmp_path / "packaging"
    bridge = packaging / "portal-parent-bridge"
    bridge.mkdir(parents=True)
    (bridge / "portal_parent.cpp").write_text("bridge source\n", encoding="utf-8")
    (packaging / DEPENDENCY_MANIFEST_NAME).write_text("[]\n", encoding="utf-8")
    (packaging / MANIFEST_NAME).write_text(
        f"url: {CANDIDATE_SOURCE_URL}\nsha256: {CANDIDATE_SOURCE_SHA256}\n",
        encoding="utf-8",
    )

    def fake_build_release(output: Path, *, force: bool) -> tuple[Path, ...]:
        assert force
        output.mkdir(parents=True)
        artifact = output / "konokashi-test.tar.gz"
        artifact.write_bytes(b"candidate")
        return (artifact,)

    monkeypatch.setattr(prepare_flatpak, "FLATPAK_DIRECTORY", packaging)
    monkeypatch.setattr(
        prepare_flatpak.release_builder,
        "build_release",
        fake_build_release,
    )
    output = tmp_path / "candidate"

    manifest = prepare_flatpak_candidate(output, force=True)

    assert manifest == output / MANIFEST_NAME
    assert (output / "portal-parent-bridge/portal_parent.cpp").read_text(
        encoding="utf-8"
    ) == "bridge source\n"
