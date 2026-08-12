# LyricFlow

LyricFlow is a planned Linux-first desktop application that detects current
playback through MPRIS, resolves track identity conservatively, finds local or
provider lyrics, and displays synchronized lyrics. For non-Latin scripts, the
original line remains first, a romanized/transliterated line appears directly
under it, and an optional translation forms a third layer.

`LyricFlow` is a working name. The current version is 0.1.0.

## Current status

Stage 1 — MPRIS diagnostic core — and Stage 2 — player selection and track
identity — are **Completed**. Stage 2 passed both the complete automated gate
and real Strawberry/JessKah/multiple-player verification. The policy selects one primary player,
suppresses explainable duplicates, derives typed local/YouTube/generic source
identities, and resolves conservative artist/title candidates without fetching
lyrics. Stage 3 is Proposed and awaits explicit authorization; it and all later
stages remain unauthorized.

The [`myonctl/LyricFlow`](https://github.com/myonctl/LyricFlow) GitHub repository
is private during pre-alpha development. Its `origin` remote and default branch
use the conventional `main` workflow. The project is intended to become public
later, but LyricFlow is **not currently open source**: no open-source license
has been selected, and `LICENSE` reserves all rights until that decision is
made. Public release prerequisites are tracked in `BACKLOG.md`.

The active tree can enumerate, inspect, and watch every raw MPRIS service through
a replaceable QtDBus boundary. A separate Stage 2 policy derives interpretations
without changing those raw observations. It does not fetch lyrics, persist to
SQLite, synchronize playback, or build a GUI. See `PROJECT_STATE.md` for
evidence and `AGENT_TODO.md` for the only implementation authority.

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
- `CONTRIBUTING.md` — practical development and contribution workflow.
- `SECURITY.md` — private handling for security-sensitive reports.
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
- `docs/STAGE_1_COMPLETION.md` — completed Stage 1 implementation, automated,
  and manual evidence.
- `docs/STAGE_2_COMPLETION.md` — completed Stage 2 implementation, automated,
  and real-player evidence.
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
.venv/bin/lyricflow players list
.venv/bin/lyricflow players inspect <service>
.venv/bin/lyricflow players watch
.venv/bin/lyricflow players select
```

`doctor` checks local prerequisites only: Linux, the session D-Bus environment,
the `PySide6.QtDBus` import, writable XDG application directories, and optional
`playerctl`. It does not connect to D-Bus or discover media players. Missing
required prerequisites return exit code 1; missing optional `playerctl` is a
warning.

The `players` commands use the session D-Bus. `list` preserves every discovered
service, `inspect` renders typed raw properties and metadata, and `watch` reports
service lifecycle, property, and seek events. Expected bus errors and player
disappearance return understandable diagnostics instead of tracebacks.

`players select` preserves those raw observations, then reports the selected
player, independent alternatives, suppressed duplicates, typed source identity,
raw title/uploader, resolved artist/title, duration, confidence, transformations,
and warnings. Repeatable `--prefer PLAYER` and `--ignore PLAYER` options apply
explicit configuration to the deterministic selection policy.

`players list` returns 0 whenever service enumeration itself succeeds, including
when individual players or fields are unavailable; their diagnostics remain in
the output. It returns 1 only when the player set cannot be enumerated.
`players inspect` returns 0 for any useful partial snapshot and 1 when no useful
inspection is possible. `players watch` returns 130 after a handled Ctrl+C and
successful signal cleanup, and prints no traceback. A cleanup failure is
reported and returns 1 rather than being hidden as a successful interrupt. For
an unambiguous interrupt check, run it directly rather than through a capture
pipeline:

```bash
.venv/bin/lyricflow players watch
```

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
