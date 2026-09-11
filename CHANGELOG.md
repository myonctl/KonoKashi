# Changelog

All notable user-visible changes are recorded here.

## Unreleased

### Added

- Provider-neutral unsynchronized, line, word, and finer-element timing with
  stable nested segment identities, explicit timing provenance, durable storage,
  and frontend-neutral fallback to line synchronization.
- Enhanced LRC word timing parsing that retains the original source while
  exposing calibrated word segments to frontend snapshots.

### Changed

- Python support is explicitly bounded to 3.11–3.14 and every advertised
  interpreter is exercised by the CI quality gate.
- Security guidance now points to the repository's enabled private
  vulnerability-reporting workflow.
- Flatpak folder selection now uses the asynchronous XDG desktop portal with a
  correctly exported X11 or Wayland parent window, without broadening filesystem
  or session-bus permissions.

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
