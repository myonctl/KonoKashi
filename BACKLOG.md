# Backlog

This file preserves durable work outside the currently authorized stage. A
backlog entry is never permission to implement it; only `AGENT_TODO.md` can
authorize implementation. Staged v1 ordering lives in `docs/ROADMAP.md`.

## Confirmed future features

- **Synced lyrics display** — Resolve and display timestamped lyrics against
  playback. Why: this is the core product outcome. Status: Planned.
  Prerequisites: Stages 1–6. Related: Roadmap Stages 4, 6, and 7.
- **User-approved corrections** — Let users correct track and lyrics matches,
  approve or reject results, and preserve those choices. Why: metadata and
  provider matches are fallible. Status: Planned. Prerequisites: stable source
  identity and persistence. Related: Roadmap Stages 2, 3, and 8.

## UX improvements

- **Correction and timing tools** — Provide manual lyric text, alignment, and
  timing repair tools. Why: provider and generated results can be wrong.
  Status: Deferred until after the v1 correction workflow. Prerequisites:
  lyric persistence and synchronization. Related: Roadmap Stage 11+.
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
  Investigation. Prerequisites: compatibility evidence and a replacement-safe
  adapter. Related: ADR 0005; Roadmap Stage 4.

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
  authoritative. Status: Planned core requirement. Prerequisites: lyric
  representation model. Related: ADR 0006; Roadmap Stages 4–5.
- **Stacked representation layers** — Show romanized/transliterated text
  directly under the original and optional translation underneath that. Why:
  readability must not replace the original. Status: Planned core requirement.
  Prerequisites: stable line alignment. Related: ADR 0006.
- **Other non-Latin scripts** — Support appropriate transliteration for
  Cyrillic, Greek, Arabic, Thai, and other scripts through replaceable engines.
  Why: the model must not be limited to three languages. Status: Planned.
  Prerequisites: language/script detection and adapter evaluation.

## Romanization / transliteration

- **Japanese romaji** — Generate or import aligned romaji while preserving
  kanji/kana and ambiguous-reading diagnostics. Status: Planned.
  Prerequisites: `RomanizationProviderPort`, provenance, correction UI.
- **Korean romanization** — Generate or import aligned Hangul romanization with
  uncertainty and pronunciation-aware correction. Status: Planned.
  Prerequisites: `RomanizationProviderPort`, provenance, correction UI.
- **Chinese pinyin** — Generate or import pinyin where language, segmentation,
  and readings can be handled responsibly. Status: Planned. Prerequisites:
  language-aware engine evaluation and correction UI.
- **Generated-output provenance** — Store generator name/version, source,
  confidence, uncertainty, and replacement history. Why: generated text must
  never masquerade as provider or user-approved text. Status: Planned.
  Prerequisites: representation persistence. Related: ADR 0006.
- **User romanization corrections** — Edit, approve, reject, or replace
  generated lines without changing original lyrics. Status: Planned.
  Prerequisites: aligned line identities and persistence. Related: Roadmap
  Stages 5 and 8.

## Translation

- **Optional aligned translation** — Import or generate an independently
  toggleable third lyric layer. Why: meaning should be available without
  replacing original or romanized text. Status: Planned. Prerequisites:
  `TranslationProviderPort`, alignment, provenance and provider review.

## Sync improvements

- **Manual global and per-line timing repair** — Let users correct offsets and
  broken line timing. Status: Planned for global offset in v1; advanced tooling
  Deferred. Prerequisites: playback clock and correction persistence.
- **Automatic timing/alignment drafts** — Investigate vocal separation and
  audio/text alignment that proposes, but does not silently approve, timings.
  Why: expand synced coverage. Status: Deferred. Prerequisites: stable manual
  review workflow, licensing and performance research. Related: Stage 11+.

## Library management

- **Directory scanning** — Incrementally scan configured music roots while
  handling moved, missing, and inaccessible files. Status: Planned.
  Prerequisites: persistence and metadata reader. Related: Roadmap Stage 9.
- **Batch lyrics downloading** — Pre-download only policy-approved,
  high-confidence matches and queue uncertainty for review. Status: Planned.
  Prerequisites: directory scanning, provider policy, cancellation and cache.
  Related: Roadmap Stage 9.

## Performance

- **Large-library profiling** — Establish startup, scan, cache, and UI latency
  budgets with representative fixtures. Status: Investigation. Prerequisites:
  implemented scanner and desktop MVP.

## Packaging

- **Linux release packaging** — Select and document a reproducible Linux
  packaging format, upgrades, backup, and uninstall behavior. Status: Planned.
  Prerequisites: stable v1 application and migration strategy. Related:
  Roadmap Stage 10.

## Technical debt

- **Imported Stage 1 draft review** — If Stage 1 is authorized, review the
  recoverable code in commit `971bfbf` as untrusted prior work rather than
  restoring it wholesale. Why: it was implemented before the required workflow
  gate and lacks real KDE verification. Status: Deferred pending Stage 1
  authorization. Prerequisites: compare against the then-current proposal.
- **Coverage policy** — Add a measured coverage threshold only after choosing a
  meaningful baseline; never substitute a percentage for acceptance tests.
  Status: Deferred. Prerequisites: broader product code.

## Investigations

- **Provider licensing and health** — Recheck API terms, maintenance health,
  response limits, and replacement paths before each provider is accepted.
  Status: Ongoing policy. Related: `docs/DEPENDENCIES.md`.
- **Romanization engine evaluation** — Compare offline behavior, linguistic
  quality, language coverage, licensing, maintenance, runtime cost, and
  correction support. Status: Planned before Roadmap Stage 5.

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
