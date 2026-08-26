# LyriFlux

LyriFlux is a planned Linux-first desktop application that detects current
playback through MPRIS, resolves track identity conservatively, finds local or
provider lyrics, and displays synchronized lyrics. For non-Latin scripts, the
original line remains first, a romanized/transliterated line appears directly
under it, and an optional translation forms a third layer.

The canonical product name is `LyriFlux`; its canonical machine identifier is
`lyriflux`. The project was renamed from LyricFlow after Stage 10. The current
version is 1.0.0.

## Current status

Stages 1 through 11 and the post-Stage-5 Chinese/Pinyin repair are **Completed**.
Stage 11 — Canonical Settings & Configuration Foundation passed its 579-test
automated gate, exact-SHA implementation CI, and bounded real KDE/configuration/
daily-use/restart/legacy-safety matrix on 2026-08-26. No implementation stage
is Active.
The installed private v1 is launchable from KDE's application menu; its
remaining cosmetic placeholder icon is explicitly deferred. Stage 12+ remains
Proposed / unauthorized. Stage 8 —
Review and correction workflow
passed its automated gate and accepted real Strawberry/desktop correction,
restart, preservation, and cleanup matrix on 2026-08-23. Stage 9 —
Music-directory scanner and batch downloads passed its automated and real
disposable-directory migration, incremental, move/delete, filename-fallback,
batch-download/review, preservation, desktop-worker, and cleanup gates later
that day.
Stage 7 — Desktop MVP passed its automated
gate and accepted real KDE, playback, recovery, scaling, theme, state, and
longer-run verification on 2026-08-23. Native Qt/Wayland interactive resizing
showed the same pointer lag in an empty PySide6 control; XWayland is the accepted
current workaround. Stage 5
— Romanization and multilingual
layers — passed its published automated gate and real Japanese generation,
fresh-process correction/reset, and Latin no-duplicate verification. Stage 3 —
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

