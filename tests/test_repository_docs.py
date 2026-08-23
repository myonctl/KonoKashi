"""Regression tests for the repository continuation contract."""

from __future__ import annotations

import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]

REQUIRED_DOCUMENTS = (
    "README.md",
    "PROJECT_STATE.md",
    "AGENTS.md",
    "AGENT_TODO.md",
    "BACKLOG.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs/PRODUCT_SPEC.md",
    "docs/ARCHITECTURE.md",
    "docs/ROADMAP.md",
    "docs/TESTING.md",
    "docs/MANUAL_TEST_LOG.md",
    "docs/DEPENDENCIES.md",
    "docs/REFERENCES.md",
    "docs/DOCUMENTATION_STYLE.md",
    "docs/DEVELOPMENT_WORKFLOW.md",
    "docs/STAGE_COMPLETION_TEMPLATE.md",
    "docs/STAGE_1_COMPLETION.md",
    "docs/STAGE_2_COMPLETION.md",
    "docs/STAGE_3_COMPLETION.md",
    "docs/STAGE_4_COMPLETION.md",
    "docs/STAGE_5_COMPLETION.md",
    "docs/STAGE_6_COMPLETION.md",
    "docs/STAGE_7_COMPLETION.md",
    "docs/STAGE_8_COMPLETION.md",
    "docs/STAGE_9_COMPLETION.md",
    "docs/PRODUCT_GAP_RESEARCH.md",
    "docs/adr/0011-offline-romanization-routing.md",
    "docs/adr/0010-stage4-lyrics-resolution.md",
    "docs/adr/0013-python-first-hybrid-architecture.md",
    "docs/adr/0014-frontend-neutral-settings-and-themes.md",
)

REQUIRED_EVIDENCE_FIXTURES = (
    "tests/fixtures/mpris/firefox_native.json",
    "tests/fixtures/mpris/plasma_browser_integration.json",
)

REQUIRED_GITHUB_COMMUNITY_FILES = (
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/pull_request_template.md",
    ".github/workflows/ci.yml",
)

PROJECT_STATE_HEADINGS = (
    "## What currently works",
    "## What does not exist yet",
    "## Current architecture summary",
    "## Supported/tested environments",
    "## Automated test status",
    "## Manual verification status",
    "## Known issues",
    "## Current blockers",
    "## Important accepted decisions",
    "## Current repository path",
    "## Continue here",
)

COMPLETION_HEADINGS = (
    "## Scope completed",
    "## Files changed",
    "## Behavior implemented",
    "## Architecture decisions",
    "## Dependencies added/removed",
    "## Database/schema changes",
    "## Tests added",
    "## Automated checks run",
    "## Manual verification",
    "## Known limitations",
    "## Bugs / technical debt",
    "## Documentation updated",
    "## Out-of-scope work intentionally not done",
    "## Git state / commit",
    "## Handoff",
)


def test_required_repository_documents_exist() -> None:
    missing = [
        relative_path
        for relative_path in REQUIRED_DOCUMENTS
        if not (REPOSITORY_ROOT / relative_path).is_file()
    ]

    assert missing == []


def test_sanitized_mpris_evidence_fixtures_are_preserved() -> None:
    fixtures = [
        json.loads((REPOSITORY_ROOT / path).read_text(encoding="utf-8"))
        for path in REQUIRED_EVIDENCE_FIXTURES
    ]

    assert [fixture["service"] for fixture in fixtures] == [
        "firefox.instance_1_95",
        "plasma-browser-integration",
    ]
    assert all("/home/" not in json.dumps(fixture) for fixture in fixtures)


def test_github_community_and_ci_files_exist() -> None:
    missing = [
        relative_path
        for relative_path in REQUIRED_GITHUB_COMMUNITY_FILES
        if not (REPOSITORY_ROOT / relative_path).is_file()
    ]

    assert missing == []


def test_github_actions_runs_the_required_quality_gate() -> None:
    content = (REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    required_commands = (
        'python -m pip install ".[dev]"',
        "python -m pytest",
        "python -m ruff check .",
        "python -m ruff format --check .",
        "python -m mypy src",
    )

    assert all(command in content for command in required_commands)


def test_git_attribution_policy_is_durable() -> None:
    agents = (REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    workflow = (REPOSITORY_ROOT / "docs/DEVELOPMENT_WORKFLOW.md").read_text(
        encoding="utf-8"
    )
    combined = f"{agents}\n{workflow}"

    required_policy = (
        "git config user.name",
        "git config user.email",
        "gh auth status",
        "myonctl",
        "Co-authored-by:",
        "CI-generated commits",
    )

    assert all(rule in combined for rule in required_policy)


def test_readme_indexes_the_continuation_documents() -> None:
    content = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    assert all(relative_path in content for relative_path in REQUIRED_DOCUMENTS[1:])


def test_project_state_contains_the_continuation_contract() -> None:
    content = (REPOSITORY_ROOT / "PROJECT_STATE.md").read_text(encoding="utf-8")

    assert content.startswith("# LyricFlow Project State\n")
    assert all(heading in content for heading in PROJECT_STATE_HEADINGS)
    assert "/home/example/Documents/LyricFlow/" in content


def test_agent_todo_proposes_stage_ten_without_authorizing_it() -> None:
    content = (REPOSITORY_ROOT / "AGENT_TODO.md").read_text(encoding="utf-8")

    assert content.startswith("# Proposed next stage — not authorized\n")
    assert (
        "Stage 9 — Music-directory scanner and batch downloads is **Completed**"
        in content
    )
    assert "Stage 10 — Packaging and v1 release is Proposed / unauthorized" in content
    assert "no currently authorized implementation stage" in content
    assert "Do not implement Stage 10" in content
    assert "Wait for explicit\nauthorization before activating Stage 10" in content


def test_manifest_is_inventory_not_authority() -> None:
    manifest = json.loads(
        (REPOSITORY_ROOT / "PLAN_MANIFEST.json").read_text(encoding="utf-8")
    )

    assert manifest["implementation_authority"] == "AGENT_TODO.md"
    assert manifest["authorized_stage"] is None
    assert manifest["proposed_stage"] == "Stage 10 — Packaging and v1 release"
    assert all((REPOSITORY_ROOT / path).is_file() for path in manifest["files"])


def test_stage_completion_template_keeps_every_required_section() -> None:
    content = (REPOSITORY_ROOT / "docs/STAGE_COMPLETION_TEMPLATE.md").read_text(
        encoding="utf-8"
    )

    assert all(heading in content for heading in COMPLETION_HEADINGS)


def test_runtime_source_does_not_embed_the_local_repository_path() -> None:
    offending: list[str] = []
    for path in (REPOSITORY_ROOT / "src").rglob("*.py"):
        if "/home/example/Documents/LyricFlow/" in path.read_text(encoding="utf-8"):
            offending.append(str(path.relative_to(REPOSITORY_ROOT)))

    assert offending == []


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
            "Do not create `lyricflowd` now",
        )
    )
    assert all(
        phrase in settings
        for phrase in (
            "one canonical typed, versioned, validated settings schema/service",
            "`lyricflow settings`",
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
            "`lyricflow tui`",
            "`lyricflow settings`",
            "`lyricflow follow --json`",
            "wallpaper/desktop-overlay modes",
        )
    )
