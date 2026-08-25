# Changelog

All notable repository changes are documented here. Current development remains
unreleased.

## Unreleased

### Changed

- Defined and activated Stage 11 from the existing canonical-settings and
  dotfile backlog items. Added one versioned typed schema/service over the nine
  actual Stage 1–10 preferences, explicit scope/reload/origin metadata, atomic
  last-known-good validation, and a frontend-neutral subscription API.
- Selected UTF-8 TOML at the LyriFlux XDG config path in ADR 0018. Added bounded
  `tomllib` parsing, TOMLKit comment-preserving serialization, mode-preserving
  atomic writes, safe symlink-target behavior, deterministic defaults < file
  precedence, and event-driven debounced desktop hot reload.
- Added `lyriflux config path|validate|get|set|reset|dump-defaults`; migrated
  existing non-default SQLite preferences idempotently without overriding
  explicit TOML; and retained SQLite for documents, corrections, caches,
  timing/calibration, and indexed state. Append-only schema 10 records only the
  successful handoff.

- Renamed the current product from LyricFlow to LyriFlux: display/application
  name, CLI (`lyriflux`), Python namespace and distribution (`lyriflux`), XDG
  namespace, desktop application ID/launcher, resources, and current docs.
- Added non-destructive, idempotent legacy XDG migration. Healthy legacy-only
  SQLite/config/cache state is copied and validated in the new namespace while
  the old state is retained; ambiguous dual-state layouts fail safely.
- Installing the LyriFlux desktop integration removes only the recognized
  project-owned LyricFlow launcher/icon so KDE does not show duplicate entries.
- Hardened reproducible builds to use sanitized temporary source copies and to
  reject stale legacy package members; this prevents an ignored old `build/`
  directory from contaminating the renamed wheel.
- Renamed the existing private GitHub repository in place to
  `myonctl/LyriFlux`. Its repository ID, creation date, history, branch SHA, and
  Actions run were preserved; no replacement repository or force-push was used.
- Accepted the real KDE menu/window, normal-song lyrics/synchronization, and
  restart/persistence matrix, completing the dedicated pre-Stage-11 rename.
- Generated desktop launchers now reference the exact installed SVG path.
  This avoids reliance on user-local icon-theme name discovery; paths
  containing spaces use desktop-entry escaping and pass launcher validation.
  KDE still showed a cosmetic placeholder in the accepted real installation,
  which is explicitly deferred and does not prevent menu launch.

## 1.0.0 — 2026-08-23

### Added

- Activated Stage 10 after the accepted multilingual repair closure commit
  `e3efe5b` passed exact-SHA GitHub Actions run `32659026971`. Selected
  standards-based source/wheel artifacts and explicit user-local XDG desktop
  integration in ADR 0017; public publication remains blocked by the separate
  license and visibility decisions.
- Added packaged freedesktop desktop-entry and scalable SVG icon assets plus
  atomic `desktop-integration install/status/remove` commands. Removal touches
  only the exact launcher/icon and never deletes the database, cache,
  configuration, backups, music, or virtual environment.
- Added reproducible same-epoch source/wheel builds with pinned build backend,
  byte comparison, SHA-256 reporting, required-content inspection, and a clean
  Ubuntu venv artifact lifecycle in CI. The exact backend is also part of the
  development extra so the deliberately non-isolated comparison environment is
  complete rather than relying on an implicit pip build sandbox.
- Added a non-overwriting, verified, mode-0600 SQLite online backup command; a
  privacy-bounded versioned JSON diagnostic export; controlled top-level
  desktop startup errors; and schema-8-to-9 release upgrade preservation.
- Added Linux v1 build/install/upgrade/backup/diagnostic/uninstall instructions,
  an end-to-end release matrix, and supported-player claims limited to actual
  automated and accepted real evidence.

- Activated the post-Stage-5 Chinese/Pinyin regression repair on 2026-08-23.
  Reproduced the defect at the document-language boundary: timed Han-only
  provider documents without language metadata stopped as ambiguous before the
  Chinese adapter, while explicit `zh` proved alignment, timing, persistence,
  synchronization, and desktop grouping remained intact.
