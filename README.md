# KonoKashi

[![CI](https://github.com/myonctl/KonoKashi/actions/workflows/ci.yml/badge.svg)](https://github.com/myonctl/KonoKashi/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB)
![Linux](https://img.shields.io/badge/platform-Linux-FCC624)

KonoKashi is a Linux-first desktop application for synchronized, multilingual
lyrics from MPRIS-compatible players. It keeps its cache, settings, corrections,
and lyric decisions local, while using LRCLIB only when local lyrics and cache do
not have a suitable result.

KonoKashi is usable and under active development.

The project is source-available under the PolyForm Noncommercial License 1.0.0.
It is free to use, study, modify, and redistribute for permitted noncommercial
purposes under that license.

## Why KonoKashi?

- Follow timestamped lyrics in a native PySide6 desktop window.
- Preserve original-script lyrics and show aligned romanization or
  transliteration beneath them.
- Keep optional translations separate from both the original and pronunciation
  layers.
- Resolve local sidecar and embedded lyrics before querying LRCLIB.
- Cache provider results and retain user-approved corrections locally.
- Configure the same nine settings through the desktop GUI, a terminal settings
  interface, the CLI, or an XDG TOML file.
- Detect Strawberry and browser playback through Linux MPRIS, with conservative
  handling when player metadata is incomplete.

For example, a multilingual lyric group can be displayed conceptually as:

```text
陽光彩虹小白馬
Yángguāng cǎihóng xiǎo báimǎ
optional translation
```

The original remains authoritative. Generated readings carry provenance and may
need correction; KonoKashi does not claim perfect romanization.

## Installation from source

KonoKashi currently supports installation from a source checkout. It requires
Linux and Python 3.11 or newer. A normal desktop installation also needs a Qt/EGL
runtime and a session D-Bus. PyICU may require ICU headers, `pkg-config`, and a
C++ compiler if a compatible wheel is unavailable for your Python version.

Do not install KonoKashi into the distribution's system Python. Use a dedicated
virtual environment:

```bash
git clone https://github.com/myonctl/KonoKashi.git
cd KonoKashi
python -m venv ~/.local/lib/konokashi
~/.local/lib/konokashi/bin/python -m pip install --upgrade pip
~/.local/lib/konokashi/bin/python -m pip install .
~/.local/lib/konokashi/bin/konokashi doctor
~/.local/lib/konokashi/bin/konokashi desktop-integration install
```

The last command installs a user-local launcher and icon. You can then start
KonoKashi from the application menu or run:

```bash
~/.local/lib/konokashi/bin/konokashi desktop
```

For development setup, see `CONTRIBUTING.md`. AUR and Flatpak packaging are being
prepared but are not published.

## Quick start

1. Start an MPRIS player such as Strawberry, or play media through KDE Plasma
   Browser Integration.
2. Launch KonoKashi from the application menu.
3. Open Settings in the desktop app to choose player, lyric layers, timing delay,
   and music-library roots.
4. Use the review controls when a recording or lyric match needs correction.

Useful diagnostics:

```bash
konokashi players list
konokashi lyrics current
konokashi config validate
konokashi storage status
konokashi diagnostics export
```

`diagnostics export` is intentionally privacy-bounded: it omits media paths,
track metadata, lyric text, usernames, hostnames, and free-form errors. Review any
diagnostic output before sharing it.

## Configuration and data

The canonical configuration is `$XDG_CONFIG_HOME/konokashi/config.toml` (normally
`~/.config/konokashi/config.toml`). Inspect and edit it through:

```bash
konokashi settings
konokashi config path
konokashi config get lyrics.show_romanization
konokashi config set lyrics.show_romanization true
konokashi config validate
```

Application state is stored under `$XDG_DATA_HOME/konokashi`, and cache data under
`$XDG_CACHE_HOME/konokashi`. Configuration and databases are created with private
permissions. Uninstalling the package or desktop launcher does not delete these
directories.

## What works today

The synchronized lyric pipeline, local/provider lookup, offline cache,
multilingual representation layers, review workflow, incremental music-library
scanner, desktop UI, GUI settings, terminal settings interface, CLI, migrations,
backup, and privacy-bounded diagnostics are functional.

Real-world verification has focused on Artix Linux with KDE Plasma, Strawberry,
and Plasma Browser Integration. Standards-compliant MPRIS players may work but
are not all individually certified.

## Known limitations

- Linux is the only supported host platform.
- A full lyrics TUI, web frontend, karaoke mode, word-level timing, and mobile
  host do not exist.
- Local sidecar access depends on the player exposing a usable local file URL.
- Browser/player metadata quality varies, so low-confidence matches are not
  accepted automatically.
- Translation generation is not implemented; translations are imported or
  user-provided layers.
- The current icon is an intentional temporary project mark, not a finalized
  trademark design.
- No public release, AUR package, or Flathub package exists yet.

## What's next

- Deeper appearance and layout customization, including more flexible lyric
  presentation and desktop or wallpaper-oriented modes.
- Better correction tools for lyric text and per-line timing.
- A full lyrics TUI and stable machine-readable event interface.
- Additional providers and frontends after the Linux desktop experience and
  packaging are stable.

## Documentation

- `docs/PRODUCT_SPEC.md` — behavior and product boundaries
- `docs/ARCHITECTURE.md` — layers, ports, storage, and runtime design
- `docs/ROADMAP.md` — public development direction
- `docs/TESTING.md` — local and CI quality gates
- `docs/DEPENDENCIES.md` — dependency rationale and licensing notes
- `docs/RELEASE.md` — build, install, upgrade, backup, and uninstall semantics
- `docs/MULTILINGUAL_SUPPORT.md` — current script/language support
- `docs/adr/` — durable technical decisions

## Contributing and security

Focused issues and pull requests are welcome. Read `CONTRIBUTING.md` before
submitting code. Do not disclose vulnerabilities or private media information in
a public issue; follow `SECURITY.md` instead.

## License

KonoKashi is source-available under the
[PolyForm Noncommercial License 1.0.0](LICENSE). It is free to use, study,
modify, and redistribute for permitted noncommercial purposes under the license.
The noncommercial restriction means KonoKashi should not be described as OSI Open
Source.

KonoKashi is the official project name. Forks and redistributed versions should
not imply that they are official KonoKashi releases or endorsed by the project.
