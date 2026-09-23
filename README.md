# KonoKashi

**Synchronized, multilingual lyrics for Linux.**

[![CI](https://github.com/myonctl/KonoKashi/actions/workflows/ci.yml/badge.svg)](https://github.com/myonctl/KonoKashi/actions/workflows/ci.yml)
![Linux](https://img.shields.io/badge/platform-Linux-FCC624)
![Python 3.11–3.14](https://img.shields.io/badge/python-3.11%E2%80%933.14-3776AB)

![KonoKashi showing synchronized original, romanized, and translated lyric layers](assets/readme/konokashi-demo.gif)

KonoKashi follows music from Linux players and browsers through MPRIS, finds a
safe lyric match, and keeps timed lyrics aligned with playback. Original script
stays primary; readings and translations are optional separate layers.

## Highlights

- Timed, plain, and instrumental lyric states with pause, seek, rate, and track
  change handling.
- Original lyrics alongside Japanese or Mandarin readings, Korean romanization,
  and transliteration for several other scripts.
- Local-first lookup: approved choices, sidecars, embedded lyrics, and cache are
  checked before LRCLIB and Unison.
- Automatic handling of messy browser and YouTube metadata without assuming
  that the uploader is the artist.
- Conservative matching: unclear versions or competing candidates stay
  ambiguous instead of silently showing the wrong lyrics.
- Normal, compact, floating-lyrics, and fullscreen desktop modes, plus terminal
  and JSONL output.
- Local review and correction tools for recording identity, lyric choice, text,
  timestamps, translations, and global offset.

## Try it

KonoKashi is usable and under active development.
It supports Python 3.11 through 3.14.
The current public beta is **0.1.0-beta.2**. For x86-64 Linux, install the
Flatpak bundle:

```bash
curl --fail --location --output /tmp/KonoKashi-0.1.0-beta.2-x86_64.flatpak \
  https://github.com/myonctl/KonoKashi/releases/download/v0.1.0-beta.2/KonoKashi-0.1.0-beta.2-x86_64.flatpak
flatpak install --user --reinstall \
  /tmp/KonoKashi-0.1.0-beta.2-x86_64.flatpak
```

Launch it from your application menu or run:

```bash
flatpak run io.github.myonctl.KonoKashi desktop
```

If your session has a stale document-portal mount, use:

```bash
flatpak run --no-documents-portal io.github.myonctl.KonoKashi desktop
```

The direct bundle is not yet published through Flathub. Checksums are attached
to the [release](https://github.com/myonctl/KonoKashi/releases/tag/v0.1.0-beta.2).
The 0.1.0-beta.1 artifacts remain available as historical release artifacts.

### Install from source

```bash
git clone --branch v0.1.0-beta.2 --depth 1 \
  https://github.com/myonctl/KonoKashi.git
cd KonoKashi
./setup.sh
.venv/bin/konokashi desktop
```

The setup helper never runs `sudo` or a system package manager. It checks
prerequisites, builds a local `.venv`, tests the native modules, and prints any
required distro packages. See [packaging/README.md](packaging/README.md) for
packaging status and dependencies.

## Use

Start an MPRIS player such as Strawberry, or play media through KDE Plasma
Browser Integration. KonoKashi selects a player, resolves the recording, and
shows lyrics automatically when the evidence is strong enough.

Open **Settings** to change typography, colors, opacity, spacing, visible lyric
layers, motion, album-art treatment, providers, or window mode. Open
**Review lyrics** only when you need to inspect an uncertain result or make a
correction. Approved corrections are stored locally and reused.

KonoKashi can automatically fetch metadata for one public YouTube video when
ordinary playback metadata is insufficient. Disable this under
**Automatically improve metadata for web media** if you do not want those
requests.

## Privacy

KonoKashi has no account, telemetry, advertising, or cloud sync.

- It does not download YouTube audio or video.
- It does not use browser cookies, profiles, or authentication.
- Online lyric sources receive only the resolved title, musical artist, and
  available album or duration.
- YouTube enrichment requests only bounded public metadata for the current
  video or a tightly limited public search when a browser omits the video URL.
- Lyrics, corrections, cache, configuration, and playback knowledge stay in
  your local XDG directories.
- Album art is loaded only from a local `file:` URI supplied by the player.

Uploader names are evidence, never automatic artist truth. Provider order and
response speed also do not decide correctness.

## Terminal output

Run the focused lyrics interface:

```bash
konokashi sync current --tui
```

Or emit local newline-delimited JSON for Waybar, OBS helpers, widgets, and other
tools:

```bash
konokashi sync current --jsonl --no-pipewire
```

The field contract is documented in
[CURRENT_LYRICS_SCHEMA.md](CURRENT_LYRICS_SCHEMA.md). JSONL output contains
track metadata and lyric text, so treat it as private playback data.

## Current limits

- Linux is the only supported host.
- Real-world testing currently focuses on KDE Plasma, Strawberry, and Plasma
  Browser Integration.
- Translation display and editing are supported, but KonoKashi does not
  generate translations or send lyrics to a translation service.
- Floating click-through depends on compositor and LayerShellQt support; the
  portable top-most fallback remains available elsewhere.
- This is beta software. Please report reproducible failures without posting
  private paths, playback history, credentials, or copyrighted lyric bodies.

## Project links

- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)
- [Backlog](BACKLOG.md)
- [Wayland overlay notes](WAYLAND_OVERLAY_REVIEW.md)
- [Security policy](SECURITY.md)

## What's next

Short-term work focuses on safer automatic matching, desktop polish, and wider
Linux testing. See the [backlog](BACKLOG.md) for longer-term plans.

## License

KonoKashi is source-available under the PolyForm Noncommercial License 1.0.0;
read the [license terms](LICENSE). It is not OSI Open Source.
