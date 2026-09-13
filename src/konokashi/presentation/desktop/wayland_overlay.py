"""Capability-gated LayerShellQt bridge for passive floating lyrics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

import shiboken6
from PySide6.QtCore import QCoreApplication, QRect
from PySide6.QtGui import QGuiApplication, QWindow


class OverlayBackendKind(Enum):
    """Honest runtime backend identity for floating lyrics."""

    PORTABLE_QT = "portable-qt"
    NATIVE_LAYER_SHELL = "native-layer-shell"


@dataclass(frozen=True, slots=True)
class OverlayCapabilities:
    """Observed compositor and application support, without inferred effects."""

    backend: OverlayBackendKind
    layer_shell_version: int = 0
    background_effect_version: int = 0
    reason: str = ""

    @property
    def native_layer_shell(self) -> bool:
        return self.backend is OverlayBackendKind.NATIVE_LAYER_SHELL

    @property
    def description(self) -> str:
        if self.native_layer_shell:
            return (
                f"Native layer-shell v{self.layer_shell_version} while locked; "
                "unlocked floating lyrics remain movable with portable Qt"
            )
        return self.reason or "Portable Qt top-most floating lyrics window"


class _NativeOverlayModule(Protocol):
    plugin_path: str

    def probe(self) -> tuple[int, int]: ...

    def configure_locked_overlay(
        self,
        window_address: int,
        left: int,
        top: int,
        width: int,
        height: int,
    ) -> bool: ...


class WaylandOverlayBackend:
    """One optional native bridge with an unconditional portable fallback."""

    def __init__(
        self,
        capabilities: OverlayCapabilities,
        native: _NativeOverlayModule | None = None,
    ) -> None:
        self.capabilities = capabilities
        self._native = native

    def configure_locked_overlay(self, window: QWindow, geometry: QRect) -> bool:
        """Assign one dedicated, not-yet-shown window to the overlay layer."""

        if not self.capabilities.native_layer_shell or self._native is None:
            return False
        screen = window.screen()
        screen_geometry = screen.geometry() if screen is not None else QRect()
        left = max(0, geometry.x() - screen_geometry.x())
        top = max(0, geometry.y() - screen_geometry.y())
        address = int(shiboken6.getCppPointer(window)[0])
        try:
            return self._native.configure_locked_overlay(
                address,
                left,
                top,
                geometry.width(),
                geometry.height(),
            )
        except (IndexError, OverflowError, RuntimeError, ValueError):
            return False


def discover_wayland_overlay_backend() -> WaylandOverlayBackend:
    """Observe real protocol support; never infer it from desktop branding."""

    platform = QGuiApplication.platformName()
    if not platform.startswith("wayland"):
        return WaylandOverlayBackend(
            OverlayCapabilities(
                OverlayBackendKind.PORTABLE_QT,
                reason="Portable Qt top-most floating lyrics window",
            )
        )
    try:
        from konokashi import _wayland_overlay_native as native
    except ImportError:
        return WaylandOverlayBackend(
            OverlayCapabilities(
                OverlayBackendKind.PORTABLE_QT,
                reason=(
                    "Portable Qt fallback; the optional LayerShellQt bridge "
                    "is not installed"
                ),
            )
        )
    if native.plugin_path:
        QCoreApplication.addLibraryPath(native.plugin_path)
    layer_shell_version, effect_version = native.probe()
    if layer_shell_version <= 0:
        return WaylandOverlayBackend(
            OverlayCapabilities(
                OverlayBackendKind.PORTABLE_QT,
                background_effect_version=max(0, effect_version),
                reason=(
                    "Portable Qt fallback; this compositor does not advertise "
                    "wlr layer-shell"
                ),
            ),
            native,
        )
    return WaylandOverlayBackend(
        OverlayCapabilities(
            OverlayBackendKind.NATIVE_LAYER_SHELL,
            layer_shell_version=layer_shell_version,
            background_effect_version=max(0, effect_version),
        ),
        native,
    )


__all__ = [
    "OverlayBackendKind",
    "OverlayCapabilities",
    "WaylandOverlayBackend",
    "discover_wayland_overlay_backend",
]
