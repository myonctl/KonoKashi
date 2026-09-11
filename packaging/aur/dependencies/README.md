# AUR dependency candidates

KonoKashi's release archive has dependencies that are not currently satisfied
correctly by the official Arch repositories and current AUR recipes:

- `python-cutlet` is in AUR at 0.5.0-2, while KonoKashi requires 0.5.2.
- `python-jaconv` is required by cutlet and has no Arch or AUR package.
- `python-unidic-lite` is at the required upstream version, but its AUR recipe
  is flagged out of date, downloads a new `pip` during `build()`, ignores wheel
  build failure, and declares only one of the source archive's two licenses.

The directories here are independent, submission-ready AUR package bases for
those gaps. They are not vendored Python dependencies and must not be added
to the `konokashi` AUR package base. Submit or coordinate them as separate AUR
packages before submitting `konokashi`; existing package maintainers should
receive the `python-cutlet` and `python-unidic-lite` updates rather than
duplicate packages.

Build/install the dependency packages in this order in a clean Arch chroot.
The maintained `mecab-git` AUR package is the current provider of the otherwise
absent `mecab` package name:

1. the existing AUR `mecab-git` package
2. `python-jaconv`, `python-unidic-lite`, and the existing AUR
   `python-mojimoji` package
3. the existing AUR `mecab-ipadic` and `python-ipadic` packages when running
   the fugashi checks
4. the existing AUR `python-fugashi` package
5. `python-cutlet`
6. `konokashi`

Every candidate uses an immutable PyPI source distribution, a pinned SHA-256,
PEP 517 wheel construction, and `python-installer`. Regenerate each `.SRCINFO`
with `makepkg --printsrcinfo` after editing its `PKGBUILD`. A normal dependency
resolving build and install is required; `--nodeps` is not an acceptance test.

Metadata was reviewed against the official Arch package database, AUR RPC v5,
and PyPI release JSON on 2026-09-11.
