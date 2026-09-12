# AUR preparation

`konokashi` is the sole proposed package. The recipe identifies the public
`0.1.0-beta.1` prerelease as `pkgver=0.1.0beta1` and consumes the immutable
GitHub release sdist `konokashi-0.1.0b1.tar.gz`. Its SHA-256 matches the public
`SHA256SUMS.txt`. Do not upload that source archive to an AUR Git repository.

The project is source-available under `PolyForm-Noncommercial-1.0.0`; do not
describe it as OSI Open Source. Keep `pkgrel=1` for this new upstream version
and increment it for packaging-only updates. Regenerate `.SRCINFO` using
`makepkg --printsrcinfo` after every recipe change.

The package uses PEP 517 wheel construction and installs into makepkg's staging
directory with Python installer. Neither build nor package functions download
dependencies or modify the host Python installation. Runtime packages are
declared individually; native dependencies remain separately packaged. KonoKashi's
own payload now contains the bounded C++ PlaybackClock experiment, so the recipe
targets `x86_64` and `aarch64` and builds against the official `pybind11`
make dependency. Architecture-specific dependency resolution, native compilation,
and installation must pass before submission. No provides/conflicts or VCS
variant is necessary for the sole package name.

The 2026-09-11 dependency review found that official Arch provides `pypinyin`
0.55.0 (the package name does not have a `python-` prefix) and all other direct
repository dependencies at the required versions. AUR provides current
`python-fugashi`, `python-mojimoji`, and `python-unidic-lite` recipes. The two
remaining gaps are AUR `python-cutlet` at 0.5.0-2, older than the required
0.5.2, and the absent `python-jaconv` package required by cutlet. Validated,
separate package-base candidates live under `dependencies/`. That directory
also contains a corrected `python-unidic-lite` maintainer-update candidate;
see its README for the dependency order and submission boundary.

For local validation, place the matching source archive beside a copy of the
recipe in a temporary build directory, then run `makepkg --verifysource`,
`makepkg`, and `namcap PKGBUILD` plus namcap on the resulting package. Compare
`makepkg --printsrcinfo` with the tracked `.SRCINFO`. A clean Arch chroot is the
preferred final build environment. A `--nodeps` experiment can validate layout,
but is not proof of a resolvable or installable AUR dependency graph.

The package check function installs the built platform wheel into an isolated
staging root and exercises package identity, parsing, both PlaybackClock
implementations and their differential suite, and all offline romanization
adapters. The complete upstream suite is a separate release gate.
After package installation, use the normal `konokashi` and desktop launcher;
do not run the user-local desktop integration installer over pacman-owned files.
Pacman removal should leave XDG configuration, database, cache, and music intact.

## Local results

The public immutable source verifies against its pinned SHA-256. Generated
`.SRCINFO` comparison and shell syntax pass. On 2026-09-11 the complete package
chain was built with checks enabled in a disposable official Arch 2026.09.01
bootstrap, then KonoKashi was installed through pacman with normal dependency
resolution. The installed CLI/import/romanization smoke passed, and package
removal preserved synthetic XDG configuration, database, and cache files. No
`--nodeps` or `--nocheck` option was used.

Official Arch namcap 3.6.0 reports no findings for the three companion
PKGBUILDs or their package archives, and no findings for the KonoKashi
PKGBUILD. Its KonoKashi package scan reports no errors; its warnings are false
positives for imports within the same `konokashi` package plus the dynamically
loaded `python-fugashi` and `python-unidic-lite` dependencies. Both dynamic
imports were exercised after the normal package install.

Sources reviewed: [Arch Python packaging](https://wiki.archlinux.org/title/Python_package_guidelines),
[AUR submission guidelines](https://wiki.archlinux.org/title/AUR_submission_guidelines),
and [`.SRCINFO`](https://wiki.archlinux.org/title/.SRCINFO).
