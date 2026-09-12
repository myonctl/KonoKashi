# NATIVE-02 candidate review

Date: 2026-09-13  
Decision: migrate the bounded LRC/plain-text parser, and no other subsystem, in
Stage 11.

This review scores implementation-neutral core candidates against the current
tree. Scores run from 1 (unfavorable) to 5 (favorable). “Low impact” and “low
complexity” are intentionally phrased so a higher score is always better. The
totals guide the decision but do not replace the boundary and risk analysis.

| Candidate | Semantic stability | Low Python coupling | Boundary clarity | Parity-testability | Future native-core value | Low packaging impact | Low complexity | Total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| LRC/plain-text parsing | 5 | 5 | 5 | 5 | 5 | 4 | 4 | 33 |
| Lyric calibration composition | 5 | 4 | 5 | 5 | 1 | 4 | 5 | 29 |
| Timeline lookup and transition deadlines | 4 | 3 | 3 | 5 | 5 | 4 | 2 | 26 |
| TTML rich-timing parsing | 2 | 3 | 4 | 4 | 4 | 2 | 2 | 21 |
| Low-level metadata normalization | 3 | 2 | 3 | 4 | 4 | 1 | 2 | 19 |
| Provider-candidate matching | 2 | 1 | 2 | 5 | 4 | 4 | 1 | 19 |

## Selected: bounded LRC/plain-text parsing

`parse_lyrics_text` is a single, framework-neutral entry point shared by local
sidecars, embedded tags, provider documents, imports, corrections, CLI, desktop,
and TUI flows. Its core work is deterministic parsing and transformation over a
bounded Unicode string with integer millisecond arithmetic. It has no network,
Qt, storage, or linguistic-library dependency.

The current parser already defines useful stable semantics:

- a 2,000,000-character input bound;
- one leading BOM removal and CRLF/CR normalization;
- exact two- or three-digit fractional timestamps;
- multiple timestamp expansion with source-position and copy identity;
- signed global offset handling without clamping invalid negative results;
- supported, unsupported, and malformed metadata behavior;
- enhanced-LRC segment extraction and exact end derivation;
- stable SHA-256-derived line and segment IDs;
- chronological ordering that preserves equal-timestamp source order;
- plain-text fallback that preserves Unicode, empty, and repeated lines;
- deterministic status, timing level, metadata, diagnostics, normalized source,
  and checksum output.

This is worth migrating for long-term ownership of a durable input boundary,
not because “C++ is faster.” Stage 11 must measure runtime and peak allocation
behavior before making any performance claim.

## Deferred candidates

### Lyric calibration composition

The 40-line calibration helper is stable and easy to test, but moving it would
add a binding and packaging surface larger than the logic it removes. It should
remain Python unless it naturally joins a later, substantial native timing
boundary.

### Timeline lookup and transition deadlines

Binary-searchable timeline lookup and integer deadline calculation are strong
future native-core material. Today the subsystem consumes and returns many
Python domain values, includes error-budget policy, and is adjacent to the
already-native PlaybackClock. Migrating it now would either expose a broad
binding or duplicate domain assembly. Reconsider it only after NATIVE-02 is
packaged and maintained successfully.

### TTML rich-timing parsing

Stage 7 deliberately changed TTML hierarchy and timing-unit semantics. The
policy needs more real public-data exposure before it is stable enough to freeze.
A native implementation would also need a carefully reviewed bounded XML parser
or a new XML dependency. Migrating it now would combine semantic churn with
dependency and security risk.

### Low-level metadata normalization

Normalization depends on Python Unicode normalization/case-fold behavior and
feeds frequently refined artist/title policy. Exact cross-version parity would
require an explicit Unicode data strategy or native ICU linkage. That packaging
cost is not justified for the current helper surface.

### Provider-candidate matching

Matching is deliberately conservative product policy rather than a settled
mechanical transform. It depends on normalization, artist-credit parsing,
`SequenceMatcher`, title aliases, and evidence prose. Stage 10 explicitly avoids
moving it; real-corpus work in Stage 16 may still refine these semantics.

## Stage 11 contract

The production Python API remains:

```python
parse_lyrics_text(
    text: str,
    *,
    timing_provenance: TimingProvenance | None = TimingProvenance.PROVIDER,
    duration_ms: int | None = None,
) -> ParsedLyricsText
```

The existing implementation becomes an explicit Python reference oracle. A
new C++20 parser owns only syntax, bounded integer transformation, stable IDs,
and domain-neutral result records. A thin Python wrapper supplies
`TimingProvenance` and constructs the existing `ParsedLyricsText`, `LyricLine`,
and `LyricTimingSegment` values. Provider, filesystem, persistence, and Qt logic
must not enter the native module.

Production may switch to native only after all of the following are true:

1. Every existing LRC fixture and public parser test has exact native/reference
   parity, including IDs, diagnostic text/order, normalized text, and checksums.
2. Seeded randomized tests cover valid Unicode, newline forms, metadata,
   duplicate/multiple/out-of-order timestamps, offsets, enhanced markers,
   duration boundaries, integer overflow, malformed timestamp-like input, empty
   input, and bounded oversized input.
3. The binding rejects invalid values without crashes, unbounded allocation, or
   partial domain output.
4. The public `parse_lyrics_text` symbol selects native production behavior while
   the Python oracle remains directly testable and is not a silent fallback.
5. The binding surface is value-based and versionable; internal C++ containers
   are not exposed as application architecture.
6. Source, editable, wheel, sdist, AUR, and Flatpak builds include the new module;
   installed-package smoke tests exercise it.
7. Release reproducibility checks include every new source, stub, and native
   artifact, and the existing PlaybackClock build remains unchanged.
8. Runtime and peak-allocation measurements are recorded for representative
   plain, line-timed, enhanced, malformed, and maximum-bound inputs. Regression
   is disqualifying even if parity passes.

## Intended implementation shape

- Keep the Python reference in the infrastructure lyrics package with no native
  imports.
- Put parser data structures and parsing in C++ without Qt or provider headers.
- Expose one narrow `_lrc_native.parse` binding and immutable value records.
- Convert native records to existing domain values in the current Python module.
- Build a separate extension so LRC and PlaybackClock ownership and rollback
  remain independent.
- Update type stubs, `setup.py`, source manifests, release artifact assertions,
  AUR/Flatpak package checks, and CI rather than relying on editable-install
  success.

No other subsystem is authorized for native migration in Stage 11.
