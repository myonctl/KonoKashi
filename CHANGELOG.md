# Changelog

All notable user-visible changes are recorded here.

## 0.1.0-beta.2 (unreleased)

### Added

- Provider-neutral unsynchronized, line, word, and finer-element timing with
  stable nested segment identities, explicit timing provenance, durable storage,
  and frontend-neutral fallback to line synchronization.
- Enhanced LRC word timing parsing that retains the original source while
  exposing calibrated word segments to frontend snapshots.
- Trust-gated desktop karaoke highlighting for exact word-timed lyrics, with
  wrapped multilingual text, clock-driven interpolation, and line-sync fallback.
- Independent reading and translation identities through synchronized frontend
  state, with compact active-line captions for romaji, pinyin, Korean readings,
  generic transliteration, and provider/imported/user/generated provenance.
- Normal, compact, desktop-overlay, and fullscreen lyric modes over one shared
  renderer, with screen targeting and explicit overlay move/resize/exit controls.
- Safety-gated click-through overlay behavior with system-tray unlock and quit
  routes; environments without a tray retain an interactive overlay.
- Read-only Unison lyric retrieval alongside LRCLIB, with explicit ODbL
  attribution, provider confidence, independent caching, bounded response
  hydration, and rich media-clock TTML word timing.
- Configurable provider preference and bounded parallel lookup with isolated
  failure, cancellation, privacy-safe duration/result diagnostics, and
  conservative conflict handling instead of first-response selection.
- A compact lyric line editor reachable from the active view, with immutable
  source evidence, local text/timestamp overlays, playback-position stamping,
  preview, undo, ordering validation, and one-action revert.
- Portable UTF-8 plain-text and LRC import/export for the current lyric document,
  including private atomic file writes and explicit overwrite protection.
- A versioned semantic JSON Lines current-lyrics stream for shell scripts,
  status bars, OBS, widgets, and local display projects, including current track,
  playback state, active line/word, next line, provenance, and timestamps.
- A focused synchronized lyrics TUI over the same clock, timeline, correction,
  representation, and snapshot boundaries as the desktop renderer.
- Midnight, Paper, and High Contrast color themes with explicit custom-color
  precedence and live GUI/TUI/CLI configuration.
- Bounded local MPRIS album art with responsive rounded presentation,
  stale-result rejection, and an optional artwork-derived background tint that
  preserves the configured primary-text contrast floor.
- One bounded C++20/pybind11 PlaybackClock implementation behind the unchanged
  Python-facing API, with the prior Python implementation retained as a
  differential reference oracle.

### Changed

- The normal desktop header now prioritizes track identity and lyrics, keeps one
  quiet review/correction action visible, moves settings, lyric details, and
  library scanning into a discoverable overflow menu, and promotes recovery
  only when a match actually needs attention.
- Healthy source, confidence, and synchronization boilerplate no longer occupies
  the listening surface; uncertain matches and degraded synchronization remain
  explicit and actionable.
- Python support is explicitly bounded to 3.11–3.14 and every advertised
  interpreter is exercised by the CI quality gate.
- Security guidance now points to the repository's enabled private
  vulnerability-reporting workflow.
- Flatpak folder selection now uses the asynchronous XDG desktop portal with a
  correctly exported X11 or Wayland parent window, without broadening filesystem
  or session-bus permissions.
- Rich-timing progress updates use a lightweight paint path so lyrics remain
  stable while seeking, pausing, changing playback rate, or refreshing metadata.
- Fresh profiles show available aligned translations by default to complete the
  original/reading/translation hierarchy; explicit existing choices are kept.
- Wayland overlay behavior is described as a portable Qt top-most fallback;
  above-fullscreen layer-shell placement is not claimed when unavailable.
- Resolved-track corrections now cover album as well as title and artists, while
  provider metadata and media tags remain unchanged.
- Album art is decoded outside the Qt UI thread from local PNG/JPEG/WebP files
  only; remote artwork is not fetched and source paths are not retained in the
  frontend asset.
- Linux wheels are now interpreter- and architecture-specific because they
  contain the native PlaybackClock module; source builds require a C++20
  compiler and pinned pybind11 build input.

## 0.1.0-beta.1 — 2026-09-10

### Added

- Product-first public documentation and lightweight contribution, security,
  issue, and pull-request guidance.
- Public-release privacy, licensing, AUR, Flatpak, freedesktop, and packaging
  readiness audit.
- Frontend-neutral appearance profiles with independent multilingual typography,
  semantic RGB/RGBA colors and opacity, spacing, alignment, visibility, lyric
  context, bounded motion preferences, and five declarative presets.
- Live desktop appearance application, GUI color/font/preset controls, and
  matching validated text controls in the terminal settings interface.

### Changed

- Maintainer stage-control documents and live operational evidence were removed
  from the tracked public surface and preserved in an ignored local archive.
- Ignore rules now cover private notes, diagnostics, profiles, draft/private
  screenshots, and playback scratch data.
- Wrapped timed-lyric layers now use measured height-for-width geometry and a
  scrollable active region, while adjacent transitions follow stable line
  positions and Instant/Reduce motion remain fully still.
- Desktop MPRIS requests are asynchronous and coalesced; library scans report
  truthful partial/failure states; Settings font and folder selection require
  deliberate commits.
- Recording candidate review, optional bounded YouTube metadata enrichment,
  multilingual representation state, and local translation editing now retain
  provenance and expose their failure/availability state.

## Pre-beta internal build — 2026-08-23

This was an internal package milestone, not a public stable release.

- Added the PySide6 synchronized-lyrics desktop, MPRIS player selection, local
  sidecar/embedded and LRCLIB lookup, durable offline cache, multilingual
  representation layers, review/correction flow, and playback synchronization.
- Added canonical XDG TOML settings shared by the GUI, terminal settings app,
  CLI, and file editing; incremental local-library scanning; SQLite migrations,
  backup, and privacy-bounded diagnostics.
- Added user-local desktop integration, reproducible wheel/source builds, and
  clean artifact lifecycle checks.
