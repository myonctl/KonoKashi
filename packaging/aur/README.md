# AUR preparation

`konokashi` is the sole proposed package. This is a **local candidate, not ready
for AUR submission**. The project is source-available under
`PolyForm-Noncommercial-1.0.0`, but no public source release exists.
The recipe currently consumes the exact reviewed local `konokashi-1.0.0.tar.gz`
source archive with a real SHA-256 checksum. It does not invent a public tag or
download URL. Do not upload the source archive to an AUR Git repository.

Before submission, select the public version, publish the separately authorized
immutable source release, replace `source` with its HTTPS asset URL, update its
checksum, and regenerate `.SRCINFO` using `makepkg --printsrcinfo`.
Keep `pkgrel=1` for a new upstream version and increment it for packaging-only
updates. The existing internal `1.0.0` is not a stable release announcement.

The package uses PEP 517 wheel construction and installs into makepkg's staging
directory with Python installer. Neither build nor package functions download
dependencies or modify the host Python installation. Runtime packages are
declared individually; native dependencies remain separately packaged. KonoKashi's
own payload is architecture independent (`any`); x86_64 dependency resolution and
installation must pass before submission. No provides/conflicts or VCS variant
is necessary for the sole package name.

Current dependency review found `python-cutlet` older than the required 0.5.2,
and missing `python-pypinyin`/`python-jaconv` results from AUR RPC. Recheck and
resolve those dependencies before claiming a normal installation works.

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

Source verification, generated `.SRCINFO` comparison, and shell syntax passed.
Normal makepkg stopped at missing runtime dependencies. A `--nodeps` build with
development-environment tooling completed and ran all 44 selected tests (two
upstream warnings). This is not an installed-product or clean-chroot result.

Signed distribution namcap 3.6.0 tooling was extracted into a temporary directory
for analysis. The PKGBUILD check passed. Package analysis prompted the addition
of `hicolor-icon-theme`; after rebuilding it reported no errors and 355 Python
dependency warnings in the incomplete host environment. Reassess these warnings
with the complete Arch dependency graph before submission. Namcap's successful
exit code alone does not establish a clean result.

Sources reviewed: [Arch Python packaging](https://wiki.archlinux.org/title/Python_package_guidelines),
[AUR submission guidelines](https://wiki.archlinux.org/title/AUR_submission_guidelines),
and [`.SRCINFO`](https://wiki.archlinux.org/title/.SRCINFO).
