# KonoKashi

[![CI](https://github.com/myonctl/KonoKashi/actions/workflows/ci.yml/badge.svg)](https://github.com/myonctl/KonoKashi/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB)
![Linux](https://img.shields.io/badge/platform-Linux-FCC624)

KonoKashi is a Linux-first desktop application for synchronized, multilingual
lyrics from MPRIS-compatible players. It keeps its cache, settings, corrections,
and lyric decisions local, while using LRCLIB only when local lyrics and cache do
not have a suitable result.

KonoKashi is usable and under active development.

The current local first-public-beta candidate is **0.1.0-beta.1**. No public
release or tag has been created.

![KonoKashi native Wayland lyric view using a controlled multilingual fixture](docs/images/konokashi-beta-lyrics.png)

_Native Wayland renderer at a normal window size; all displayed metadata and
lyrics are synthetic test fixtures._

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
- Configure behavior and semantic appearance through the desktop GUI, terminal
  settings interface, CLI, or one XDG TOML file.
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
3. Open Settings in the desktop app to choose players, lyric layers, appearance,
   spacing, and music-library roots.
4. Use the review controls when a recording or lyric match needs correction.
   Review also exposes exact-document Chinese/Japanese routing and local,
   per-line translation editing when lyrics are loaded.

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

Online provider results expire after seven days and no-result cache entries after
one day. **Possible lyrics matches…** can explicitly refresh online evidence.
Offline mode may label an expired positive result as stale fallback, but an
expired negative never suppresses a later online search.

## Configuration and data

The canonical configuration is `$XDG_CONFIG_HOME/konokashi/config.toml` (normally
`~/.config/konokashi/config.toml`). Inspect and edit it through:

```bash
konokashi settings
konokashi config path
konokashi config get lyrics.display.romanized
konokashi config set lyrics.display.romanized true
konokashi config validate
```

Appearance is one validated semantic profile shared by the desktop, Settings
TUI, CLI, and TOML. Built-in `default`, `compact`, `lyric-only`, `current-line`,
and `large-display` presets remain customizable. For example:

```toml
[appearance]
preset = "large-display"

[appearance.typography]
lyric_scale_percent = 125

[appearance.typography.original]
family = "Noto Sans CJK JP"
size = 48
weight = 700

[appearance.colors]
active_lyric = "#55DDEE"
background = "#101418E6"

[appearance.alignment]
lyrics = "center"
```

Colors use `#RRGGBB` or `#RRGGBBAA` and are normalized on application writes.
An unavailable font name remains configured while Qt/fontconfig supplies a
graceful multilingual fallback. The desktop Appearance reset removes the whole
appearance profile only; individual values can also be reset in
either settings frontend or with `konokashi config reset KEY`. Invalid external
edits are rejected atomically and the last-known-good appearance stays active.

Desktop Settings keeps **Search settings…** visible above the categories. Press
Ctrl+F to search names, descriptions, canonical keys, categories, and sections
across all settings, including collapsed branches and advanced controls. Native
tree navigation groups Functionality and Appearance beneath disclosure arrows;
Workspace and Advanced describe their current bounded capabilities. Escape clears
the query, then leaves the search field.
Edits preserve the current category, scroll position, and keyboard focus.
Wheel gestures over numeric and choice editors scroll the page without changing
their values. **Reset Appearance…** asks for confirmation, with Cancel selected
by default; individual resets remain immediate.

**Lyrics scale** in Appearance adjusts original, romanized, and translated lyrics
together from 50% to 200% (100% by default). It multiplies configured lyric sizes
while preserving their relationships and leaves menus and metadata unchanged.
Typography offers installed or manual font families, a multilingual live preview,
Unicode-aware substring filtering (for example, `noto sa`), and named weights
from Thin through Black while preserving exact configured values. Browsing and
typing do not save a font until a choice is committed. Motion offers Instant or
Smooth lyric transitions plus Slow, Normal, Fast,
and exact custom speed choices. Wrapped original, romanized/transliterated, and
translated layers are measured at the available width; unusually tall active
lyrics use the lyric area's scrollbar instead of overlapping. Smooth mode follows
the measured old/new line positions only for adjacent changes. Seeks and track
changes settle immediately, while Instant and Reduce motion disable both movement
and opacity animation.

Preferred and ignored MPRIS players use collection editors with discovered stable
service-family suggestions and manual entry. Preferred order breaks ties between
equally active players; ignored entries always take precedence. Transient D-Bus
instance suffixes are not suggested.

Music Library uses **Add folder…** as its primary action and opens Qt's native
directory chooser; cancellation changes nothing. Multiple Unicode paths are
supported, while duplicate or nested roots are rejected with local feedback.
Manual absolute-path entry remains under Advanced. A completed scan reports every
counter and distinguishes success, partial errors, failure, and cancellation.
**Library results…** keeps bounded paths and error details local while providing
copy-path, open-containing-folder, and rescan actions; exported diagnostics contain
only error categories and counts.

The **Progress** category contains the bar visibility, thickness, fill and track
colors, opacity, corner radius, spacing, and timestamp visibility. Set the corner
radius to zero for square ends. Hiding the bar leaves timestamps independently
controllable; a track without a known duration has no progress bar.

Background color alpha and **Background opacity** combine to make the ordinary
desktop window transparent. They leave text opacity independent; whole-window
opacity, which would also fade text and controls, is not used. Settings and native
menus remain opaque and accessible. Wayland supports live updates; other platforms
need an alpha-capable composited surface and fall back to opaque painting when
Qt reports no alpha buffer.

Press Alt to focus the native application menu. File includes Settings (Ctrl+,),
library scanning, and Quit (Ctrl+Q); View controls component visibility and
appearance presets. Lyrics opens track/lyrics review, and Help provides diagnostics
and About. View changes persist through the same canonical configuration.

Application state is stored under `$XDG_DATA_HOME/konokashi`, and cache data under
`$XDG_CACHE_HOME/konokashi`. Configuration and databases are created with private
permissions. Uninstalling the package or desktop launcher does not delete these
directories.

## What works today

The synchronized lyric pipeline, local/provider lookup, offline cache,
multilingual representation layers, review workflow, incremental music-library
scanner, customizable desktop appearance and spacing, GUI settings, terminal settings
interface, CLI, migrations, backup, and privacy-bounded diagnostics are
functional.

Ambiguous and suitable no-result states expose **Possible lyrics matches…**.
That review surface shows provider identity, separate text/timing confidence and
evidence, and supports refresh, bounded manual title/artist search, choose,
reject, and reset. For a confirmed YouTube video, **Use YouTube metadata** may
optionally contact YouTube for that current public video's title, uploader,
duration, and description credits. It never downloads audio/video, reads
cookies, authenticates, uploads lyric text, or treats the description as lyrics.
Only bounded extracted recording fields are cached; description prose is
discarded.

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
- Native installations need an optional `yt-dlp` executable for user-requested
  YouTube metadata enrichment; its absence leaves normal resolution unchanged.
- Translation generation is not implemented. Translation means aligned provider,
  imported, local, or user text. Desktop Review can edit and approve one exact
  original line; neither that path nor the representation CLI uploads lyrics.
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
