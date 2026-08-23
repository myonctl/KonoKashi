# Backlog

This file preserves durable work outside the currently authorized stage. A
backlog entry is never permission to implement it; only `AGENT_TODO.md` can
authorize implementation. Staged v1 ordering lives in `docs/ROADMAP.md`.

## Confirmed future features

- **Synced lyrics display** — Resolve and display timestamped lyrics against
  playback. Why: this is the core product outcome. Status: Implemented through
  the Stage 7 desktop/application model and tested automatically and on real
  KDE/Strawberry/browser playback.
  Prerequisites: Stages 1–6. Related: Roadmap Stages 4, 6, and 7.
- **User-approved corrections** — Let users correct track and lyrics matches,
  approve or reject results, and preserve those choices. Why: metadata and
  provider matches are fallible. Status: Implemented in Stage 8 for stable
  source artist/title, lyric alternative selection, match approval/rejection,
  durable per-recording rejection history, exact-document delay, audit evidence,
  and independent reset; real desktop verification is pending. Prerequisites:
  stable source identity and persistence. Related: Roadmap Stages 2, 3, and 8.
- **Independent lyric text and timing corrections** — Let users correct lyric
  text, individual line timestamps, and a whole-document/global offset while
  preserving provider-original text/timing and provenance. User-approved
  corrections must outrank provider/generated values, survive restart and
  provider refresh, and reset independently. Why: a High recording match does
  not guarantee perfect provider transcription or timing. Status: Planned;
  explicitly not implemented by Stage 4 closure. Prerequisites: correction
  model/UI, playback synchronization for timing review, and migrations that
  preserve original plus approved layers. Stage 8 implements only the
  whole-document delay and match/identity workflow; lyric-text and per-line
  editing remain Deferred. Related: Product specification match
  confidence versus lyric quality; Roadmap Stages 6, 8, and 11+.

## UX improvements

- **One canonical settings schema and service** — Unify GUI settings,
  `lyricflow settings`, human-edited XDG configuration, and noninteractive CLI
  automation over typed validated semantics with explicit scope and reload
  class. Include actionable unknown-key/value diagnostics, atomic
  last-known-good reload, and deliberate comment-preservation/migration policy.
  Status: Planned future architecture; not Stage 8. Prerequisites: settings
  schema/format design and migration from current typed SQLite settings.
  Related: ADR 0014.
- **First-class interactive settings TUI and GUI counterpart** — Provide a
  polished keyboard-first Textual candidate with categories, search, validated
  controls, reset, scope/origin, and preview, plus equivalent desktop
  capability over the same schema. Status: Planned; Textual must receive a
  current upstream/license/terminal/CJK/test/packaging/resource review before
  adoption. Prerequisites: canonical settings service. Related: ADRs 0013 and
  0014.
- **Traditional dotfile/ricing configuration** — Support safe declarative,
  XDG-compliant, version-control/symlink/Stow-friendly files, deterministic
  config commands, documented layering, and hot reload where safe. Preserve
  hand-written comments where practical and reject executable sourcing,
  circular includes, and partial invalid application. Status: Planned future
  work; exact format/layout deliberately undecided. Related: ADR 0014.
- **Safe frontend-neutral themes and shareable rices** — Define portable
  semantic theme/layout data with validation, named presets, import/export,
  duplication, and a user directory. Qt QSS/QML and Textual TCSS remain adapter
  technologies; themes never execute Python, shell, commands, or plugins. Plain
  files and Git are sufficient; no cloud marketplace is planned. Status:
  Planned future work. Prerequisites: canonical settings/theme schema.
- **Normal, compact, and wallpaper/desktop overlay modes** — Evolve presentation
  without assuming a toolbar, opaque background, permanent metadata, or fixed
  geometry. Overlay behavior may include transparent/frameless layouts,
  anchoring, optional controls/metadata, and independent multilingual layers;
  click-through/layering/blur/taskbar behavior requires portable Wayland-first
  capability research plus optional compositor adapters. Status: Planned
  investigation; not Stage 8. Preserve the accepted native Wayland resize
  limitation and do not make LyricFlow KDE-only.

- **First-class full-screen TUI** — Provide an everyday-use terminal frontend,
  conceptually `lyricflow tui`, with current track, synchronized multilingual
  lyrics, explicit states, corrections, and settings through the same
  application services used by the desktop frontend. It is not a diagnostic
  log or reduced fallback. Status: Planned; explicitly not implemented in Stage
  6. Prerequisites: the Stage 6 synchronization engine and shared application
  state. Related: Product specification first-class terminal frontend and
  planned presentation architecture.

