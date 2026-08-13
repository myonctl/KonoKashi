# LyricFlow

LyricFlow is a planned Linux-first desktop application that detects current
playback through MPRIS, resolves track identity conservatively, finds local or
provider lyrics, and displays synchronized lyrics. For non-Latin scripts, the
original line remains first, a romanized/transliterated line appears directly
under it, and an optional translation forms a third layer.

`LyricFlow` is a working name. The current version is 0.1.0.

## Current status

Stages 1 through 4 are **Completed**. Stage 5 — Romanization and multilingual
layers — is Active and implemented locally; automated verification is in
green and real manual verification/publication remain pending. Stage 3 —
persistence foundation — passed
the complete automated gate and real restart/persistence verification for
schema initialization, approved corrections, and player settings. The policy
selects one primary player,
suppresses explainable duplicates, derives typed local/YouTube/generic source
identities, resolves conservative artist/title candidates, and persists
approved corrections and accepted player settings in local SQLite storage.
Stage 4 — lyrics resolution — passed its complete automated gate and accepted
real Strawberry sidecar, LRCLIB, fresh-process offline-cache, and conservative
no-result verification.

The [`myonctl/LyricFlow`](https://github.com/myonctl/LyricFlow) GitHub repository
is private during pre-alpha development. Its `origin` remote and default branch
use the conventional `main` workflow. The project is intended to become public
later, but LyricFlow is **not currently open source**: no open-source license
has been selected, and `LICENSE` reserves all rights until that decision is
made. Public release prerequisites are tracked in `BACKLOG.md`.

The active tree can enumerate, inspect, and watch every raw MPRIS service through
a replaceable QtDBus boundary. A separate Stage 2 policy derives interpretations
without changing those raw observations. Stage 3 adds XDG-local SQLite
migrations and typed repositories for identities, corrections, settings, and
the provider-neutral lyric foundation. Stage 4 adds original-only LRC/plain
parsing, adjacent and supported embedded sources, restart-safe cache/matches,
and read-only LRCLIB retrieval. Stage 5 adds offline, stable-line-aligned
romanization/transliteration candidates, provider/import boundaries,
provenance/uncertainty, durable user approval/rejection/reset, translation
structure, and layer settings. It does not synchronize playback or build a
GUI. See `PROJECT_STATE.md` for evidence and `AGENT_TODO.md` for the only
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
- `docs/STAGE_3_COMPLETION.md` — completed Stage 3 implementation, automated,
  storage, restart, cleanup, publication, and CI evidence.
- `docs/STAGE_4_COMPLETION.md` — completed Stage 4 implementation, automated,
  local/provider/offline/no-result, publication, and CI evidence.
- `docs/STAGE_5_COMPLETION.md` — Stage 5 implementation checkpoint and pending
  automated/publication/manual verification gates.
- `docs/adr/0010-stage4-lyrics-resolution.md` — accepted Stage 4 precedence,
  matching, cache/offline/refresh, privacy, and schema policy.
- `docs/adr/0011-offline-romanization-routing.md` — accepted Stage 5 script
  routing, offline engine, style, ambiguity, and replacement policy.
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

PySide6, HTTPX, Mutagen, Cutlet/Fugashi/UniDic-lite, and PyICU are runtime
dependencies. PyICU requires system ICU development headers when installed
from PyPI source. pytest, Ruff, and mypy are development dependencies.
Rationale, licensing cautions, and usage boundaries are recorded in
`docs/DEPENDENCIES.md`.

## Implemented commands

```bash
.venv/bin/lyricflow --version
.venv/bin/lyricflow doctor
.venv/bin/lyricflow players list
.venv/bin/lyricflow players inspect <service>
.venv/bin/lyricflow players watch
.venv/bin/lyricflow players select
.venv/bin/lyricflow storage status
.venv/bin/lyricflow storage migrate
.venv/bin/lyricflow storage settings show
.venv/bin/lyricflow lyrics current
.venv/bin/lyricflow lyrics current --offline
.venv/bin/lyricflow lyrics current --refresh
.venv/bin/lyricflow lyrics romanize current --language ja
.venv/bin/lyricflow lyrics representations current --offline
.venv/bin/lyricflow storage display show
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
explicit configuration to the deterministic selection policy. A bare selector
matches only the case-insensitive MPRIS service family: `firefox` matches
`firefox` and `firefox.instance_*`, but never another service merely because
its Identity, DesktopEntry, title, or artist mentions Firefox. Use
`service:NAME` for one exact service, `family:NAME` for an explicit service
family, `identity:NAME` for exact Identity, or `desktop-entry:NAME` for exact
DesktopEntry matching. Ignored selectors take precedence when the same player
is also preferred.

`storage status` is read-only and reports the XDG database path, schema and
integrity state, and safe repository counts without dumping media paths or lyric
content. `storage migrate` explicitly initializes or upgrades the database
without destructive reset. `storage settings set --prefer ... --ignore ...`
atomically replaces durable player configuration; `players select` uses it when
command-line values are absent. A selected stable source can be deliberately
corrected with `--approve-title` plus one or more `--approve-artist` values and
reset with `--reset-override`. Session-only generic sources reject approval.

The default database is
`$XDG_DATA_HOME/lyricflow/lyricflow.sqlite3`, falling back to
`$HOME/.local/share/lyricflow/lyricflow.sqlite3`. Tests inject isolated temporary
paths and never depend on the current working directory.

`lyrics current` selects and resolves the current track, then applies approved,
adjacent LRC, supported read-only embedded, durable High-confidence cache, and
LRCLIB exact/search precedence. Normal output includes provenance, confidence,
evidence, cache/network state, unsafe alternatives, line/timing counts, and a
three-line preview; use `--full` only when deliberately printing complete lyric
content. `--offline` forbids HTTP and reuses only local/durable data. `--refresh`
bypasses automatic provider cache/matches while preserving approved data and a
prior usable result on failure. The two flags cannot be combined.

`lyrics romanize current` transforms each canonical original line locally and
persists generated candidates by stable line ID. Use `--language ja`, `ko`, or
`zh` when explicit metadata is needed to disambiguate short or Han-only text;
Han-only input is not guessed as Japanese or Chinese. `--regenerate` replaces
only generated cache for the selected `--line-id` values and never overwrites a
user approval. `lyrics representations current` shows bounded stacked previews,
candidate/decision counts, provenance, generator/version, uncertainty, missing
alignment, and inherited original timing.

The safe correction commands operate only on the current resolved lyric
document and confirm its document and line IDs:

```bash
.venv/bin/lyricflow lyrics representations set current \
  --offline --line-id LINE_ID --kind romanized --text "Corrected text"
.venv/bin/lyricflow lyrics representations approve current \
  --offline --line-id LINE_ID --kind romanized
.venv/bin/lyricflow lyrics representations reject current \
  --offline --line-id LINE_ID --kind romanized
.venv/bin/lyricflow lyrics representations reset current \
  --offline --line-id LINE_ID --kind romanized
```

Generated rejection suppresses generated fallback until explicit reset or
regeneration. Approved user text wins over retained provider/imported/generated
evidence across restart and provider refresh. `storage display set` persists
independent original, romanized/transliterated, and translated toggles; defaults
are original on, romanized on, and translated off.

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
