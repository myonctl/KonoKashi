# Wayland overlay capability review

Review date: 2026-09-13. This review covers the bounded native surface bridge;
it is not another native-core migration.

## Decision

Floating lyrics use two deliberate roles:

- While unlocked, the existing Qt `xdg_toplevel` remains movable and resizable
  through compositor-owned system move/resize operations.
- When click-through is locked, the same desktop workspace is transferred to a
  dedicated LayerShellQt window on the overlay layer. Unlocking transfers it
  back to the original `xdg_toplevel`.
- If the process is not using Wayland, the bridge is absent, or the compositor
  does not advertise `zwlr_layer_shell_v1`, KonoKashi retains its portable Qt
  top-most fallback.

The dedicated window is essential. A live prototype showed that assigning
LayerShellQt to the application's normal `QWindow` permanently selected the
layer-shell surface role: hiding and showing it again created another layer
surface instead of restoring an `xdg_toplevel`. Wayland surface roles are not a
mode toggle.

## Capability matrix

| Runtime observation | Locked floating behavior | Unlocked behavior |
| --- | --- | --- |
| Wayland + native bridge + `zwlr_layer_shell_v1` | Native overlay-layer surface | Portable movable Qt top-level |
| Wayland without protocol or bridge | Portable Qt top-most fallback | Portable movable Qt top-level |
| X11 or another Qt platform | Portable Qt top-most fallback | Portable movable Qt top-level |

This runtime probe intentionally does not infer support from a desktop name.
KWin and wlroots-based compositors can take the enhanced route when they expose
the protocol. Mutter/GNOME and any other compositor without the advertised
global take the fallback route without an error or a support claim.

The bridge separately records `ext_background_effect_manager_v1` when present,
but beta.2 does not request blur. That compositor-specific capability is neither
the standard layer-shell protocol nor sufficient evidence of a portable blur
contract, so the UI does not claim or fake blur.

## Native contract

The C++20/pybind11 bridge is intentionally narrow:

- `probe()` opens a short-lived Wayland connection and reports only advertised
  layer-shell and background-effect protocol versions.
- `configure_locked_overlay()` accepts an existing dedicated `QWindow`, monitor-
  relative geometry, and no lyric/application state.
- LayerShellQt assigns the overlay layer, top/left anchors, desired size, output,
  negative exclusive zone, no keyboard interactivity, and an application scope.
- Qt's transparent-for-input and does-not-accept-focus flags make the passive
  host click-through. Click-through remains unavailable unless the tray supplies
  an out-of-window recovery action.

Monitor targeting selects the `QScreen` before layer configuration. Geometry is
expressed as top/left margins relative to that output, avoiding assumptions about
the compositor's global-coordinate model. Moving a locked overlay to another
screen reconfigures both the output and those relative margins.

The bridge is optional for source development (`KONOKASHI_LAYER_SHELL=auto`) so
unsupported platforms still build. Release, Flatpak, and Arch package builds set
`KONOKASHI_LAYER_SHELL=required` and fail closed if their declared native SDK is
missing. Flatpak pins upstream LayerShellQt 6.7.5 commit
`f18f51d5abe59e55a338d953acf8fd169a3d7d1d`; Arch declares `layer-shell-qt`.

## Evidence and limits

The maintainer host ran KDE Plasma/KWin on Wayland with Qt/PySide 6.11.2 and
LayerShellQt 6.7.5. `wayland-info` observed layer-shell v5 and KWin's background-
effect v1. A filtered `WAYLAND_DEBUG=client` trace demonstrated:

1. the normal surface receiving `xdg_surface` + `xdg_toplevel`;
2. only the locked host receiving `get_layer_surface(..., layer=overlay, ...)`;
3. the normal surface receiving a fresh `xdg_toplevel` after unlock;
4. identical workspace ownership before lock and after unlock.

A controlled opaque fullscreen Qt window was then placed below the locked lyrics
surface. A compositor screenshot showed the 760×360 lyrics host above that
fullscreen client at the requested centered geometry. This proves the tested
KWin behavior, not universal "over everything" behavior. The protocol's overlay
layer is above ordinary top/fullscreen client layers, but compositors retain
control of security surfaces, lock screens, privileged shell UI, and ordering
among peers. KonoKashi makes no claim beyond the compositor's protocol contract.

Automated regressions cover capability wording, shared-workspace transfer and
restoration, passive window flags, geometry passed to the native boundary, safe
recovery, and failure back to the portable backend.

Primary references:

- [wlr layer-shell unstable v1 protocol](https://wayland.app/protocols/wlr-layer-shell-unstable-v1)
- [LayerShellQt `Window` API](https://api.kde.org/legacy/plasma/layer-shell-qt/html/window_8h_source.html)
- [LayerShellQt usage documentation](https://api.kde.org/legacy/plasma/layer-shell-qt/html/dir_f3eec1e9e98e02e34c8efeb863b66c5f.html)
- [Phosh compositor protocol requirements](https://phosh-abfd01.pages.gitlab.gnome.org/gettingstarted.html)