The [`myonctl/LyriFlux`](https://github.com/myonctl/LyriFlux) GitHub repository
is private during pre-alpha development. Its `origin` remote and default branch
use the conventional `main` workflow. The project is intended to become public
later, but LyriFlux is **not currently open source**: no open-source license
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
structure, and layer settings. Stage 6 adds a precision/
uncertainty-aware playback clock, separate audio/lyric/presentation calibration,
durable document/output residual settings, active-line deadlines, a shared
frontend-neutral snapshot, and diagnostic `sync` commands. Its automated and
real cooperative Strawberry/timed-lyrics closure gates pass. Stage 7 adds a
real PySide6 Widgets main window driven by the same frontend-neutral
application state, with synchronized multilingual lyrics, explicit normal and
failure states, progress, shared display settings, and bounded details. Stage 8
adds a shared correction/audit service and desktop Review dialog for alternative
lyrics, artist/title overrides, approval/rejection, exact-document delay, and
independent resets while preserving raw/provider evidence. The full-screen TUI
remains future work. Stage 9 adds typed global library-root/download/worker
settings, incremental read-only Mutagen scanning, conservative filename
fallback, resumable schema-8 state, uncertain-item review, and opt-in
High/Approved-only batch lyric resolution. The desktop scan action uses the
existing bounded background pool and supports cooperative cancellation. See
`PROJECT_STATE.md` for evidence and `AGENT_TODO.md` for the only implementation
authority.

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
- Keep LyriFlux Python-first. Native Rust is available only for a coherent
  subsystem with measured benefit; there is no whole-project rewrite or daemon.
- Evolve settings and safe declarative themes through one frontend-neutral
  validated model shared by desktop, future TUI, files, and automation.

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
- `docs/RELEASE.md` — v1 build, installation, backup, upgrade, diagnostics,
  uninstall, end-to-end matrix, and supported-player claims.
- `docs/PROJECT_RENAME_LYRIFLUX.md` — the post-Stage-10 identity and data
  migration contract and verification record.
- `docs/REFERENCES.md` — external primary technical references.
- `docs/MULTILINGUAL_SUPPORT.md` — exact local language routes, terminology,
  evidence policy, and limitations.
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
- `docs/STAGE_5_COMPLETION.md` — completed Stage 5 implementation, automated,
  multilingual/correction/Latin-safety, publication, and CI evidence.
- `docs/STAGE_6_COMPLETION.md` — completed Stage 6 implementation, automated,
  real-player/timed-lyrics, delay, publication, and CI evidence.
- `docs/STAGE_7_COMPLETION.md` — completed Stage 7 implementation, automated,
  real KDE/player/state/resource evidence, and accepted limitations.
- `docs/STAGE_8_COMPLETION.md` — completed Stage 8 implementation, automated,
  real desktop, cleanup, publication, and CI evidence.
- `docs/STAGE_9_COMPLETION.md` — completed Stage 9 implementation, automated,
  disposable-directory, preservation, desktop-worker, cleanup, and publication
  evidence.
- `docs/STAGE_10_COMPLETION.md` — Stage 10 packaging, artifact, data-safety,
  installed-desktop, publication, and CI evidence.
- `docs/STAGE_11_COMPLETION.md` — completed canonical settings/configuration
  foundation, migration, real KDE hot-reload, daily-use, publication, and CI
  evidence.
- `docs/PRODUCT_GAP_RESEARCH.md` — bounded public product-feedback findings and
  Stage 7/future/rejected scope decisions.
- `docs/adr/0010-stage4-lyrics-resolution.md` — accepted Stage 4 precedence,
  matching, cache/offline/refresh, privacy, and schema policy.
- `docs/adr/0011-offline-romanization-routing.md` — accepted Stage 5 script
  routing, offline engine, style, ambiguity, and replacement policy.
- `docs/adr/0013-python-first-hybrid-architecture.md` — accepted Python-first,
  selective native-module, retained desktop, future TUI, and no-daemon policy.
- `docs/adr/0014-frontend-neutral-settings-and-themes.md` — accepted canonical
  settings, dotfile, safe semantic theme, and frontend adapter policy.
- `docs/adr/0015-incremental-library-scanner.md` — accepted Stage 9 incremental,
  read-only, resumable, bounded, and confidence-gated scanning policy.
- `docs/adr/0016-chinese-pinyin-regression-repair.md` — accepted conservative
  Chinese evidence, phrase-aware Pinyin, and durable override policy.
- `docs/adr/0017-linux-v1-packaging.md` — accepted Python distribution,
  reproducible-build, and user-local XDG desktop-integration policy.
- `docs/adr/` — accepted and historical architecture/process decisions.

`PLAN_MANIFEST.json` is a machine-readable document inventory, not an authority.
`CODEX_MASTER_PROMPT.md` is a convenience entry point that redirects agents to
the current repository state; it cannot authorize a stage.

## Development setup

LyriFlux requires Python 3.11 or newer. From the repository root:

```bash
python -m venv .venv
.venv/bin/python -m pip install ".[dev]"
```

PySide6, HTTPX, Mutagen, Cutlet/Fugashi/UniDic-lite, pypinyin, PyICU, and
TOMLKit are runtime dependencies. PyICU requires system ICU development headers
when installed from PyPI source. pytest, Ruff, and mypy are development dependencies.
Rationale, licensing cautions, and usage boundaries are recorded in
`docs/DEPENDENCIES.md`.

## Implemented commands

```bash
.venv/bin/lyriflux --version
.venv/bin/lyriflux doctor
.venv/bin/lyriflux desktop
.venv/bin/lyriflux config path
.venv/bin/lyriflux config validate
.venv/bin/lyriflux config get lyrics.display.translated
.venv/bin/lyriflux config set lyrics.display.translated true
.venv/bin/lyriflux config reset lyrics.display.translated
.venv/bin/lyriflux config dump-defaults
.venv/bin/lyriflux players list
.venv/bin/lyriflux players inspect <service>
.venv/bin/lyriflux players watch
.venv/bin/lyriflux players select
.venv/bin/lyriflux storage status
.venv/bin/lyriflux storage migrate
.venv/bin/lyriflux storage settings show
.venv/bin/lyriflux lyrics current
.venv/bin/lyriflux lyrics current --offline
.venv/bin/lyriflux lyrics current --refresh
.venv/bin/lyriflux lyrics romanize current --language ja
.venv/bin/lyriflux lyrics representations current --offline
.venv/bin/lyriflux storage display show
.venv/bin/lyriflux library settings
.venv/bin/lyriflux library settings --root /absolute/test/music --workers 2
.venv/bin/lyriflux library scan --offline
.venv/bin/lyriflux library status
.venv/bin/lyriflux library review
.venv/bin/lyriflux sync current --offline
.venv/bin/lyriflux sync probe --duration-seconds 30
.venv/bin/lyriflux sync delay show --offline
.venv/bin/lyriflux sync audio status
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

Canonical settings use UTF-8 TOML at
`$XDG_CONFIG_HOME/lyriflux/config.toml`, falling back to
`$HOME/.config/lyriflux/config.toml`. Built-in defaults have lower precedence
than explicit file values, and `config get` reports the origin, scope, type,
and reload behavior. Direct edits, Git, symlinks, and Stow are supported;
invalid complete saves are rejected while a running desktop retains its last
known-good snapshot. LyriFlux writes through validated, comment-preserving,
atomic replacement and never executes configuration. The existing `storage
settings`/`storage display` and `library settings` commands are compatibility
adapters over this same service.

`storage status` is read-only and reports the XDG database path, schema and
integrity state, and safe repository counts without dumping media paths or lyric
content. `storage migrate` explicitly initializes or upgrades the database
without destructive reset. `storage settings set --prefer ... --ignore ...`
atomically replaces canonical player configuration; `players select` uses it when
command-line values are absent. A selected stable source can be deliberately
corrected with `--approve-title` plus one or more `--approve-artist` values and
reset with `--reset-override`. Session-only generic sources reject approval.

The default database is
`$XDG_DATA_HOME/lyriflux/lyriflux.sqlite3`, falling back to
`$HOME/.local/share/lyriflux/lyriflux.sqlite3`. Tests inject isolated temporary
paths and never depend on the current working directory.

`library settings` is the noninteractive adapter over one typed global model.
Roots default empty, automatic downloads default off, and metadata workers
default to four with a validated 1–8 bound; all apply on the next scan. Repeat
`--root` to atomically replace roots, use `--clear-roots` for explicit cleanup,
and use `--automatic-downloads on` only after validating a small disposable
test directory. `library scan` commits each processed file for resume, skips
unchanged signatures, recognizes same-filesystem moves, reconciles deletions
only after a complete walk, and never saves audio tags. `--offline` forbids new
provider HTTP. Filename-only uncertainty and unresolved download outcomes are
listed by `library review`. The desktop Scan library action consumes these same
settings on its background pool; activating it again requests safe cancellation.

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
Han-only input is not guessed from script alone. Document/provider evidence may
route it automatically; `lyrics language set zh|ja` supplies a durable,
resettable user-approved decision when it remains ambiguous. `--regenerate` replaces
only generated cache for the selected `--line-id` values and never overwrites a
user approval. `lyrics representations current` shows bounded stacked previews,
candidate/decision counts, provenance, generator/version, uncertainty, missing
alignment, and inherited original timing.

The safe correction commands operate only on the current resolved lyric
document and confirm its document and line IDs:

```bash
.venv/bin/lyriflux lyrics representations set current \
  --offline --line-id LINE_ID --kind romanized --text "Corrected text"
.venv/bin/lyriflux lyrics representations approve current \
  --offline --line-id LINE_ID --kind romanized
.venv/bin/lyriflux lyrics representations reject current \
  --offline --line-id LINE_ID --kind romanized
.venv/bin/lyriflux lyrics representations reset current \
  --offline --line-id LINE_ID --kind romanized
```

Generated rejection suppresses generated fallback until explicit reset or
regeneration. Approved user text wins over retained provider/imported/generated
evidence across restart and provider refresh. `storage display set` persists
independent original, romanized/transliterated, and translated toggles; defaults
are original on, romanized on, and translated off.

`sync current` follows a selected timed document with locally interpolated
frames and adaptive authoritative MPRIS checks. It renders media versus
qualified audible position, clock health/RTT/residual/drift evidence, separated
automatic-output/residual/document/presentation terms, aligned active
multilingual lines, and the next deadline. `sync probe` is bounded, loads no
lyrics, uses no provider network, and reports clock/audio quality independently
from provider timestamps. Positive `sync delay set +250ms` displays the current
document later without modifying its stored provider lines; `sync audio
calibrate +25ms` means the exact current output is heard later than an automatic
estimate predicts. Both are durable and independently resettable. PipeWire
evidence is diagnostic-only whenever current stream routing is unproven; unknown
latency is never printed or applied as zero.

`desktop` opens the normal resizable application. It stays open in a
deliberate waiting state when no player is available, automatically follows
player/source changes, invalidates old lyrics before resolving a new track,
and displays timed, untimed, instrumental, ambiguous, no-result, offline, and
provider-failure states without diagnostic clutter. Settings edits use the
same durable original/romanized/translated toggles as the CLI and a schema 6
opt-in lyric-selection mechanic; lyrics are passive by default. Available
offline romanization is generated at the shared frontend boundary. Provider,
filesystem, and storage work runs outside the Qt UI thread; in-flight provider
requests are cancelled on source changes and shutdown. Detailed limitations
remain available from the Details button. Review opens the Stage 8 source-bound
workflow: it shows raw, automatic, effective, and provider metadata separately;
loads bounded alternative results off the UI thread; and dispatches explicit
track correction, match approval/rejection/selection/reset, and exact-document
delay/reset actions through shared application services. It never edits audio
tags or provider lyric text/timestamps. Append-only schema 7 keeps rejected
document IDs/evidence separate from the current selection so another choice or
restart cannot silently reattach a rejected result. Reset match choices clears
the current decision and every rejected-result preference for that source.
Scan library starts the configured Stage 9 service on the bounded worker pool,
remains responsive during large fixtures, and becomes a cooperative Cancel scan
action until completion. Root/download/worker configuration and uncertain-item
review use the shared CLI model rather than Qt-owned settings.

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
.venv/bin/lyriflux players watch
```

## Quality gate

Run from the repository root:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy src
.venv/bin/lyriflux --version
.venv/bin/lyriflux doctor
.venv/bin/lyriflux storage status
.venv/bin/lyriflux desktop --help
```

Exact current results belong in `PROJECT_STATE.md` and `AGENT_TODO.md`.
