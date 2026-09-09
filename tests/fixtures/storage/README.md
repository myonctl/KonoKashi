# Historical storage fixture

`historical_schema_10.sqlite3` is a synthetic regression artifact created by
running migrations 1 through 10 exactly as defined at accepted commit
`b7a33abd5600c6f31833cf67648ee4ddd042f920`. It was not initialized with the
current migration registry.

The fixture contains invented recording/source identities, synchronized lyric
lines, generated and user-approved representation state, a correction, an
approved provider match, provider cache state, a library track, and a completed
library scan. Paths use only the `/synthetic` namespace. It contains no owner or
other personal data and no complete copyrighted lyrics.

The committed fixture SHA-256 is:

```text
a10857b1d203966afe9d641c112f059530bd283133a6923645ff8ebd2f41fa4d
```

To regenerate it from the retained Git history, first remove or move the old
fixture deliberately, then run:

```bash
.venv/bin/python tests/fixtures/storage/generate_historical_schema_10.py
```

The generator refuses to overwrite an existing fixture and pins the ten
historical checksums, including migration 4's published identity.
