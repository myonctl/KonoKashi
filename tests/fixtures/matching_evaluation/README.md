# Matching-evaluation fixtures

`synthetic_cases.json` contains invented metadata and tiny invented lyric lines.
It exercises the development evaluator without network access, personal playback
history, local media paths, credentials, or commercial lyrics.
`youtube_automatic_cases.json` additionally replays the real frontend's automatic
YouTube retry with an injected, lyric-free metadata response. It verifies both
recovery after missing musical-artist evidence and skipping enrichment after a
strong first pass. These synthetic checks do not establish real-world accuracy.
`album_free_get_cases.json` replays a duration-bounded LRCLIB lookup after
unhelpful search results, plus a conflicting-result case that must stay
ambiguous. It uses invented metadata and lyrics only.
`browser_url_less_cases.json` is based on an observed Chromium MPRIS field
shape, with invented artist, title, and lyric text. It checks that a clear
artist-first title can retrieve lyrics without a URL while uploader-only and
reversed interpretations stay ambiguous. A separate invented Topic case checks
that bounded, fields-only video discovery supplies credits without making the
browser source permanent. The evaluator preserves an empty
`track.album` because browsers can report it distinctly from an absent field.

Run it from an editable development environment:

```bash
.venv/bin/python scripts/evaluate_matching.py \
  tests/fixtures/matching_evaluation/synthetic_cases.json
```

The evaluator accepts multiple files and directories and can emit a stable JSON
report with `--json`. By default, an oracle mismatch exits 1; input/schema errors
exit 2.
An optional `youtube_metadata` object on a YouTube case is passed through the real
fields-only metadata parser and automatic frontend retry without contacting YouTube. For those
cases, set `expected.enrichment_used` explicitly. The report separately counts
enrichment invocation, metadata network contact, and correct automatic recoveries;
set `expected.enrichment_network_used` when the distinction matters. It never treats a wrong
automatic document as an enrichment success. Store only bounded, sanitized
recording fields and, if essential, a short credit line—never a full description
or lyric body.
An optional `web_discovery` object on a URL-less browser case supplies at most
five `search_results` rows and one `video_metadata` object. The real discovery
adapter and frontend retry run against these injected fields without network
access. A search match is only an ephemeral evidence hint; it does not change
the case's source identity or authorize a lyric by itself.
Optional `provider.query_results` rules bind a recorded response to an exact query
mode, title, and artist tuple. Rules may also bind `album`, `duration_ms`, and
`broad` when those fields were recorded; unmatched queries return no result. Use
these rules for YouTube cases so an unrelated first-pass search cannot see a
candidate that was actually found only after enrichment. Cases without rules
retain schema-1 provider behavior for backward compatibility.
Real-world cases must explicitly set `expected.known_supported` only when an
independent source/recording oracle justifies it; unlabeled cases are reported
as unclassified, never silently folded into the known-supported denominator.
The committed invented cases intentionally remain unclassified.
`expected.wrong_version_record_ids` can classify known incorrect versions
without weakening the general wrong-automatic-accept counter.

## Private empirical corpus

Put real-world cases under the repository-local ignored directory
`.matching-evaluation-private/`. Do not add that directory to Git and do not copy
it into issue attachments or release artifacts. A private case should retain only
the minimum metadata and candidate evidence needed to reproduce a decision:

- deliberately pseudonymous case ID and short problem description;
- typed MPRIS title, artist, album, duration, stable synthetic replacement URL,
  and track ID where they affect behavior;
- provider candidate metadata, provider record ID, and only tiny redacted or
  synthetic lyric text sufficient to distinguish plain from synchronized data;
- necessary prior approval/rejection/override state;
- explicit expected status, record, timing trust, origin, and network-use outcome.

Never retain a playlist, chronological playback log, account identifier, media
path, URL token, full provider response, or complete lyrics. Replace a real local
path or video ID with a stable synthetic identity after confirming identity type
is not itself the bug. When lyric content does not affect matching, use invented
one-line text. When parser content is essential, reduce it to the smallest
copyright-safe fragment or keep the entire case outside Git.

Corpus schema 1 is demonstrated by the committed fixture. Grow the ignored corpus
toward 100, 250, and 500 hostile cases; report only counts actually replayed.

## Beta.2 empirical snapshot

On 2026-09-14, beta.2 was replayed against 100 minimized cases in the ignored
private corpus. The source identities came from public LRCLIB records and were
independently confirmed by MusicBrainz (the first 33 accepted cases) or the
iTunes Search metadata API (the remaining 67) using normalized title, related
artist credit, and recording duration within two seconds. Provider lyric text
was replaced immediately with one invented line while preserving only whether
plain, synchronized, or instrumental content existed. URLs and local paths were
replaced with stable synthetic identities.

The snapshot covered 25 browser/YouTube-shaped cases, 75 local-file-shaped
cases, 14 Vocaloid cases, 5 anime-song cases, 8 non-Latin-title cases, 11 feature
credits, 10 remixes, 4 covers, 7 radio edits, 6 live versions, 45 artist-name
variants, 28 duplicate-title sets, 28 multi-result searches with wrong
candidates present, 5 bad-duration cases, and 5 local-sidecar overrides. All 100
omitted album metadata. An installed-package live check separately observed two
simultaneous MPRIS services without retaining their playback metadata.

Results through the production resolver were:

- 100/100 cases matched their independent oracle;
- 90/90 expected provider automatic accepts were correct;
- 0 expected accepts became unnecessary ambiguities;
- 5/5 deliberately bad-duration cases remained ambiguous;
- 5/5 local sidecars won without provider access;
- 0 wrong automatic accepts, 0 misses, and 0 wrong timing trusts.

The dogfood pass fixed six conservative false ambiguities: parenthesized feature
credits remain part of provider titles, and an exact repeated-title boundary can
disambiguate an artist name that itself contains a spaced dash. The minimized
regressions and full corpus retain zero wrong automatic accepts.
This deliberately selected hostile corpus is release evidence, not a claim of
population-wide matching accuracy. Translated artist names were attempted but
could not be distinguished reliably from ordinary public-database credit
variants, so only the broader artist-name-variant count is claimed.
