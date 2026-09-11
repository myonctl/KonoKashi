# Flatpak candidate

This directory contains the generic `0.1.0-beta.1` build candidate, not a
Flathub submission.
`io.github.myonctl.KonoKashi` matches the current GitHub repository identity and
the existing desktop application ID.

The manifest uses the current `io.qt.PySide.BaseApp` 6.11 branch, generated
hash-pinned Python sources, and the immutable public GitHub release sdist. It
installs the PolyForm Noncommercial license explicitly. Preserve the declared
`PolyForm-Noncommercial-1.0.0` project license and disclose AI-assisted
packaging as required by the current Flathub generative-AI policy.

Flathub's stable repository only accepts stable software, and current policy
says new submissions are not accepted to its beta repository. Do not submit
this first beta to Flathub; retain the manifest for direct local builds and
reassess submission when KonoKashi has a stable release.

Fugashi is built separately from the exact upstream v1.5.2 Git commit because
its PyPI source build reported version `0.0.0` in this SDK. MeCab is also built
from a pinned upstream commit. Preserve these adjustments when regenerating
the Python dependency manifest.

## Permissions

- `--share=network`: query configured LRCLIB/Unison sources when local lyrics and
  cache miss, and contact
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
ordinary MPRIS and configured provider resolution remains usable.

The desktop's **Add folder…** action explicitly calls the XDG desktop portal's
asynchronous directory chooser in a Flatpak session; outside Flatpak it retains
Qt's native directory-only dialog. The portal response is restricted to one local
directory URI, and cancellation does not change Settings. The returned
portal/document path is stored as the configured root. Access outside the standard
Music directory depends on that deliberate portal grant and the host portal
implementation; manual paths alone cannot bypass the sandbox. Verify add, restart,
rescan, cancellation, Unicode paths, and grant persistence in the locally built
candidate. Do not add `--filesystem=home` or `--filesystem=host` to compensate for
a portal problem.

On Wayland, the portal must receive an exported parent-window identifier to keep
the chooser attached to Settings. PySide does not expose that operation, so the
manifest builds the small `portal-parent-bridge` against `Qt6::GuiPrivate` from the
matching KDE SDK. The bridge calls Qt's own `portalWindowIdentifier()` and is loaded
only by a Wayland Flatpak process from the fixed `/app/lib` path. Keep the KDE
runtime, SDK, and PySide BaseApp branches aligned; rebuild the bridge whenever that
branch changes. X11 derives its standard hexadecimal window ID directly, and other
native installs do not load the bridge.

Flatpak supplies private XDG paths below
`~/.var/app/io.github.myonctl.KonoKashi/`. Flatpak and native installations
therefore have intentionally separate configuration, data, and cache. Use
`flatpak run io.github.myonctl.KonoKashi settings` for the terminal settings UI or
`flatpak run --command=konokashi io.github.myonctl.KonoKashi config path` for the
CLI; no host-config bridge is used.

## Local build

The canonical manifest always retains the immutable published release sdist.
To validate the current checkout instead, first derive a local manifest from a
twice-built, byte-identical current sdist. The helper also copies the colocated
portal bridge source required by the manifest. The command changes no tracked file:

```bash
python scripts/prepare_flatpak.py --output-dir build/flatpak-current
```

Install Flatpak Builder, then run from the repository root:

```bash
flatpak run org.flatpak.Builder --user --force-clean --install-deps-from flathub \
  --repo=build/flatpak-current/repo build/flatpak-current/app \
  build/flatpak-current/io.github.myonctl.KonoKashi.yaml
flatpak --user remote-add --if-not-exists --no-gpg-verify konokashi-local \
  build/flatpak-current/repo
flatpak --user install --noninteractive konokashi-local io.github.myonctl.KonoKashi
```

If the sandboxed Flatpak Builder reports a stale `rofiles-fuse` mount, retry that
build with `--disable-rofiles-fuse`. This changes only the local build mechanism,
not the application sandbox or exported result.

Do not submit this beta candidate to Flathub under the current stable-release
policy.