- **Machine-readable current-line/event output** — Provide a stable stdout
  current-line mode and/or JSON event stream for reusable consumers such as
  Waybar, eww, AGS, OBS, and Discord RPC. Why: public neighboring-project
  feedback shows demand for composition with desktop/status/streaming tools,
  while separate integration-specific business logic would fragment the core.
  Status: Planned future work; explicitly not Stage 7. Prerequisites: stabilize
  the shared frontend snapshot/subscription contract through real desktop use.
  Related: `docs/PRODUCT_GAP_RESEARCH.md` and the first-class TUI requirement.

- **Per-track representation display overrides** — Extend Stage 5's durable
  global original/romanized/translated toggles with optional stable-track
  overrides when the desktop settings model exists. Why: the product spec
  permits track-specific display choices without changing safe global defaults.
  Status: Deferred to the desktop/settings stage. Prerequisites: Stage 7 UI and
  a deliberate source-identity settings schema.

- **Per-layer lyric appearance controls** — Let users configure original,
  romanized/transliterated, and translated font family, relative size, weight,
  color, and spacing independently while retaining readable defaults and system
  theme/high-DPI behavior. Why: pronunciation and translation should remain
  visually subordinate without forcing one hierarchy on every user. Status:
  Planned future work; the Stage 7 desktop provides only responsive defaults
  and visibility toggles. Prerequisites: an accessible appearance-settings
  model and persistence policy; explicitly not authorized by the Stage 7
  verification repair.

- **Fast manual lyric synchronization** — When reliable synchronized lyrics
  cannot be found or generated, let the user paste or obtain correct plain
  lyrics, play the exact recording, and stamp each line start with one key or
  button press before advancing immediately. Preserve repeated choruses as
  distinct line instances; support undo/re-stamp, small seeks, and later fine
  adjustment. Approved manual timing outranks generated timing and is cached
  permanently for that stable recording identity. Automatic alignment or
  YouTube transcript timing may provide rough hints only and must work as an
  optional aid, never as the lyric authority. Non-Latin timing attaches to the
  original line, with aligned romanized/transliterated and translated layers
  inheriting it unless they have more precise timing. Future waveform editing
  may extend the workflow. Why: manual timing must remain available even when
  every AI/provider timing path fails. Status: Deferred; explicitly outside
  Stage 2. Prerequisites: lyric documents and alignment, stable recording
  identity, persistence, playback synchronization, and correction UI. Related:
  Product specification manual synchronization fallback; Roadmap Stage 11+.
- **KDE overlay or widget** — Investigate a compact overlay, Plasma widget, or
  companion surface. Why: lyrics should remain visible outside the main
  window. Status: Investigation. Prerequisites: stable desktop application
  state API. Related: Roadmap Stage 11+.

## Provider integrations

- **Multiple lyrics providers** — Add legal, maintainable providers behind
  `LyricsProviderPort` after LRCLIB. Why: improve coverage without coupling the
  application to one API. Status: Planned. Prerequisites: provider contract,
  licensing and dependency review. Related: ADR 0003; Roadmap Stage 11+.
- **Strawberry lyrics interoperability** — Determine whether Strawberry offers
  a documented interface, portable file, or safely versioned read-only cache.
  Why: reuse already available lyrics without duplicate requests. Status:
  Investigated in Stage 4 and deferred: no documented stable lyrics API/export
  or versioned read-only cache contract was found; adjacent LRC and embedded
  lyrics remain supported. Prerequisites for reconsideration: a published
  compatibility contract and replacement-safe adapter. Related: ADR 0005.
- **YouTube transcript timing hints** — A later manual-alignment workflow may
  optionally use transcript timestamps as rough reviewable hints, never as
  authoritative lyric text or approved timing. Status: Deferred beyond Stage
  4; no transcript fetching/import exists. Prerequisites: explicit
  authorization, consent/privacy policy, provenance, quality evaluation, and a
  manual review workflow. Related: manual synchronization fallback; Stage 11+.

## Player integrations

- **Future player adapters** — Add replaceable adapters for platforms or
  players not sufficiently represented through Linux MPRIS. Why: broaden
  support without changing core policies. Status: Deferred until Linux v1 is
  stable. Prerequisites: player port and platform ADR. Related: ADR 0002.
