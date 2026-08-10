# Changelog

All notable repository changes are documented here. Current development remains
unreleased.

## Unreleased

### Added

- `PROJECT_STATE.md` as the concise current-reality and continuation entry point.
- `BACKLOG.md` for durable future features, technical debt, investigations, and
  intentionally deferred/rejected ideas outside stage authority.
- Documentation style, development workflow, and stage-completion standards.
- ADR 0007 establishing repository state and `AGENT_TODO.md` authority for
  context-free continuation.
- Repository-document consistency tests.
- Git history, beginning with imported-state snapshot `971bfbf` so the
  pre-hardening tree remains recoverable, followed by hardened Stage 0 boundary
  `1cc7e3d`.
- Stage 0 installable Python package with a `src/` architecture skeleton.
- `lyricflow --version` and local-only `lyricflow doctor` prerequisite reporting.
- pytest, Ruff, mypy, and GitHub Actions quality configuration.
- Product, architecture, testing, dependency, reference, roadmap, manual-log,
  and ADR baselines.

### Changed

- Standardized `AGENT_TODO.md` as the only implementation authority; it now
  explicitly authorizes no stage and records Stage 1 as Proposed only.
- Defined a zero-context start/end workflow, evidence language, append-only
  manual verification, Git discipline, dependency review, failure handling,
  extension boundaries, and exact handoff requirements.
- Assigned a non-overlapping responsibility to each repository document and
  updated the README documentation index.
- Clarified actual Stage 0 architecture separately from planned modules.
- Preserved multilingual original/romanized/translated requirements and all
  major future integrations in the product spec, roadmap, and backlog.

### Removed

- Removed the unverified Stage 1 MPRIS draft from the active tree to restore the
  explicitly required Stage 0-only boundary. Its pre-hardening form remains
  recoverable in commit `971bfbf` for review after Stage 1 is authorized; it is
  not accepted implementation or completion evidence.
