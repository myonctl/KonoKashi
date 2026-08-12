# Sanitized Stage 2 policy fixtures

These are synthetic, sanitized equivalents of the accepted product examples,
not claims of new live capture. They exercise Stage 2 identity, selection, and
normalization policy without a session bus, network call, or private path.

The Stage 1 browser fixtures remain unchanged in `tests/fixtures/mpris/` and are
also reused by Stage 2 tests.

`youtube_non_music_duplicate.json` is sanitized from the read-only Stage 2 live
smoke failure on 2026-08-12. It permanently covers an empty reported artist and
a Firefox-only `- YouTube` title decoration without treating a documentary
title as an artist/song pair.