- **Firefox/KDE duplicate policy** — Recognize duplicate native Firefox and
  Plasma Browser Integration observations without discarding diagnostics.
  Why: one video can appear as multiple players. Status: Planned.
  Prerequisites: Stage 1 raw observations. Related: Roadmap Stage 2.

## Multilingual lyrics

- **Original-script preservation** — Store and display original non-Latin
  lyrics without destructive conversion. Why: source text must remain
  authoritative. Status: Implemented in Stages 4–5 and tested through original
  refresh/correction isolation. Related: ADR 0006; Roadmap Stages 4–5.
- **Stacked representation layers** — Show romanized/transliterated text
  directly under the original and optional translation underneath that. Why:
  readability must not replace the original. Status: Implemented at the Stage 5
  application/CLI model and Stage 7 desktop rendering; accepted real KDE
  verification includes Japanese grouped-layer transitions and toggles.
  Related: ADR 0006.
- **Other non-Latin scripts** — Support appropriate transliteration for
  Cyrillic, Greek, Arabic, Thai, and other scripts through replaceable engines.
  Why: the model must not be limited to three languages. Status: Initial
  ICU-backed Cyrillic, Greek, Arabic, and Thai support is implemented; other or
  unsupported scripts remain clean extension work.

## Romanization / transliteration

- **Japanese romaji** — Generate or import aligned romaji while preserving
  kanji/kana and ambiguous-reading diagnostics. Status: Implemented in Stage 5
  with Cutlet Modified Hepburn and durable CLI/application correction; desktop
  UX remains later work.
- **Korean romanization** — Generate or import aligned Hangul romanization with
  uncertainty and pronunciation-aware correction. Status: Implemented in Stage
  5 with ICU Hangul-Latin and correction diagnostics.
- **Chinese pinyin** — Generate or import pinyin where language, segmentation,
  and readings can be handled responsibly. Status: Implemented in Stage 5 for
  explicitly Chinese Han with ICU tone marks; ambiguous Han stays unavailable.
- **Generated-output provenance** — Store generator name/version, source,
  confidence, uncertainty, and replacement history. Why: generated text must
  never masquerade as provider or user-approved text. Status: Implemented in
  Stage 5 with qualitative uncertainty and typed schema 4 records. Related: ADR
  0006.
- **User romanization corrections** — Edit, approve, reject, or replace
  generated lines without changing original lyrics. Status: Implemented in
  Stage 5 through current-document CLI/application operations; desktop UX
  remains Stage 7/8. Related: Roadmap Stages 5 and 8.

## Translation

- **Optional aligned translation** — Import or generate an independently
  toggleable third lyric layer. Why: meaning should be available without
  replacing original or romanized text. Status: Imported/user-aligned storage,
  approval, precedence, diagnostics, and toggle structure are implemented in
  Stage 5. Translation generation remains Deferred; no cloud/API adapter exists.
  Prerequisites for generation: `TranslationProviderPort`, provenance and
  provider review.

## Sync improvements

- **Manual global and per-line timing repair** — Let users correct offsets and
  broken line timing after synchronization. Status: whole-document delay is
  implemented and restart-safe in Stage 6; durable per-line repair/editor is
  part of the deferred fast manual synchronization workflow above.
  Prerequisites: correction UI and approved per-line persistence semantics.
- **Qualified current-stream audio routing** — Replace default-sink-only
  diagnostic evidence with a proven selected-MPRIS-stream-to-output mapping
  where PipeWire exposes one safely, so fresh graph/device latency plus the
  exact-device residual may be applied automatically. Status: Investigation;
  Stage 6 deliberately leaves current default-sink evidence diagnostic-only.
  Prerequisites: stable route identity, output-change events, real analog/USB/
  HDMI/Bluetooth evaluation, and no audio capture.
- **Automatic timing/alignment drafts** — Investigate vocal separation and
  audio/text alignment that proposes, but does not silently approve, timings.
  Why: expand synced coverage. Status: Deferred. Prerequisites: stable manual
  review workflow, licensing and performance research. Related: Stage 11+.

## Library management

- **Directory scanning** — Incrementally scan configured music roots while
  handling moved, missing, and inaccessible files. Status: Completed in Stage
  9 with automated and accepted disposable-directory evidence.
  Prerequisites: persistence and metadata reader. Related: Roadmap Stage 9.
- **Batch lyrics downloading** — Pre-download only policy-approved,
  high-confidence matches and queue uncertainty for review. Status: Completed
  in Stage 9 with accepted real provider/review evidence.
  Prerequisites: directory scanning, provider policy, cancellation and cache.
  Related: Roadmap Stage 9.

