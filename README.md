# KonoKashi

**Synchronized, multilingual lyrics for the Linux desktop.**

[![CI](https://github.com/myonctl/KonoKashi/actions/workflows/ci.yml/badge.svg)](https://github.com/myonctl/KonoKashi/actions/workflows/ci.yml)
![Linux](https://img.shields.io/badge/platform-Linux-FCC624)
![Python 3.11–3.14](https://img.shields.io/badge/python-3.11%E2%80%933.14-3776AB)

![KonoKashi advancing through synchronized Japanese, romanized, and English lyric layers](assets/readme/konokashi-demo.gif)

_The real desktop renderer, driven by a copyright-safe synthetic lyric fixture._

KonoKashi is a Linux-first desktop app that follows music from players and
browser integrations, looks for a suitable lyric match, and advances timed
lines with playback. Original script stays primary; romanization or
transliteration and translation remain separate, optional layers. The app is
local-first, deeply customizable, and source-available.

## Why KonoKashi?

- **Read the song, not just the transcription.** Keep Japanese, Chinese, Korean,
  and other original scripts visible alongside Latin readings.
- **Follow whatever is playing.** Detect Strawberry and browser playback through
  Linux MPRIS instead of tying lyrics to one music service.
- **Prefer what is yours.** Reuse approved lyrics, local sidecars, embedded
  lyrics, and durable cached matches before making a live LRCLIB request.
- **Stay in control.** Review uncertain matches and retain corrections locally;
  low-confidence results are not silently approved.
- **Make it fit your desktop.** Tune typography, colors, transparency, spacing,
  visible layers, motion, and progress presentation.

## Features

- **Synchronized lyrics:** timed line changes plus progressive highlighting for
  trustworthy word-timed lyrics, with exact pause, seek, rate, and track-change
  handling; line-only, plain, and instrumental states remain explicit.
- **Multilingual layers:** original text plus aligned romanization or
  transliteration and optional translation, with each active secondary layer's
  identity and provenance visible without displacing the original script.
  Available translations are shown by default and remain independently toggled.
- **Local-first resolution:** sidecar and embedded lyrics, SQLite-backed cache,
  offline reuse, and LRCLIB fallback.
- **Player-aware matching:** conservative metadata handling for Strawberry and
  KDE Plasma Browser Integration, including duplicate MPRIS entries.
- **Review tools:** choose or reject lyric matches, correct resolved identity,
  adjust whole-document timing, and maintain local per-line translations.
- **Multiple controls:** native PySide6 desktop, graphical settings, a terminal
  settings interface, CLI diagnostics, and one canonical TOML configuration.

Local reading support covers Japanese and Mandarin with language-aware limits,
Korean romanization, and generic transliteration for Cyrillic, Greek, Arabic,
and Thai.
Generated readings are labeled in the lyric view. Romanization is a reading aid,
not a promise of perfect sung pronunciation.

## What the demo shows

The demo advances a local four-line fixture through KonoKashi's real lyric
viewport and smooth-transition system. Each active group contains:

```text
夜の窓に　星がほどける
Yoru no mado ni hoshi ga hodokeru
Stars unravel in the window of night.
```

The title, artist, lyrics, and translation were written specifically for this
demo. No commercial song, audio, playback history, or private user data is
included.

## Try it

KonoKashi supports Linux with Python 3.11 through 3.14; every advertised version
is exercised in CI. For the current beta, install the immutable public release
with your distribution's `pipx` package. Pipx creates and manages the isolated
environment for you:

A desktop session needs a Qt/EGL runtime and session D-Bus. If PyICU has no
wheel for your Python version, install ICU development headers, `pkg-config`,
and a C++ compiler first.

```bash
pipx install 'https://github.com/myonctl/KonoKashi/releases/download/v0.1.0-beta.1/konokashi-0.1.0b1.tar.gz'
konokashi doctor
konokashi desktop-integration install
```

Launch KonoKashi from the application menu, or run `konokashi desktop`. Upgrade
to a future release with `pipx install --force <new-release-url>`, and remove the
application with `pipx uninstall konokashi`. Removal intentionally preserves
your XDG configuration, cache, and lyric database.

For a development checkout instead:

```bash
git clone https://github.com/myonctl/KonoKashi.git
cd KonoKashi
python -m venv ~/.local/lib/konokashi
~/.local/lib/konokashi/bin/python -m pip install --upgrade pip
~/.local/lib/konokashi/bin/python -m pip install .
~/.local/lib/konokashi/bin/konokashi doctor
~/.local/lib/konokashi/bin/konokashi desktop-integration install
```

Launch KonoKashi from the application menu, or run:

```bash
~/.local/lib/konokashi/bin/konokashi desktop
```

Then start an MPRIS player such as Strawberry—or play media through KDE Plasma
Browser Integration—and KonoKashi will follow the selected source. AUR and
Flatpak recipes exist for local validation, but neither is published yet. See
the [packaging status](https://github.com/myonctl/KonoKashi/blob/main/packaging/README.md)
for validated routes and explicit decisions on later formats.

For a development checkout, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Customize it

Open **Settings** in the desktop app for discoverable controls, or run
`konokashi settings` for the full-screen terminal settings interface. Both edit
the same validated configuration as the CLI and
`$XDG_CONFIG_HOME/konokashi/config.toml`.

Built-in presets provide quick starting points, while typography, layer
visibility, colors, background opacity, spacing, alignment, lyric scale, smooth
motion, and progress styling remain independently adjustable. Unavailable fonts
fall back through Qt/fontconfig without replacing the configured choice.

## Current status

KonoKashi is usable and under active development. **0.1.0-beta.1** is the first
public beta.

Linux is the only supported host today. Real-world verification has focused on
Artix Linux with KDE Plasma, Strawberry, and Plasma Browser Integration. Other
standards-compliant MPRIS players may work, but are not all individually
certified.

Translation generation is not implemented. A translation layer contains aligned
provider, imported, local, or user-approved text; KonoKashi does not silently
send lyrics to a machine-translation service. A full lyrics TUI, web frontend,
additional rich-timing provider formats, and Windows support are future work.

## What's next

- Refine the Linux desktop experience through public-beta feedback.
- Expand lyric-text and per-line timing correction while preserving originals.
- Explore deeper themes, compact/overlay layouts, and a full lyrics TUI.
- Stabilize release packaging before any AUR or Flathub submission.

See the concise [backlog](BACKLOG.md) for longer-term direction.

## License

KonoKashi is source-available under the PolyForm Noncommercial License 1.0.0;
read the full [license terms](LICENSE). It may be used, studied, modified, and
redistributed for permitted noncommercial purposes under that license. The
noncommercial restriction means it is **not OSI Open Source**.

## Contributing

Focused bug fixes, tests, documentation improvements, and well-scoped proposals
are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull
request.

## Security

Please follow [SECURITY.md](SECURITY.md) for private vulnerability reporting
guidance. Do not post credentials, private paths, playback history, lyrics, or
other sensitive media information in a public issue.