- Added conservative document-level Chinese evidence, Japanese kana priority,
  a durable resettable per-document `zh`/`ja` override in append-only schema 9,
  and actionable representation diagnostics. Overrides invalidate generated
  fallback only; original/provider lyrics, timestamps, imported candidates,
  and approved line edits remain unchanged.
- Replaced generic ICU Chinese generation with local phrase-aware pypinyin
  Hanyu Pinyin using Unicode tone marks while retaining ICU for conservative
  character-variant evidence and the existing generic script transforms.
  Added ADR 0016 and an explicit multilingual support/limitations matrix.
- Added deterministic Chinese phrase/polyphone, Simplified/Traditional,
  mixed-text, timed/repeated-line, restart, CLI override/reset, Japanese
  non-regression, Latin no-duplicate, generic-script, desktop, and sync tests.
  Real Wowkie/Japanese/Latin acceptance and repair publication evidence remain
  pending; Stage 10 has not begun.
- The first live disposable Chinese desktop check passed, then exposed a
  separate generic-script presentation defect on real Cyrillic lyrics. Fixed
  synchronization snapshot selection so an empty romanized placeholder cannot
  mask an available transliterated line; added an exact regression for the
  original-empty/Cyrillic-populated combination.
- Accepted the complete real multilingual repair matrix on 2026-08-23. The
  disposable Chinese document displayed aligned tone-mark Pinyin; the actual
  Cyrillic original displayed its repaired ICU transliteration; the previously
  accepted Ado Han+kana document remained Japanese with romaji; and the Latin
  document retained its normal original-only layout without a duplicate layer.
  Disposable Chinese source/match state was removed and the audio hash, size,
  and mtime remained unchanged.
- Authorized the repository-defined Stage 9 Music-directory scanner and batch
  downloads scope on 2026-08-23. Stage 10 and later work remain unauthorized.
- Implemented Stage 9 typed global library settings; deterministic read-only
  Mutagen/filename scanning; bounded workers; per-file resume; unchanged,
  same-filesystem move, and completed-walk deletion handling; Stage 8 Approved
  correction reuse; opt-in High/Approved resolver-gated downloads; durable
  review/status; CLI automation; and background desktop scan/cancel controls.
- Added append-only schema 8, safe aggregate library diagnostics, ADR 0015, and
  deterministic cancellation, no-tag-edit, policy, resume, 200-file worker
  bound, and simulated 10,000-item responsive-desktop regressions.
- Published Stage 9 implementation commit `936279c` under `myonctl`; exact-SHA
  GitHub Actions run `32643929046` passed the complete quality job in 54 seconds.
  Evidence commit `27a612d` is also published, and run `32644177784` passed.
- Accepted the complete real Stage 9 disposable-directory matrix: migration,
  initial and unchanged scans, same-filesystem move, missing/restoration,
  conservative filename fallback, explicit download/review behavior, unchanged
  audio hashes/sizes/mtimes, real desktop-worker responsiveness, and final
  schema-8/root/review cleanup all passed. The post-matrix non-editable closure
  gate passed 510 tests plus Ruff, format, mypy, doctor, installed CLI smokes,
  and final storage/library checks. Stage 9 is Completed; Stage 10 — Packaging
  and v1 release is Proposed / unauthorized.
- Published Stage 9 verified closure commit `dd63cb0` under `myonctl`; exact-SHA
  GitHub Actions run `32653969635` passed the complete quality job in 53 seconds.
- Authorized the repository-defined Stage 8 Review and correction workflow on
  2026-08-23 after confirming it remains exactly the roadmap's alternative
  lyric selection, source-identity correction, match approval/rejection,
  recording-scoped delay, reset, and audit-evidence scope. Stage 9 and later
  work remain unauthorized.
- Added ADR 0013: LyricFlow remains Python-first, retains PySide6, permits only
  evidence-justified coherent native modules through a future PyO3/maturin
  boundary, prefers Textual for future TUI investigation, and does not create a
  daemon without demonstrated simultaneous-client need.
