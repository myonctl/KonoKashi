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
own payload is architecture independent (`any`); x86_64 dependency resolution and
installation must pass before submission. No provides/conflicts or VCS variant
is necessary for the sole package name.

The 2026-09-10 dependency review found AUR `python-cutlet` at 0.5.0-2, older
than the required 0.5.2, and no AUR packages named `python-pypinyin` or
`python-jaconv`. The latter is also a dependency of `python-cutlet`. Resolve
those packages before submission or claiming a normal installation works.

For local validation, place the matching source archive beside a copy of the
recipe in a temporary build directory, then run `makepkg --verifysource`,
`makepkg`, and `namcap PKGBUILD` plus namcap on the resulting package. Compare
`makepkg --printsrcinfo` with the tracked `.SRCINFO`. A clean Arch chroot is the
preferred final build environment. A `--nodeps` experiment can validate layout,
but is not proof of a resolvable or installable AUR dependency graph.

The package check function exercises package identity, parsing, and all offline
romanization adapters. The complete upstream suite is a separate release gate.
After package installation, use the normal `konokashi` and desktop launcher;
do not run the user-local desktop integration installer over pacman-owned files.
Pacman removal should leave XDG configuration, database, cache, and music intact.

## Local results

The public immutable source verifies against its pinned SHA-256. Generated
`.SRCINFO` comparison and shell syntax pass. A normal makepkg/clean-chroot build
remains blocked by the unresolved runtime dependency graph above; a `--nodeps`
build is not proof of a resolvable or installable AUR package.

Signed distribution namcap 3.6.0 tooling was extracted into a temporary directory
for analysis. The PKGBUILD check passed. Package analysis prompted the addition
of `hicolor-icon-theme`; after rebuilding it reported no errors and 355 Python
dependency warnings in the incomplete host environment. Reassess these warnings
with the complete Arch dependency graph before submission. Namcap's successful
exit code alone does not establish a clean result.

Sources reviewed: [Arch Python packaging](https://wiki.archlinux.org/title/Python_package_guidelines),
[AUR submission guidelines](https://wiki.archlinux.org/title/AUR_submission_guidelines),
and [`.SRCINFO`](https://wiki.archlinux.org/title/.SRCINFO).
