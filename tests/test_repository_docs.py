"""Regression tests for the public repository surface."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]

PUBLIC_DOCUMENTS = (
    "README.md",
    "BACKLOG.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "packaging/README.md",
    "SECURITY.md",
)

MAINTAINER_ONLY_PATHS = (
    "AGENTS.md",
    "AGENT_TODO.md",
    "CODEX_MASTER_PROMPT.md",
    "PLAN_MANIFEST.json",
    "PROJECT_STATE.md",
    "docs",
)

REQUIRED_GITHUB_FILES = (
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/pull_request_template.md",
    ".github/workflows/ci.yml",
)


def test_public_repository_documents_exist() -> None:
    missing = [
        relative_path
        for relative_path in (*PUBLIC_DOCUMENTS, *REQUIRED_GITHUB_FILES)
        if not (REPOSITORY_ROOT / relative_path).is_file()
    ]

    assert missing == []


def test_license_is_canonical_polyform_noncommercial_1_0_0() -> None:
    content = (REPOSITORY_ROOT / "LICENSE").read_bytes()

    assert hashlib.sha256(content).hexdigest() == (
        "ffcca38841adb694b6f380647e15f17c446a4d1656fed51a1e2041d064c94cc8"
    )


def test_maintainer_only_documents_are_not_in_public_tree() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--", *MAINTAINER_ONLY_PATHS],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    assert tracked == []
    ignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/.maintainer-private/" in ignore
    assert "/docs/" in ignore
    assert "/AGENTS.md" in ignore


def test_public_docs_do_not_embed_maintainer_home_path() -> None:
    private_marker = "/" + "home/" + "myon"
    offending = [
        relative_path
        for relative_path in PUBLIC_DOCUMENTS
        if private_marker
        in (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
    ]

    assert offending == []


def test_readme_demo_is_bounded_animated_and_sanitized() -> None:
    demo = REPOSITORY_ROOT / "assets/readme/konokashi-demo.gif"
    content = demo.read_bytes()

    assert content.startswith((b"GIF87a", b"GIF89a"))
    assert len(content) < 1_500_000
    assert content.count(b"\x21\xf9\x04") >= 24
    assert b"/home/" not in content
    assert b"myon" not in content.lower()


def test_sanitized_mpris_evidence_fixtures_are_preserved() -> None:
    paths = (
        "tests/fixtures/mpris/firefox_native.json",
        "tests/fixtures/mpris/plasma_browser_integration.json",
    )
    fixtures = [
        json.loads((REPOSITORY_ROOT / path).read_text(encoding="utf-8"))
        for path in paths
    ]

    assert [fixture["service"] for fixture in fixtures] == [
        "firefox.instance_1_95",
        "plasma-browser-integration",
    ]
    assert all("/home/" not in json.dumps(fixture) for fixture in fixtures)


def test_github_actions_runs_the_required_quality_gate() -> None:
    content = (REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    required_commands = (
        'python -m pip install ".[dev]"',
        "python -m pytest",
        "python -m ruff check .",
        "python -m ruff format --check .",
        "python -m mypy src",
        "python scripts/build_release.py",
        "desktop-file-validate",
        "appstreamcli validate --pedantic --no-net",
        "/tmp/gitleaks dir",
        "GITLEAKS_SHA256",
        "QT_QPA_PLATFORM: offscreen",
        "PlaybackClock is NativePlaybackClock",
        "--yes g++ libegl1",
    )

    assert all(command in content for command in required_commands)


def test_advertised_python_versions_are_bounded_and_exercised_in_ci() -> None:
    pyproject = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    workflow = (REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    contributing = (REPOSITORY_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    aur = (REPOSITORY_ROOT / "packaging/aur/PKGBUILD").read_text(encoding="utf-8")

    assert pyproject["project"]["requires-python"] == ">=3.11,<3.15"
    assert "pybind11==3.1.0" in pyproject["build-system"]["requires"]
    for version in ("3.11", "3.12", "3.13", "3.14"):
        assert f'"Programming Language :: Python :: {version}"' in (
            REPOSITORY_ROOT / "pyproject.toml"
        ).read_text(encoding="utf-8")
    assert 'python-version: ["3.11", "3.12", "3.13", "3.14"]' in workflow
    assert "python-version: ${{ matrix.python-version }}" in workflow
    assert "Python 3.11 through 3.14" in readme
    assert "Python 3.11 through 3.14" in contributing
    assert "'python>=3.11'" in aur
    assert "'python<3.15'" in aur
    assert "'pybind11'" in aur
    assert "arch=('x86_64' 'aarch64')" in aur


def test_security_policy_uses_enabled_private_reporting_route() -> None:
    policy = (REPOSITORY_ROOT / "SECURITY.md").read_text(encoding="utf-8")

    assert "Private vulnerability reporting is enabled" in policy
    assert "Before this repository becomes public" not in policy


def test_readme_is_product_first_and_honest_about_license() -> None:
    content = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    prose = " ".join(content.split())

    assert content.startswith("# KonoKashi\n")
    assert "usable and under active development" in content
    assert "## Try it" in content
    assert "assets/readme/konokashi-demo.gif" in content
    assert "source-available under the PolyForm Noncommercial License 1.0.0" in prose
    assert "OSI Open Source" in prose
    assert "## What's next" in content
    assert "AGENT_TODO.md" not in content


def test_readme_distinguishes_current_beta2_from_historical_beta1() -> None:
    content = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    assert "0.1.0-beta.2" in content
    assert "has not been published" in content
    assert "0.1.0-beta.1 artifacts remain available as historical" in content
    assert "v0.1.0-beta.1/konokashi-0.1.0b1.tar.gz" not in content
    assert "normal-user beta.2 package URL will be documented only after" in content


def test_runtime_source_does_not_embed_a_maintainer_home_path() -> None:
    private_marker = "/" + "home/" + "myon"
    offending = [
        str(path.relative_to(REPOSITORY_ROOT))
        for path in (REPOSITORY_ROOT / "src").rglob("*.py")
        if private_marker in path.read_text(encoding="utf-8")
    ]

    assert offending == []


def test_appstream_metadata_matches_the_desktop_identity() -> None:
    metadata = (
        REPOSITORY_ROOT
        / "src/konokashi/resources/io.github.myonctl.KonoKashi.metainfo.xml"
    ).read_text(encoding="utf-8")

    assert "<id>io.github.myonctl.KonoKashi</id>" in metadata
    assert (
        '<launchable type="desktop-id">io.github.myonctl.KonoKashi.desktop</launchable>'
    ) in metadata
    assert "<metadata_license>CC0-1.0</metadata_license>" in metadata
    assert "<project_license>PolyForm-Noncommercial-1.0.0</project_license>" in metadata
    assert '<release version="0.1.0-beta.2"' in metadata
    assert "publication awaits the beta.2 quality gates" in metadata
    assert '<release version="0.1.0-beta.1"' in metadata
    assert "First public beta of KonoKashi" in metadata


def test_beta_version_is_consistent_across_release_candidates() -> None:
    pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    package = (REPOSITORY_ROOT / "src/konokashi/__init__.py").read_text(
        encoding="utf-8"
    )
    aur = (REPOSITORY_ROOT / "packaging/aur/PKGBUILD").read_text(encoding="utf-8")
    srcinfo = (REPOSITORY_ROOT / "packaging/aur/.SRCINFO").read_text(encoding="utf-8")

    assert 'version = "0.1.0b2"' in pyproject
    assert '__version__ = "0.1.0b2"' in package
    assert 'DISPLAY_VERSION = "0.1.0-beta.2"' in package
    assert "pkgver=0.1.0beta2" in aur
    assert "_sdistver=0.1.0b2" in aur
    assert "pkgver = 0.1.0beta2" in srcinfo
    assert (
        "source = konokashi-0.1.0b2.tar.gz::https://github.com/myonctl/KonoKashi/"
        "releases/download/v0.1.0-beta.2/konokashi-0.1.0b2.tar.gz"
    ) in srcinfo
    assert (
        "sha256sums = 0000000000000000000000000000000000000000000000000000000000000000"
        in srcinfo
    )


def test_aur_dependency_graph_has_separate_candidates_for_real_gaps() -> None:
    aur_root = REPOSITORY_ROOT / "packaging/aur"
    package = (aur_root / "PKGBUILD").read_text(encoding="utf-8")
    srcinfo = (aur_root / ".SRCINFO").read_text(encoding="utf-8")
    cutlet = (aur_root / "dependencies/python-cutlet/PKGBUILD").read_text(
        encoding="utf-8"
    )
    jaconv = (aur_root / "dependencies/python-jaconv/PKGBUILD").read_text(
        encoding="utf-8"
    )
    unidic = (aur_root / "dependencies/python-unidic-lite/PKGBUILD").read_text(
        encoding="utf-8"
    )

    assert "'pypinyin>=0.55'" in package
    assert "python-pypinyin" not in package
    assert "depends = pypinyin>=0.55" in srcinfo
    assert "depends = python-pypinyin" not in srcinfo

    assert "pkgname=python-cutlet" in cutlet
    assert "pkgver=0.5.2" in cutlet
    for dependency in (
        "'python-fugashi>=1.5.2'",
        "'python-jaconv>=0.5.0'",
        "'python-mojimoji>=0.0.13'",
    ):
        assert dependency in cutlet
    assert "pkgname=python-jaconv" in jaconv
    assert "pkgver=0.5.0" in jaconv
    assert "pkgname=python-unidic-lite" in unidic
    assert "license=('MIT' 'BSD-3-Clause')" in unidic
    assert "pip install" not in unidic


def test_later_package_formats_have_an_explicit_maintenance_gate() -> None:
    status = (REPOSITORY_ROOT / "packaging/README.md").read_text(encoding="utf-8")

    for package_format in ("`.deb`", "`.rpm`", "AppImage"):
        assert package_format in status
    assert status.count("Deferred") >= 3
    assert "must not vendor dependencies" in status
    assert "`--nodeps` or `--nocheck`" in status


def test_release_copy_excludes_maintainer_and_flatpak_worktrees() -> None:
    build_script = (REPOSITORY_ROOT / "scripts/build_release.py").read_text(
        encoding="utf-8"
    )

    for excluded in (
        ".maintainer-private",
        ".release-readiness-work",
        ".flatpak-builder",
        "*.so",
    ):
        assert f'"{excluded}"' in build_script


def test_flatpak_manifest_uses_narrow_runtime_permissions() -> None:
    manifest = (
        REPOSITORY_ROOT / "packaging/flatpak/io.github.myonctl.KonoKashi.yaml"
    ).read_text(encoding="utf-8")

    assert "--talk-name=org.mpris.MediaPlayer2.*" in manifest
    assert "--filesystem=xdg-music:ro" in manifest
    assert "--filesystem=home" not in manifest
    assert "--filesystem=host" not in manifest
    assert "--socket=session-bus" not in manifest
    assert "type: archive" in manifest
    assert "name: portal-parent-bridge" in manifest
    assert "path: portal-parent-bridge" in manifest
    assert "name: python3-pybind11-build" in manifest
    assert "pybind11-3.1.0-py3-none-any.whl" in manifest
    assert (
        "sha256: b8488090f8acffbcb6b5d6a85571a6827a0a2981ffb75e5a0b27b87c4a6b7dd0"
        in manifest
    )
    assert (
        "https://github.com/myonctl/KonoKashi/releases/download/"
        "v0.1.0-beta.2/konokashi-0.1.0b2.tar.gz"
    ) in manifest
    assert (
        "sha256: 0000000000000000000000000000000000000000000000000000000000000000"
        in manifest
    )
    candidate_notes = (REPOSITORY_ROOT / "packaging/flatpak/README.md").read_text(
        encoding="utf-8"
    )
    assert "asynchronous directory chooser" in candidate_notes
    assert "outside Flatpak it retains" in candidate_notes
    assert "desktop portal" in candidate_notes
    assert "Qt6::GuiPrivate" in candidate_notes
    assert "runtime, SDK, and PySide BaseApp branches aligned" in candidate_notes
    assert "Do not add `--filesystem=home`" in candidate_notes
    assert "python scripts/prepare_flatpak.py" in candidate_notes
    assert "changes no tracked file" in candidate_notes
    bridge = REPOSITORY_ROOT / "packaging/flatpak/portal-parent-bridge"
    assert (bridge / "CMakeLists.txt").is_file()
    assert (bridge / "portal_parent.cpp").is_file()


def test_personal_audio_device_name_is_not_a_regression_fixture() -> None:
    content = (REPOSITORY_ROOT / "tests/test_pipewire_latency.py").read_text(
        encoding="utf-8"
    )

    assert "MOMENTUM" not in content
    assert "Synthetic Wireless Sink" in content
