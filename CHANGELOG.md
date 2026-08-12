# Changelog

All notable repository changes are documented here. Current development remains
unreleased.

## Unreleased

### Added

- Stage 3 XDG-local SQLite persistence with explicit per-operation connection
  ownership, foreign keys, transactions, controlled errors, and two append-only
  checksummed migrations.
- Typed repositories for structured source identities, durable user-approved
  track corrections, preferred/ignored players, provider-neutral lyric
  documents/representations/lines/alignment, lyric match decisions, and provider
  cache kept separate from approval.
- `lyricflow storage status`, `storage migrate`, and `storage settings` local
  diagnostics plus deliberate `players select --approve-*` and
  `--reset-override` correction controls.
- Stage 3 regression coverage for empty/current/failed/newer/corrupt/locked
  databases, rollback, foreign keys, Unicode paths and values, repository
  round-trips, repeated lyric lines, exact integer timing/provenance, and fresh-
  process correction/settings precedence.
- ADR 0009 establishing the XDG location, append-only migration policy,
  per-operation/thread connection ownership, integer-millisecond lyric timing,
  and non-destructive recovery boundary.

- Stage 2 typed local-file, YouTube-video, and deliberately session-only generic
  MPRIS source identities, including decoded/canonical local paths and common
  YouTube URL extraction without playlist or tracking parameters.
- Explainable player selection with playback state, configured preference,
  metadata quality, ignored players, deterministic ties, independent-player
  retention, and duplicate suppression diagnostics.
- Conservative track candidates and confidence classes that preserve raw
  metadata, uploader evidence, version markers, transformations, warnings, and
  user-approved in-memory corrections.
- `lyricflow players select` with raw/resolved metadata, identity, confidence,
  alternatives, suppressed duplicates, and repeatable `--prefer`/`--ignore`
  diagnostics.
- Sanitized Stage 2 Strawberry and JessKah fixtures plus regression coverage for
  selection, identity, normalization, confidence, overrides, and explainability.
- The deferred fast manual synchronization product requirement, including
  line-start stamping, undo/re-stamp, seeks, fine adjustment, original-line
  multilingual alignment, and permanent precedence for approved timing.

- Regression coverage for authoritative D-Bus owner acquisition, loss, direct
  replacement, lifecycle subscription rejection, and teardown failure.
- Regression coverage proving the production proxy declares Metadata as
  `QVariantMap`, real-shaped local Metadata survives conversion, and one
  property's D-Bus error cannot mark later successful properties unavailable.
- Regressions for mixed-quality player enumeration, MaximumRate NotSupported,
  several simultaneous property failures, the live Firefox raw `a{sv}` signal
  shape, direct SIGINT, broken capture pipes, and controlled watcher cleanup.
- An opt-in private D-Bus probe that verifies the installed PySide6 raw-map
  representation and typed-property recovery path.
- Regression coverage for equivalent/self-returning QtDBus wrappers, cyclic and
  nested values, representative GetAll metadata, and PySide6 signal subscription
  construction, failure reporting, and teardown.
- Stage 1 raw MPRIS discovery, inspection, lifecycle, property-change, and seek
  diagnostics behind replaceable application and Qt-free bus boundaries.
- `lyricflow players list`, `lyricflow players inspect <service>`, and
  `lyricflow players watch` commands with expected failure states and clean
  Ctrl+C handling.
- Deterministic MPRIS tests covering service counts, races, malformed and
  missing values, Unicode, raw browser evidence, signals, Qt wrapper removal,
  and diagnostic CLI behavior.
- Private-GitHub preparation: practical contribution and security guidance,
  structured bug/feature issue forms, and a pull request checklist.
- Created the private `myonctl/LyricFlow` GitHub repository with `origin` using
  the existing local history and files; no GitHub-generated project files were
  added.
- Pushed `main` with upstream tracking and verified the required GitHub Actions
  quality workflow successfully on the private remote.
- Added a permanent Git/GitHub attribution policy and repository-local identity
  preflight for user-representative commits and canonical pushes.
- A future public-release checklist covering licensing, history/privacy review,
  dependency licenses, CI, branch protection, release strategy, packaging, and
  explicit visibility authorization.
- `PROJECT_STATE.md` as the concise current-reality and continuation entry point.
- `BACKLOG.md` for durable future features, technical debt, investigations, and
  intentionally deferred/rejected ideas outside stage authority.
- Documentation style, development workflow, and stage-completion standards.
- ADR 0007 establishing repository state and `AGENT_TODO.md` authority for
  context-free continuation.
- Repository-document consistency tests.
- Git history, beginning with imported-state snapshot `d4719b3` so the
  pre-hardening tree remains recoverable, followed by hardened Stage 0 boundary
  `06e0d4e`.
- Stage 0 installable Python package with a `src/` architecture skeleton.
- `lyricflow --version` and local-only `lyricflow doctor` prerequisite reporting.
- pytest, Ruff, mypy, and GitHub Actions quality configuration.
- Product, architecture, testing, dependency, reference, roadmap, manual-log,
  and ADR baselines.

### Changed

- Made preferred/ignored player selector semantics explicit and deterministic:
  bare selectors match only MPRIS service families, typed selectors opt into
  exact service/Identity/DesktopEntry matching, no media/descriptive-field
  substring matching occurs, and ignored configuration visibly takes
  precedence over preference. This fixes persisted `--ignore firefox`
  incorrectly suppressing KDE Plasma Browser Integration when it reports
  Firefox-related descriptive properties. Published fix `f739b54` passed GitHub
  Actions run `31641111236`; the live Scenario C-only retest remains pending.
