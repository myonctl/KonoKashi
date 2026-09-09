# Flatpak candidate

This directory contains a local-build candidate, not a Flathub submission.
`io.github.myonctl.KonoKashi` matches the current GitHub repository identity and
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

- `--share=network`: query LRCLIB when local lyrics and cache miss, and contact
  YouTube only after the user selects **Use YouTube metadata** for the current
  confirmed public video.
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
permissions with `flatpak info --show-permissions io.github.myonctl.KonoKashi`.

The sandbox cannot use a host `yt-dlp` executable. The manifest therefore pins
the official 2026.08.19 Unix zipapp and its SHA-256 in `/app/bin`. KonoKashi
invokes it with user configuration ignored, without cookies, authentication,
playlist traversal, or media download. If that module is removed or unavailable,
ordinary MPRIS and LRCLIB resolution remains usable.

The desktop's **Add folder…** action uses Qt's native directory-only dialog. In a
Flatpak session Qt routes the native chooser through the desktop portal, so a user
can deliberately grant a selected directory without granting all of home or host.
The returned portal/document path is stored as the configured root. Access outside
the standard Music directory depends on that explicit portal grant and the host
portal implementation; manual paths alone cannot bypass the sandbox. Verify add,
restart, rescan, cancellation, Unicode paths, and grant persistence in the locally
built candidate. Do not add `--filesystem=home` or `--filesystem=host` to compensate
for a portal problem.

Flatpak supplies private XDG paths below
`~/.var/app/io.github.myonctl.KonoKashi/`. Flatpak and native installations
therefore have intentionally separate configuration, data, and cache. Use
`flatpak run io.github.myonctl.KonoKashi settings` for the terminal settings UI or
`flatpak run --command=konokashi io.github.myonctl.KonoKashi config path` for the
CLI; no host-config bridge is used.

## Local build

Install Flatpak Builder, then run from this directory:

```bash
flatpak run org.flatpak.Builder --user --force-clean --install-deps-from flathub \
  --repo=repo build io.github.myonctl.KonoKashi.yaml
flatpak --user remote-add --if-not-exists --no-gpg-verify konokashi-local repo
flatpak --user install --noninteractive konokashi-local io.github.myonctl.KonoKashi
```

Do not submit this candidate until the readiness report's blockers are resolved.
