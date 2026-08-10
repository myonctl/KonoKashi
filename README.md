# LyricFlow

LyricFlow is a planned Linux-first desktop application that detects current
playback through MPRIS, resolves track identity conservatively, finds local or
provider lyrics, and displays synchronized lyrics. For non-Latin scripts, the
original line remains first, a romanized/transliterated line appears directly
under it, and an optional translation forms a third layer.

`LyricFlow` is a working name. The current version is 0.1.0.

## Current status

Stage 0 — repository foundation and workflow hardening — is Implemented. The
package, local `doctor` diagnostic, quality tooling, CI definition, architecture
skeleton, and repository continuation process exist.

No MPRIS player discovery or other product feature is implemented in the active
tree. No implementation stage is currently authorized. Stage 1 — MPRIS
diagnostic core — is Proposed and awaiting explicit user authorization. See
`PROJECT_STATE.md` for current evidence and `AGENT_TODO.md` for the only
implementation authority.

## Core product rules

- Linux/KDE Plasma first, with Strawberry and Firefox through KDE Plasma Browser
  Integration as initial player targets.
- MPRIS playback detection; no audio fingerprinting for v1.
- Local, user-approved, and cached lyrics outrank network results.
- Never silently change audio metadata or hide uncertain matches.
- Preserve original lyric script; align romanization/transliteration underneath
  and optional translation below that, with provenance and user corrections.
- Keep provider, player, language, storage, and UI integrations replaceable.
- Keep operation local-first and do no slow external work on the UI thread.
- Continue safely from repository evidence without prior conversation context.

The complete behavioral authority is `docs/PRODUCT_SPEC.md`.

## Repository documentation

Read the first three files at the start of every session:

- `PROJECT_STATE.md` — fastest current-reality summary and exact next action.
- `AGENTS.md` — durable constraints for all contributors and agents.
- `AGENT_TODO.md` — the only document that authorizes implementation.

Supporting authorities:

- `BACKLOG.md` — durable future features, debt, and investigations; never
  authorization.
- `CHANGELOG.md` — notable repository changes.
- `docs/PRODUCT_SPEC.md` — product requirements and non-goals.
- `docs/ARCHITECTURE.md` — actual/planned boundaries, flows, and models.
- `docs/ROADMAP.md` — ordered stages and their acceptance gates.
- `docs/TESTING.md` — automated strategy, quality gate, fixtures, and manual
  matrix.
- `docs/MANUAL_TEST_LOG.md` — append-only real-world verification evidence.
- `docs/DEPENDENCIES.md` — dependency rationale and replacement paths.
- `docs/REFERENCES.md` — external primary technical references.
- `docs/DOCUMENTATION_STYLE.md` — evidence, status, path, TODO, and ADR rules.
- `docs/DEVELOPMENT_WORKFLOW.md` — exact start, implementation, Git, bug, and
  handoff workflow.
- `docs/STAGE_COMPLETION_TEMPLATE.md` — required stage completion report.
- `docs/adr/` — accepted and historical architecture/process decisions.

`PLAN_MANIFEST.json` is a machine-readable document inventory, not an authority.
`CODEX_MASTER_PROMPT.md` is a convenience entry point that redirects agents to
the current repository state; it cannot authorize a stage.

## Development setup

LyricFlow requires Python 3.11 or newer. From the repository root:

```bash
python -m venv .venv
.venv/bin/python -m pip install ".[dev]"
```

PySide6 is the only runtime dependency. pytest, Ruff, and mypy are development
dependencies. Rationale and usage boundaries are recorded in
`docs/DEPENDENCIES.md`.

## Implemented commands

```bash
.venv/bin/lyricflow --version
.venv/bin/lyricflow doctor
```

`doctor` checks local prerequisites only: Linux, the session D-Bus environment,
the `PySide6.QtDBus` import, writable XDG application directories, and optional
`playerctl`. It does not connect to D-Bus or discover media players. Missing
required prerequisites return exit code 1; missing optional `playerctl` is a
warning.

## Quality gate

Run from the repository root:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy src
.venv/bin/lyricflow --version
.venv/bin/lyricflow doctor
```

Exact current results belong in `PROJECT_STATE.md` and `AGENT_TODO.md`.
