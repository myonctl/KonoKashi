# Contributing to KonoKashi

Thank you for considering a contribution. KonoKashi is Linux-first and under
active development; focused bug fixes, tests, documentation improvements, and
well-scoped proposals are welcome.

Contributions are accepted under the project's
`PolyForm-Noncommercial-1.0.0` license. By submitting a contribution, you agree
that it may be distributed under those terms. Read `LICENSE` before contributing.

## Development setup

Use Linux and Python 3.11 through 3.14. From a fresh checkout:

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/konokashi doctor
```

Qt needs a working EGL/OpenGL runtime for desktop use. The authorized native
PlaybackClock and bounded LRC parser require a C++20 compiler, OpenSSL
development headers, and the pinned pybind11 build input; building PyICU from
source may additionally require ICU development headers and `pkg-config`.
LayerShellQt development files enable the optional bounded Wayland surface
bridge; ordinary development builds retain the portable Qt fallback if absent.

## Making a change

- Open or reference an issue before a large behavioral or architectural change.
- Keep the pull request focused; roadmap entries are context, not blanket
  authorization to implement adjacent features.
- Preserve the existing application/domain boundaries and local-first privacy
  model; discuss significant architecture changes before implementing them.
- Add a regression test for every bug fix and proportionate tests for behavior
  changes.
- Use synthetic or deliberately sanitized fixtures. Never submit credentials,
  personal playback history, private paths, complete copyrighted lyrics, or
  unrelated diagnostic data.
- Update user documentation when installation, configuration, behavior, or known
  limitations change.
- Discuss a new runtime dependency before adding it and explain its purpose,
  licensing, runtime impact, and replacement path in the pull request.

## Quality gate

Run from the repository root:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy src
.venv/bin/python scripts/build_release.py --output-dir /tmp/konokashi-dist
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

PlaybackClock and bounded LRC parsing are the only authorized native migrations.
Keep both Python references and differential suites intact; migrating another
subsystem requires a separate decision based on real maintenance and packaging
evidence. `NATIVE_02_CANDIDATE_REVIEW.md` records the parser boundary and
`NATIVE_02_BENCHMARK.md` records its production gate.
The small LayerShellQt surface bridge is presentation integration rather than a
third core migration; its capability and fallback boundary is recorded in
`WAYLAND_OVERLAY_REVIEW.md`.

Use `SECURITY.md` for vulnerabilities rather than opening a public issue.
