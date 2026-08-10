# LyricFlow

Linux-first desktop application that detects the currently playing song through MPRIS, resolves the track identity, finds local or online lyrics, and displays synchronized lyrics against the real playback position. For non-Latin-script songs, it shows the original lyric with a romanized/transliterated line beneath it and an optional translation layer.

`LyricFlow` is a working project name. Renaming it later must not affect domain or storage design.

## Product direction

The first supported environment is KDE Plasma on Linux.

Known usable players:

- Strawberry
- Firefox through `plasma-browser-integration`
- Other standards-compliant MPRIS players when their metadata is sufficient

Foobar2000 through Wine is explicitly outside v1 because it did not expose an MPRIS player in the user's test.

## Core product rules

1. The app is local-first.
2. Player discovery uses MPRIS, not audio fingerprinting.
3. Local `.lrc` files and approved cached results take priority over network providers.
4. LRCLIB is the first online provider.
5. Metadata is never silently rewritten.
6. A weak lyrics match is shown as uncertain and requires review.
7. The UI thread never performs network, filesystem-scan, database-migration, or long-running parsing work.
8. Every external integration sits behind a replaceable interface.
9. Agents implement one roadmap stage at a time.
10. A stage is not complete until automated tests, documentation, and stated manual checks pass.

## Repository documents

Read these before changing code:

- `AGENTS.md` — permanent rules for every coding agent
- `AGENT_TODO.md` — the only currently authorized stage
- `docs/PRODUCT_SPEC.md` — product behaviour and boundaries
- `docs/ARCHITECTURE.md` — package boundaries and data flow
- `docs/ROADMAP.md` — gated implementation sequence
- `docs/TESTING.md` — test strategy and manual test matrix
- `docs/adr/` — architecture decisions that must not be casually reversed
- `CODEX_MASTER_PROMPT.md` — initial prompt to start Codex correctly

## Development status

**Stage 1 — MPRIS diagnostic core** is implemented and awaiting the real KDE
manual checks recorded in `AGENT_TODO.md`. The code connects through PySide6
QtDBus, preserves raw player metadata in typed models, and reports player
lifecycle/property/seek events. It does not select a preferred player,
interpret track titles, suppress duplicates, fetch lyrics, or create a GUI.

## Install for development

LyricFlow requires Python 3.11 or newer. From the repository root:

```bash
python -m venv .venv
.venv/bin/python -m pip install ".[dev]"
```

PySide6 is the only runtime dependency. The development extra adds pytest,
Ruff, and mypy. The rationale, layer boundaries, and replacement strategy for
each dependency are recorded in `docs/DEPENDENCIES.md`.

## Diagnostic commands

```bash
.venv/bin/lyricflow --version
.venv/bin/lyricflow doctor
.venv/bin/lyricflow players list
.venv/bin/lyricflow players inspect plasma-browser-integration
.venv/bin/lyricflow players watch
```

`doctor` performs local checks only. It verifies Linux, the session D-Bus
environment variable, the `PySide6.QtDBus` import, writable XDG application
directories, and optional `playerctl` availability. It does not connect to or
discover media players. Missing required prerequisites produce exit code 1;
missing `playerctl` produces a warning and still exits successfully.

`players list` prints every registered `org.mpris.MediaPlayer2.*` service; it
does not score or hide duplicates. `players inspect` accepts either a short
suffix such as `plasma-browser-integration` or a full D-Bus service name.
`players watch` reacts to service registration/removal, MPRIS
`PropertiesChanged`, and `Seeked` signals and exits cleanly on Ctrl+C.
Durations and positions remain integer microseconds in models and output.

Automated tests use sanitized fixtures and a fake D-Bus boundary. They never
depend on the developer's live Strawberry, Firefox, or KDE session. Real-player
acceptance evidence belongs only in `docs/MANUAL_TEST_LOG.md`.

Run the same quality gates used by CI:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy src
```

The CI job uses Python 3.12 and installs `.[dev]` non-editably before running
these commands and the installed CLI smoke checks.
