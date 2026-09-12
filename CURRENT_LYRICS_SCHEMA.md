# Current lyrics JSONL schema v2

`konokashi sync current --jsonl` writes one compact JSON object per line. The
schema identifier is `io.github.myonctl.konokashi.current-lyrics` and the
top-level `version` is `2`.

Version 2 is intentionally incompatible with version 1: the generic fine-timing
value formerly called `active_word` is now `active_segment`. The v2
`active_word` field is present only for a segment whose semantic unit is really
`word`, or for the real word parent of the active finer segment.

## Record fields

- `schema`: stable schema identifier string.
- `version`: integer `2`.
- `event`: currently `state`.
- `sequence`: positive counter local to one encoder process.
- `generation`: source-generation counter; changes when the selected playback
  source changes.
- `observed_monotonic_us`: local monotonic observation time in microseconds.
- `track`: title, artists, album, and duration in microseconds.
- `playback`: status, disciplined media position, optional audible position,
  and playback rate.
- `lyrics`: document status, opaque document ID, source attribution,
  provenance, match confidence, timing level, and display delay.
- `active_lines`: all simultaneously active line records in source order.
- `active_segment`: the latest trusted fine-timing leaf reached at the audible
  display position, or `null`.
- `active_word`: the semantic word corresponding to `active_segment`, or
  `null` when no real word identity exists.
- `next_line`: the next line record, or `null`.
- `next_transition_monotonic_us`: local monotonic deadline for the next line,
  or `null`.

An active or next line record contains `line_id`, source `index`, original
`text`, `source_timestamp_us`, `effective_transition_us`, and optional aligned
`reading` and `translation` objects. Representation objects retain kind, text,
provenance, approval state, source, source version, language, script, and
uncertainty.

## Segment fields

`active_segment` and `active_word` use the same object shape:

- `line_id` and `segment_id`: stable opaque identities within the document.
- `text`: exact segment text, including meaningful spacing.
- `unit`: `word`, `syllable`, `grapheme`, or `provider-element`.
- `source_start_us` / `source_end_us`: provider or imported media timing.
- `effective_start_us` / `effective_end_us`: timing after local corrections.
- `provenance`: timing provenance, or `null`.
- `parent_segment_id`: parent timing identity when a hierarchy is present.
- `provider_unit`: provider's explicit or retained element kind only when
  `unit` is `provider-element`.

Consumers must branch on `unit`; they must not infer semantics from text length,
spacing, `provider_unit`, or the presence of `active_word`.

## Emission and privacy

Serialization is deterministic: keys are sorted, UTF-8 text is not ASCII
escaped, and a record never contains an embedded JSONL delimiter. Ordinary
position and highlight-fraction ticks are suppressed; meaningful track,
playback, lyric, line, representation, or active-segment transitions emit a new
record.

The stream omits local paths, raw MPRIS URLs, and stable source identities. It
does contain lyric text and playback metadata, so consumers must treat it as
private playback data.
