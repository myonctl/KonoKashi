"""Regression tests for the public repository surface."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]

PUBLIC_DOCUMENTS = (
    "README.md",
    "AGENTS.md",
    "BACKLOG.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs/PRODUCT_SPEC.md",
    "docs/ARCHITECTURE.md",
    "docs/ROADMAP.md",
    "docs/TESTING.md",
    "docs/DEPENDENCIES.md",
    "docs/RELEASE.md",
    "docs/REFERENCES.md",
    "docs/MULTILINGUAL_SUPPORT.md",
)

MAINTAINER_ONLY_PATHS = (
    "AGENT_TODO.md",
    "CODEX_MASTER_PROMPT.md",
    "PLAN_MANIFEST.json",
    "PROJECT_STATE.md",
    "docs/MANUAL_TEST_LOG.md",
    "docs/STAGE_15_COMPLETION.md",
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
    present = [
        relative_path
        for relative_path in MAINTAINER_ONLY_PATHS
        if (REPOSITORY_ROOT / relative_path).exists()
    ]

    assert present == []
    assert "/.maintainer-private/" in (REPOSITORY_ROOT / ".gitignore").read_text(
        encoding="utf-8"
    )


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
    demo = REPOSITORY_ROOT / "docs/images/konokashi-demo.gif"
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
    )

    assert all(command in content for command in required_commands)


def test_readme_is_product_first_and_honest_about_license() -> None:
    content = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    prose = " ".join(content.split())

    assert content.startswith("# KonoKashi\n")
    assert "usable and under active development" in content
    assert "## Try it" in content
    assert "docs/images/konokashi-demo.gif" in content
    assert "source-available under the PolyForm Noncommercial License 1.0.0" in prose
    assert "OSI Open Source" in prose
    assert "## What's next" in content
    assert "AGENT_TODO.md" not in content


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
    assert '<release version="0.1.0-beta.1"' in metadata
    assert "no public release was created" in metadata


def test_beta_version_is_consistent_across_release_candidates() -> None:
    pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    package = (REPOSITORY_ROOT / "src/konokashi/__init__.py").read_text(
        encoding="utf-8"
    )
    aur = (REPOSITORY_ROOT / "packaging/aur/PKGBUILD").read_text(encoding="utf-8")
    srcinfo = (REPOSITORY_ROOT / "packaging/aur/.SRCINFO").read_text(encoding="utf-8")

    assert 'version = "0.1.0b1"' in pyproject
    assert '__version__ = "0.1.0b1"' in package
    assert 'DISPLAY_VERSION = "0.1.0-beta.1"' in package
    assert "pkgver=0.1.0beta1" in aur
    assert "_sdistver=0.1.0b1" in aur
    assert "pkgver = 0.1.0beta1" in srcinfo
    assert "source = konokashi-0.1.0b1.tar.gz" in srcinfo


def test_release_copy_excludes_maintainer_and_flatpak_worktrees() -> None:
    build_script = (REPOSITORY_ROOT / "scripts/build_release.py").read_text(
        encoding="utf-8"
    )

    for excluded in (
        ".maintainer-private",
        ".release-readiness-work",
        ".flatpak-builder",
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
    candidate_notes = (REPOSITORY_ROOT / "packaging/flatpak/README.md").read_text(
        encoding="utf-8"
    )
    assert "native directory-only dialog" in candidate_notes
    assert "desktop portal" in candidate_notes
    assert "Do not add `--filesystem=home`" in candidate_notes


def test_personal_audio_device_name_is_not_a_regression_fixture() -> None:
    content = (REPOSITORY_ROOT / "tests/test_pipewire_latency.py").read_text(
        encoding="utf-8"
    )

    assert "MOMENTUM" not in content
    assert "Synthetic Wireless Sink" in content


def test_accepted_cross_cutting_architecture_is_durable() -> None:
    native = (
        REPOSITORY_ROOT / "docs/adr/0013-python-first-hybrid-architecture.md"
    ).read_text(encoding="utf-8")
    settings = (
        REPOSITORY_ROOT / "docs/adr/0014-frontend-neutral-settings-and-themes.md"
    ).read_text(encoding="utf-8")
    product = (REPOSITORY_ROOT / "docs/PRODUCT_SPEC.md").read_text(encoding="utf-8")
    native = " ".join(native.split())
    settings = " ".join(settings.split())
    product = " ".join(product.split())

    assert all(
        phrase in native
        for phrase in (
            "Python-first",
            "PyO3",
            "maturin",
            "Retain PySide6",
            "Qt Quick/QML",
            "Textual",
            "Do not create `konokashid` now",
        )
    )
    assert all(
        phrase in settings
        for phrase in (
            "one canonical typed, versioned, validated settings schema/service",
            "`konokashi settings`",
            "human-edited XDG configuration files",
            "last-known-good runtime state",
            "hot reload",
            "declarative data only",
            "must never execute Python, shell, `eval`, plugins, or commands",
            "Machine-readable snapshot/event output",
        )
    )
    assert all(
        phrase in product
        for phrase in (
            "`konokashi tui`",
            "`konokashi settings`",
            "`konokashi follow --json`",
            "wallpaper/desktop-overlay modes",
        )
    )
