# Contributing to LyriFlux

LyriFlux is a private, pre-alpha project intended to become public open source
after its licensing, security, privacy, packaging, and release prerequisites are
resolved. The current `LICENSE` grants no open-source permissions. Repository
access does not change that license status.

## Before making a change

Start from a clean checkout and follow this reading order:

1. `PROJECT_STATE.md` — current reality and exact continuation point.
2. `AGENTS.md` — durable repository rules.
3. `AGENT_TODO.md` — the only implementation authority.
4. Relevant sections of `docs/PRODUCT_SPEC.md`, `docs/ARCHITECTURE.md`,
   `docs/ROADMAP.md`, and `docs/TESTING.md`.
5. Relevant decisions in `docs/adr/`.
6. Existing implementation, tests, fixtures, Git history, and current diff.

An issue, roadmap entry, backlog item, pull request, source draft, or discussion
does not authorize implementation. Do not begin a Proposed stage until the user
explicitly authorizes it through `AGENT_TODO.md`.

## Development setup

LyriFlux requires Python 3.11 or newer. From the repository root:

```bash
python -m venv .venv
.venv/bin/python -m pip install ".[dev]"
```

Never commit `.venv`, caches, local databases, music, lyrics caches, credentials,
tokens, machine configuration, or reproducible build output.

## Making changes

- Keep changes coherent and inside the authorized scope.
- Do not silently expand scope or implement backlog features opportunistically.
- Preserve the dependency direction and replaceable boundaries documented in
  `docs/ARCHITECTURE.md`.
- Add or update tests with every behavior change. Keep regression tests and
  sanitized evidence fixtures permanently when practical.
- Do not weaken tests, linting, formatting, or type checking to obtain a pass.
- Document any new dependency according to `docs/DEPENDENCIES.md` and
  `AGENTS.md` before adding it.
- Record significant architecture or workflow decisions in an ADR. Never
  silently reverse an Accepted ADR.
- Follow `docs/DOCUMENTATION_STYLE.md` and leave an exact handoff according to
  `docs/DEVELOPMENT_WORKFLOW.md`.

## Quality gate

Run the complete gate from the repository root:

```bash
.venv/bin/python -m pip install --no-build-isolation --no-deps --force-reinstall .
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy src
.venv/bin/lyriflux --version
.venv/bin/lyriflux doctor
```

Record exact results. A configured CI workflow or passing subset is not proof
that the complete gate passed.

## Pull requests

Keep pull requests reviewable and explain scope, authorization, behavior,
tests, manual verification, documentation, dependencies, privacy impact, and
known limitations. Significant architecture changes require an ADR in the same
change or an already Accepted ADR that authorizes the direction.

Do not include secrets or unnecessary personal information in issues, pull
requests, logs, screenshots, fixtures, or commit history. Follow `SECURITY.md`
for sensitive reports.
