# Flatpak candidate

This directory contains a local-build candidate, not a Flathub submission.
`io.github.myonctl.LyriFlux` matches the current GitHub repository identity and
the existing desktop application ID.

The manifest uses the current `io.qt.PySide.BaseApp` 6.11 branch and generated,
hash-pinned Python sources. Its local `dir` source deliberately excludes Git,
maintainer-private notes, environments, and build output. Before a Flathub
submission, replace that source with an immutable public release archive and
checksum; move the manifest to the submission repository's top level; preserve
the declared `PolyForm-Noncommercial-1.0.0` project license; and disclose
AI-assisted packaging as required by the current Flathub generative-AI policy.

Fugashi is built separately from the exact upstream v1.5.2 Git commit because
its PyPI source build reported version `0.0.0` in this SDK. MeCab is also built
from a pinned upstream commit. Preserve these adjustments when regenerating
the Python dependency manifest.

## Permissions

- `--share=network`: query LRCLIB when local lyrics and cache miss.
- `--socket=wayland` and `--socket=fallback-x11`: display the Qt desktop UI.
- `--share=ipc` and `--device=dri`: Qt/X11 shared-memory and accelerated display
  support; neither grants file or bus access.
- `--talk-name=org.mpris.MediaPlayer2.*`: observe only MPRIS player names on the
  filtered session bus. The unrestricted session-bus socket is not granted.
- `--filesystem=xdg-music:ro`: read audio metadata and adjacent sidecar lyrics
  beneath the user's standard Music directory. The home or host filesystem is
  not exposed.

The installed KDE runtime also adds read-only `xdg-config/kdeglobals` access
and talk access to `com.canonical.AppMenu.Registrar`, `org.kde.KGlobalSettings`,
and `org.kde.kconfig.notify` for desktop integration. Review effective
permissions with `flatpak info --show-permissions io.github.myonctl.LyriFlux`.

Arbitrary configured library roots outside the Music directory are unavailable
unless the user grants a path override. LyriFlux does not yet provide a file
chooser/portal workflow that can persist arbitrary root grants, so this is a
documented functionality difference rather than a reason to expose all of home.

Flatpak supplies private XDG paths below
`~/.var/app/io.github.myonctl.LyriFlux/`. Flatpak and native installations
therefore have intentionally separate configuration, data, and cache. Use
`flatpak run io.github.myonctl.LyriFlux settings` for the terminal settings UI or
`flatpak run --command=lyriflux io.github.myonctl.LyriFlux config path` for the
CLI; no host-config bridge is used.

## Local build

Install Flatpak Builder, then run from this directory:

```bash
flatpak run org.flatpak.Builder --user --force-clean --install-deps-from flathub \
  --repo=repo build io.github.myonctl.LyriFlux.yaml
flatpak --user remote-add --if-not-exists --no-gpg-verify lyriflux-local repo
flatpak --user install --noninteractive lyriflux-local io.github.myonctl.LyriFlux
```

Do not submit this candidate until the readiness report's blockers are resolved.