- Added ADR 0014: future GUI/TUI/files/automation settings and safe declarative
  themes share one typed frontend-neutral model with explicit scope,
  validation, atomic last-known-good reload, dotfile-friendly workflows, and
  adapter-owned Qt/Textual styling. Future overlay and machine-readable
  integration directions remain planned rather than implemented.
- Implemented the Stage 8 frontend-neutral review/correction service and
  provider alternative catalog. Stable-source artist/title corrections,
  current match approval/rejection, explicit alternative approval, match reset,
  and exact-document delay/reset reuse existing typed repositories and remain
  independent across restart.
- Added the desktop Review dialog with separate raw, automatic, effective, and
  provider evidence; bounded alternative lookup and every mutation stay on the
  existing worker boundary. Provider/exact-local rejections are not
  automatically reattached, and no audio tag, provider lyric text, or provider
  timestamp mutation path was added.
- Added append-only schema 7 for durable per-recording rejected-document
  history and evidence. Existing schema-6 rejected decisions are backfilled;
  selecting another result or restarting no longer forgets a rejection.
- Published Stage 8 implementation commit `44360cb` under `myonctl`; GitHub
  Actions run `32640628463` passed the complete quality job on its exact SHA.
  Stage 8 remains verification pending until the bounded real desktop matrix is
  accepted.
- Hardened Stage 8 cleanup so Reset match choices clears both the current match
  and all source-scoped rejected-result preferences, including a rejection that
  stopped being current after automatic fallback selected another result.
  Cleanup commit `c396d1d` is published, and exact-SHA Actions run `32641121107`
  passed its complete quality job.
- Accepted the complete real Stage 8 Strawberry/desktop matrix: audit layers
  remained distinct, corrections/match decisions/delay persisted and reset at
  their intended scopes, rejected results remained suppressed, source audio
  and provider values remained unchanged, and final schema-7 storage integrity
  passed with zero temporary corrections, rejections, delays, or calibrations.
  Verified closure commit `cec9ac9` is published, and exact-SHA Actions run
  `32641960138` passed its complete quality job in 51 seconds. Stage 8 is
  Completed; Stage 9 is Proposed / unauthorized.

- Activated the repository-defined Stage 7 Desktop MVP after explicit user
  authorization on 2026-08-22 while preserving the future first-class
  `lyricflow tui` and leaving Stage 8+ unauthorized.
- Added a Qt-free frontend session/state boundary combining existing player
  selection, lyric resolution, multilingual representation settings, timing,
  generation rejection, snapshots, and controlled user-facing states for both
  the desktop and future TUI.
- Added `lyricflow desktop`: a responsive PySide6 Widgets main window with a
  current-track header, active/nearby synchronized lyric hierarchy, untimed and
  instrumental presentation, progress/playback state, durable representation
  toggles, compact source/sync status, and bounded details.
- Added worker-owned storage/provider/filesystem work, source-change and
  shutdown cancellation for LRCLIB requests, semantic snapshot publication,
  and clean no-player/player-change recovery without widget-owned D-Bus, SQL,
  provider, matching, or romanization policy.
- Added deterministic desktop state, stale A-to-B event, pause/resume/seek,
  outcome, settings, multilingual/Unicode, plain-text escaping, logical resize,
  100-200% scale-factor, light/dark palette, launch, shutdown, and cancellable-
  request regressions. The bounded public feedback synthesis and Stage 7 scope
  decisions are recorded in `docs/PRODUCT_GAP_RESEARCH.md`.
- Added the minimal CI `libegl1` system prerequisite required to import
  QtGui/QtWidgets and run the desktop suite offscreen on GitHub's base image.
- Hardened desktop player re-selection so unrelated player events and
  same-source policy re-evaluation preserve the valid lyric session, while
  selected metadata/disappearance and genuinely changed sources still
  invalidate immediately. Added coordinator, stale-selection, keyboard-focus,
  Escape-dialog, and fixed-widget-tree regressions.
