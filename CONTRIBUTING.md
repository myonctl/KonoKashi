# Contributing to LyriFlux

Thank you for considering a contribution. LyriFlux is Linux-first and under
active development; focused bug fixes, tests, documentation improvements, and
well-scoped proposals are welcome.

Until an open-source license is selected, source access does not grant permission
to redistribute or publish modified copies. This policy will be updated before
the repository becomes public.

## Development setup

Use Linux and Python 3.11 or newer. From a fresh checkout:

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/lyriflux doctor
```

Qt needs a working EGL/OpenGL runtime for desktop use. Building PyICU from source
may require ICU development headers, `pkg-config`, and a C++ compiler.

## Making a change

- Open or reference an issue before a large behavioral or architectural change.
- Keep the pull request focused; roadmap entries are context, not blanket
  authorization to implement adjacent features.
- Preserve the boundaries in `docs/ARCHITECTURE.md` and the privacy rules in
  `AGENTS.md`.
- Add a regression test for every bug fix and proportionate tests for behavior
  changes.
- Use synthetic or deliberately sanitized fixtures. Never submit credentials,
  personal playback history, private paths, complete copyrighted lyrics, or
  unrelated diagnostic data.
- Update user documentation when installation, configuration, behavior, or known
  limitations change.
- Discuss a new runtime dependency before adding it and document the decision in
  `docs/DEPENDENCIES.md`.

## Quality gate

Run from the repository root:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy src
.venv/bin/python scripts/build_release.py --output-dir /tmp/lyriflux-dist
```

For a focused documentation-only change, explain which checks were run and why
the remainder were unnecessary. CI remains authoritative for the supported
Python matrix and reproducible artifact job.

## Pull requests

Describe the problem, behavior change, tests, manual verification, privacy
impact, dependency changes, and known limitations. Significant architectural
changes should include an ADR or explicitly explain why no durable decision is
needed.

Review feedback may request smaller commits or additional evidence. Do not weaken
tests, linting, formatting, typing, or security checks to obtain a green result.

Use `SECURITY.md` for vulnerabilities rather than opening a public issue.
