# Changelog

All notable repository changes are documented here. Current development remains
unreleased.

## Unreleased

### Added

- Private-GitHub preparation: practical contribution and security guidance,
  structured bug/feature issue forms, and a pull request checklist.
- Created the private `myonctl/LyricFlow` GitHub repository with `origin` using
  the existing local history and files; no GitHub-generated project files were
  added.
- Pushed `main` with upstream tracking and verified the required GitHub Actions
  quality workflow successfully on the private remote.
- Added a permanent Git/GitHub attribution policy and repository-local identity
  preflight for user-representative commits and canonical pushes.
- A future public-release checklist covering licensing, history/privacy review,
  dependency licenses, CI, branch protection, release strategy, packaging, and
  explicit visibility authorization.
- `PROJECT_STATE.md` as the concise current-reality and continuation entry point.
- `BACKLOG.md` for durable future features, technical debt, investigations, and
  intentionally deferred/rejected ideas outside stage authority.
- Documentation style, development workflow, and stage-completion standards.
- ADR 0007 establishing repository state and `AGENT_TODO.md` authority for
  context-free continuation.
- Repository-document consistency tests.
- Git history, beginning with imported-state snapshot `d4719b3` so the
  pre-hardening tree remains recoverable, followed by hardened Stage 0 boundary
  `06e0d4e`.
- Stage 0 installable Python package with a `src/` architecture skeleton.
- `lyricflow --version` and local-only `lyricflow doctor` prerequisite reporting.
- pytest, Ruff, mypy, and GitHub Actions quality configuration.
- Product, architecture, testing, dependency, reference, roadmap, manual-log,
  and ADR baselines.

### Changed

- Rewrote the six pre-policy commits from a generic Codex identity to `myonctl`
  with a verified account-associated GitHub email. Commit order, messages,
  trees, and dates were preserved; ADR 0008 records the old-to-new mapping.
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
  recoverable in commit `d4719b3` for review after Stage 1 is authorized; it is
  not accepted implementation or completion evidence. The sanitized Firefox
  and KDE browser observations remain as project-history fixtures.