- Completed the Stage 7 real KDE matrix and repaired issues it exposed:
  automatic offline romanization now runs at the shared frontend boundary;
  logical-area typography scales without clipping the title; romanization and
  translation use visibly subordinate plain-text layers; lyric selection and
  the I-beam cursor are persisted opt-in mechanics through append-only schema
  6; and direct SIGINT exits 130 without a traceback.
- Recorded accepted real Strawberry/browser playback, pause/seek/track/player
  recovery, multilingual toggles, untimed/instrumental/no-result states,
  taskbar/keyboard/window lifecycle, light/dark palettes, and longer-run
  resource evidence. Native Qt 6.11.1/KWin 6.7.4 Wayland resize latency also
  reproduced in an empty control; XWayland is the accepted current workaround.

- Activated Stage 6 after explicit user authorization on 2026-08-22 and made
  the accepted future full-screen `lyricflow tui` requirement durable in the
  product specification, architecture, and backlog. The Stage 6 terminal
  synchronization display remains a diagnostic prototype, not that future TUI.
- Added midpoint-bracketed MPRIS Position sampling; a pure monotonic playback
  clock with fixed-point rates, pause/seek/track/rate/suspend resets, adaptive
  resampling, stale/duplicate/slow-read rejection, no-rewind phase slew, robust
  bounded drift fitting, health/uncertainty evidence, and exact trajectory-aware
  line deadlines.
- Added separate typed audio-output, lyric timestamp, and presentation latency
  terms; schema 5 persistence for per-document display delay and stable-output
  residual calibration; a bounded read-only PipeWire default-sink diagnostic;
  and `sync current/probe/delay/audio` surfaces. Unsafe or stale latency remains
  diagnostic-only and unknown never becomes zero.
- Added immutable frontend-neutral synchronization snapshots, aligned Stage 5
  representation layers, source-generation guards, and leak-free meaningful-
  change subscriptions so future desktop/TUI adapters do not depend on MPRIS,
  PipeWire, storage, lyrics providers, or linguistic adapters. ADR 0012 records
  the timing, persistence, sampling, and publication policy.

- Stage 5 deterministic Unicode script composition and conservative routing
  that distinguishes Latin, Han, kana, Hangul, Cyrillic, Greek, Arabic, Thai,
  punctuation/numbers, mixed scripts, and ambiguous Han without inventing a
  language claim.
- Offline Cutlet/Fugashi/UniDic-lite Modified Hepburn generation for Japanese
  and Unicode ICU transforms for Korean, explicitly Chinese Han/Pinyin,
  Cyrillic, Greek, Arabic, and Thai, with embedded Latin preservation,
  generator/version provenance, and qualitative uncertainty.
- Schema migration 4 and a typed representation repository for per-original-
  line provider/imported/generated candidates, ordered diagnostics, independent
  user drafts/approvals/rejections, and global display settings. Original lyrics
  and timestamps remain canonical and user approvals survive restart,
  regeneration, and provider/original refresh.
- Application precedence/import/correction services plus bounded
  `lyrics romanize current`, `lyrics representations ...`, and
  `storage display` commands. Translation is a functional imported/user third
  layer; no translation engine, cloud API, lyric upload, or telemetry was added.
- Stage 5 deterministic adapter, script, alignment, persistence, failure,
  privacy, settings, and fresh-process CLI regressions using tiny sanitized
  lyric excerpts only. ADR 0011 records routing, dependencies, styles, and
  replacement paths.

- Stage 4 provider-neutral local-first lyrics resolution with typed timed,
  untimed, instrumental, ambiguous, offline, rate-limit, invalid, and
  unavailable outcomes carrying source identity, provenance, confidence, and
  evidence.
- Bounded LRC/plain parser; exact adjacent sidecars; read-only supported Mutagen
  embedded fields; conservative LRCLIB exact/search through HTTPX; raw response
  cache, normalized documents, durable match evidence, offline/refresh policy,
  persistent instrumental/no-result behavior, and schema migration 3.