- Activated Stage 3 after explicit authorization. Durable local implementation
  and the complete automated gate pass. Published implementation through
  `d374542` and verified passing GitHub Actions run `31631263888`; manual
  verification remains pending, and Stage 4 remains unauthorized.
- Replaced the production `players select` process-local override adapter with
  SQLite-backed corrections and persisted player settings while retaining the
  in-memory adapter for fast application tests. Raw MPRIS snapshots and the
  automatic candidate remain visible beneath an approved correction, while
  generic session-only sources cannot receive durable approval.

- Closed Stage 2 after the user-prepared real KDE session passed the tagged
  Strawberry local identity/version-marker check, exact JessKah YouTube
  duplicate/uploader/candidate check, and simultaneous independent-player
  selection/tie-break check. Published closure `a3d9e04` to canonical `main`
  and verified passing GitHub Actions run `31627907279`. At that closure point,
  Stage 3 was still Proposed and unauthorized.
- Corrected Stage 2 after a real-session read-only smoke check: blank browser
  artist elements no longer improve selection quality, and terminal Firefox
  `- YouTube` decoration is removed before conservative artist/title parsing.
  The richer Plasma duplicate now wins without inventing a musical artist for
  documentary content; the failed attempt and passing retest remain append-only
  evidence.
- Activated Stage 2 after explicit authorization. Its implementation and full
  automated gate are complete; the required real Strawberry/YouTube/multiple-
  player verification remains pending, so Stage 2 is not Completed and Stage 3
  remains unauthorized.

- Closed Stage 1 as Completed after explicit acceptance of the live
  `ba77489` result and passing-evidence commit `9abdd59`. The accepted
  cumulative result covers useful three-player enumeration, Strawberry and
  Plasma Browser Integration inspection, best-effort native Firefox behavior,
  metadata/Seeked/playback changes, Strawberry disappearance/appearance and
  playback after relaunch, clean SIGINT exit 130 without traceback, and no
  supported raw-`a{sv}` conversion warning. Stage 2 remains Proposed /
  awaiting explicit authorization and is not Active.
- Replaced the connection-interface lifecycle convenience signals with a direct
  typed D-Bus `NameOwnerChanged` subscription after live retest #4a missed a
  complete Strawberry service loss.
- Recorded retest #4a append-only as a partial pass and retest #4b as a pass at
  `ba77489`, including typed list output, both metadata changes, explicit
  Strawberry disappearance/appearance and relaunch playback, and direct exit
  130. The Stage 1 closure entry above records its later acceptance; Stage 2
  remains unauthorized.
- Replaced raw `GetAll`/untyped fallback property retrieval with statically
  typed MPRIS proxies, making list Metadata usable on PySide6 6.11.1 and
  isolating sticky `lastError()` state per property result.
- Recorded Stage 1 live retest #3 append-only as a substantial partial pass,
  preserving its successful enumeration, direct inspections, watch signals,
  seeks, and SIGINT while leaving metadata-change and lifecycle evidence pending
  for retest #4.
- Made Stage 1 player inspection independently best-effort: unavailable fields
  stay visible with diagnostics, useful fields survive optional-property
  failures, and one bad player no longer makes `players list` fail.
- Recovered undecodable standard PropertiesChanged maps through a bounded typed
  interface refresh and made Ctrl+C return 130 even when a capture pipe closes
  during shutdown.
- Recorded Stage 1 live retest #2 append-only as a partial pass while retaining
  its successful direct inspections, watch startup, live property/status/seek
  events, and Strawberry Seeked evidence.
- Corrected Stage 1 after failed live KDE verification: MPRIS properties now use
  PySide6's typed bounded interface-property path, no-progress wrappers become
  controlled diagnostics, and PropertiesChanged/Seeked subscriptions use the
  installed binding's supported `SLOT()` string form with checked results and
  matching teardown.
- Marked Stage 1 as requiring a successful live retest while retaining every
  Stage 2 behavior as unauthorized.
- Rewrote the six pre-policy commits from a generic Codex identity to `myonctl`
  with a verified account-associated GitHub email. Commit order, messages,
  trees, and dates were preserved; ADR 0008 records the old-to-new mapping.
- Verified GitHub resolves both author and committer of every canonical commit
  to `myonctl`, then observed the complete Actions workflow pass on the
  rewritten history.
- Standardized `AGENT_TODO.md` as the only implementation authority; it now
  explicitly authorizes no stage and records Stage 1 as Proposed only.
- Defined a zero-context start/end workflow, evidence language, append-only
  manual verification, Git discipline, dependency review, failure handling,
  extension boundaries, and exact handoff requirements.
- Assigned a non-overlapping responsibility to each repository document and
  updated the README documentation index.
- Clarified actual Stage 0 architecture separately from planned modules.
- Preserved multilingual original/romanized/translated requirements and all
  major future integrations in the product spec, roadmap, and backlog.

### Removed

- Removed the unverified Stage 1 MPRIS draft from the active tree to restore the
  explicitly required Stage 0-only boundary. Its pre-hardening form remains
  recoverable in commit `d4719b3` for review after Stage 1 is authorized; it is
  not accepted implementation or completion evidence. The sanitized Firefox
  and KDE browser observations remain as project-history fixtures.
