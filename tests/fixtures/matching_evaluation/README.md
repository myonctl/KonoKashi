# Matching-evaluation fixtures

`synthetic_cases.json` contains invented metadata and tiny invented lyric lines.
It exercises the development evaluator without network access, personal playback
history, local media paths, credentials, or commercial lyrics.

Run it from an editable development environment:

```bash
.venv/bin/python scripts/evaluate_matching.py \
  tests/fixtures/matching_evaluation/synthetic_cases.json
```

The evaluator accepts multiple files and directories and can emit a stable JSON
report with `--json`. By default, an oracle mismatch exits 1; input/schema errors
exit 2.

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
