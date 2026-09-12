# Packaging status

KonoKashi maintains package routes only when their complete runtime dependency
graph can be built, tested, installed, upgraded, and removed without bypassing
the target package manager. The current routes are:

- The supported beta install path is `pipx install` from the immutable GitHub
  release archive documented in the root README. It is independently validated
  and requires neither a repository clone nor a manually managed virtual
  environment. Current-development wheels are platform-specific because of the
  one bounded native PlaybackClock module; source installs require a C++20
  toolchain and the pinned pybind11 build input.

- [`flatpak/`](flatpak/): a reproducible local-build candidate for the current
  beta. Flathub submission is intentionally deferred until a stable release.
- [`aur/`](aur/): an Arch/AUR candidate plus separate package candidates for
  genuine repository gaps. These are not published AUR package bases yet.

The following formats were investigated on 2026-09-11 and are deliberately not
represented by placeholder recipes.

| Format | Status | Decisive constraint | Revisit when |
| --- | --- | --- | --- |
| `.deb` | Deferred | Debian 13 lacks `cutlet`, `fugashi`, `unidic-lite`, and `mojimoji`; its `python3-pypinyin` 0.54.0 is below KonoKashi's 0.55 minimum. `python-jaconv` is currently available only in testing and unstable. | The required Japanese stack reaches a supported Debian release, or separate policy-compliant dependency packages have committed maintainers. |
| `.rpm` | Deferred | Fedora's official source-package index has no projects for `cutlet`, `fugashi`, `unidic-lite`, or `pypinyin`. | The missing dependencies enter Fedora, or separate reviewed packages have committed maintainers. |
| AppImage | Deferred | It would bundle the same Qt, Python, MeCab, and dictionary stack already covered by the Flatpak candidate, without solving a distinct distribution need today. | Users report a concrete environment that Flatpak and native packages cannot serve, and the bundle has an owned update and verification process. |

The repository evidence can be checked in the official indexes:

- Debian's [package search](https://packages.debian.org/) and
  [source-package search](https://sources.debian.org/advancedsearch/) cover the
  supported suites. The current
  [`python3-pypinyin`](https://packages.debian.org/python3-pypinyin) and
  [`python-jaconv`](https://sources.debian.org/src/python-jaconv/) records show
  the version and suite constraints above.
- Fedora's [package index](https://packages.fedoraproject.org/) and
  [source-package index](https://src.fedoraproject.org/) are the authoritative
  inventory used for the RPM check.

A future native package must keep third-party projects as independently
reviewable packages, declare every dependency normally, run upstream and
KonoKashi tests during the build, and pass clean-install and clean-removal
checks. It must not vendor dependencies into the application package, download
code during the build, use `--nodeps` or `--nocheck`, or claim support before a
fresh target-system proof exists.