- `lyricflow lyrics current` with `--offline`, `--refresh`, and deliberate
  `--full`; normal diagnostics use a bounded preview and never dump complete
  lyrics implicitly.
- Stage 4 parser/local/provider/matcher/cache/restart/privacy/failure/CLI tests
  and sanitized Strawberry, JessKah, black-screen, and LRCLIB fixtures. ADR 0010
  records acceptance thresholds and cache semantics.

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

- Closed Stage 6 after the real Strawberry matrix passed: a three-minute Locked
  stability probe, exact pause/resume, forward/backward rapid seeks, track
  invalidation, aligned Japanese/romaji transitions, perceived delay direction
  and fresh-process persistence/reset, clean direct interrupts, and final
  storage cleanup. A visible paused-start resume race was fixed with guarded
  post-subscription authoritative refresh. The structurally mismatched S3RL
  timeline remains rejected provider-quality evidence; Stage 7 is Proposed /
  unauthorized. Verified-closure commit `00a3d25` was published normally and
  GitHub Actions run `32586628479` passed all jobs.
- Published Stage 6 implementation commit `8502968` to canonical `main` under
  the verified `myonctl` identity. GitHub Actions run `32583407778` passed the
  complete quality job. Evidence checkpoint `70f5b8b` and Actions run
  `32583856426` preserved the pre-closure state.

- Activated Stage 5 after explicit user authorization on 2026-08-13. Its local
  implementation is complete with automated and real manual/publication gates
  tracked separately; Stage 6 remains Proposed and unauthorized.
- Published Stage 5 implementation commit `c55ccac` to canonical `main` after
  the 336-test local gate; GitHub Actions run `31746378400` passed the exact
  implementation SHA. Focused real manual verification remains pending.
- Closed Stage 5 as Completed after real schema 4 migration, offline timed
  Japanese generation with 47/51 aligned versioned candidates, fresh-process
  user approval and exact reset, and a timed 59-line Latin document producing
  zero redundant candidates or failures. Stage 6 is Proposed / awaiting
  explicit authorization and is not Active.

- Closed Stage 4 as Completed after the user accepted all final real-world
  scenarios on published baseline `05c7212`: exact adjacent Strawberry sidecar
  LRC with no network, live LRCLIB record `22395851` plus fresh-process offline
  reuse, and conservative black-screen no-result behavior with no uploader-as-
  artist query. Match confidence is now documented separately from provider
  text/timing quality; provenance-preserving text, per-line timing, and global-
  offset corrections remain planned and unimplemented. Stage 5 is Proposed /
  awaiting explicit authorization and is not Active.
- Finished the Stage 4 implementation checkpoint while leaving real-world
  provider/cache, local timed-lyrics, and failure/no-result verification
  pending. The Strawberry investigation found no documented stable lyrics
  export/API or versioned read-only cache contract, so no private-schema adapter
  was added. Stage 5 remains unauthorized.
- Closed Stage 3 as Completed after all real-XDG scenarios passed: schema
  migration/status diagnostics, approved-correction persistence and precedence
  after restart, correction reset, durable preferred/ignored player settings,
  corrected service-family selection in a fresh process, and clean settings
  cleanup with integrity `ok`. Stage 4 is Proposed / awaiting explicit
  authorization and is not Active.
- Made preferred/ignored player selector semantics explicit and deterministic:
  bare selectors match only MPRIS service families, typed selectors opt into
  exact service/Identity/DesktopEntry matching, no media/descriptive-field
  substring matching occurs, and ignored configuration visibly takes
  precedence over preference. This fixes persisted `--ignore firefox`
  incorrectly suppressing KDE Plasma Browser Integration when it reports
  Firefox-related descriptive properties. Published fix `f739b54` passed GitHub
  Actions run `31641111236`; the final live Scenario C-only retest passed.
- Activated Stage 3 after explicit authorization. Durable local implementation
  and the complete automated gate passed. Published implementation through
  `d374542` and verified passing GitHub Actions run `31631263888`; its later
  manual closure is recorded above.
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
