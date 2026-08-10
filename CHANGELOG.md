# Changelog

All notable changes will be documented here.

## Unreleased

### Added

- Stage 1 typed raw MPRIS player, metadata, inspection, and event models.
- PySide6 QtDBus session-bus discovery, bounded asynchronous property reads,
  service lifecycle subscriptions, `PropertiesChanged`, and `Seeked` handling.
- `lyricflow players list`, `players inspect`, and signal-driven `players watch`
  diagnostics with clean Ctrl+C shutdown.
- Sanitized Firefox and Plasma Browser Integration evidence fixtures plus fake
  D-Bus lifecycle, malformed-data, Unicode, race, and CLI tests.
- Stage 0 installable Python package with a `src/` architecture skeleton.
- `lyricflow --version` and the local-only `lyricflow doctor` prerequisite
  report with tested exit-code behavior.
- pytest, Ruff, and mypy quality configuration plus matching GitHub Actions CI.
- Dependency decision record, repository hygiene files, and license decision
  placeholder.
- Added core multilingual lyric layers: original script, aligned romanization/transliteration, and optional translation.
- Initial product, architecture, testing, and agent-workflow plans.
- Added optional, read-only Strawberry lyrics interoperability investigation and architecture constraints.