## Performance

- **Evidence-gated native module** — If a Python/native binding, packaging,
  concurrency, reliability, or measured performance problem justifies it,
  assess one coherent `lyricflow-native` Rust module via PyO3/maturin. Do not
  rewrite functioning Python subsystems, including the Stage 6 clock, merely
  for theoretical speed. Status: Architectural option only; no native code is
  authorized. Related: ADR 0013.

- **Large-library profiling** — Establish startup, scan, cache, and UI latency
  budgets with representative fixtures. Status: Investigation. Prerequisites:
  implemented scanner and desktop MVP.

## Packaging

- **Linux release packaging** — Select and document a reproducible Linux
  packaging format, upgrades, backup, and uninstall behavior. Status: Planned.
  Prerequisites: stable v1 application and migration strategy. Related:
  Roadmap Stage 10.

## Public release readiness

This checklist is planning only. Changing repository visibility requires
separate explicit authorization.

- [ ] Select and deliberately accept a real open-source license; replace the
  current all-rights-reserved placeholder and update package metadata.
- [ ] Re-audit the complete Git history for credentials, private files, local
  databases, music, lyrics caches, and machine-specific configuration.
- [ ] Review screenshots, logs, recorded responses, and fixtures for personal
  information and unnecessary provider data.
- [ ] Verify runtime, development, build, and transitive dependency licenses are
  compatible with the selected project license and distribution plan.
- [ ] Complete README installation and end-user usage documentation for the
  supported release artifact.
- [x] Establish practical contribution guidelines in `CONTRIBUTING.md`; review
  them again for public contributors before release.
- [ ] Establish and verify a durable private security-reporting procedure; the
  current `SECURITY.md` deliberately names no unverified email address.
- [ ] Verify the complete quality workflow on GitHub Actions for the release
  candidate.
- [ ] Consider protected `main`, required passing CI, and review requirements
  without unnecessarily obstructing current single-developer work.
- [ ] Decide versioning, changelog, tagging, and release strategy.
- [ ] Verify the package/distribution approach, reproducibility, upgrade,
  backup, uninstall, and fresh-machine behavior.
- [ ] Change GitHub repository visibility only after explicit authorization and
  a final public-release audit.

## Technical debt

- **Imported Stage 1 draft review** — The recoverable code in commit `d4719b3`
  was reviewed as untrusted prior work and restored selectively rather than
  wholesale. The accepted replacement passed real KDE verification at
  `ba77489`. Status: Completed in Stage 1.
- **Coverage policy** — Add a measured coverage threshold only after choosing a
  meaningful baseline; never substitute a percentage for acceptance tests.
  Status: Deferred. Prerequisites: broader product code.

## Investigations

- **Provider licensing and health** — Recheck API terms, maintenance health,
  response limits, and replacement paths before each provider is accepted.
  Status: Ongoing policy. Related: `docs/DEPENDENCIES.md`.
- **Romanization engine evaluation** — Compare offline behavior, linguistic
  quality, language coverage, licensing, maintenance, runtime cost, and
  correction support. Status: Completed for the Stage 5 baseline through the
  Cutlet/UniDic and Unicode ICU review in `docs/DEPENDENCIES.md` and ADR 0011;
  reassess before replacing or expanding engines.

## Rejected / intentionally deferred ideas

- **Silent metadata modification** — Rejected. LyricFlow will not edit audio
  tags or files without explicit action and preview.
- **Fragile Strawberry private-schema dependency** — Rejected. Unsupported or
  mutable private storage must fail as a normal miss.
- **Audio fingerprinting and microphone listening for v1** — Rejected for v1;
  MPRIS is the accepted playback source. Related: ADR 0002.
- **Genius or Musixmatch scraping for v1** — Rejected. Related: ADR 0003.
- **Foobar2000/Wine-specific v1 integration** — Deferred because the observed
  setup exposed no MPRIS player.
- **Whole-project Rust rewrite** — Rejected. LyricFlow remains Python-first;
  only measured, coherent native extraction may be considered. Related: ADR
  0013.
- **Pre-emptive `lyricflowd` daemon** — Rejected until multiple simultaneous
  clients demonstrate a shared live-state need that justifies lifecycle, IPC,
  versioning, reconnect, supervision, and recovery costs. Related: ADR 0013.
- **Executable themes/configuration** — Rejected. Themes/layouts/configuration
  remain declarative data and never gain Python, shell, `eval`, command, or
  hidden plugin execution. Related: ADR 0014.
