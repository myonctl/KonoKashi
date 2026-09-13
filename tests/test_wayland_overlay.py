"""Capability and surface-transfer regressions for the Wayland overlay."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from konokashi.presentation.desktop.main_window import MainWindow
from konokashi.presentation.desktop.wayland_overlay import (
    OverlayBackendKind,
    OverlayCapabilities,
    WaylandOverlayBackend,
)
from konokashi.presentation.desktop.window_surface import DesktopWindowMode


class _FakeNativeBridge:
    plugin_path = "/test/plugins"

    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.calls: list[tuple[int, int, int, int, int]] = []

    def probe(self) -> tuple[int, int]:
        return (5, 1)

    def configure_locked_overlay(
        self,
        window_address: int,
        left: int,
        top: int,
        width: int,
        height: int,
    ) -> bool:
        self.calls.append((window_address, left, top, width, height))
        return self.succeeds


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    application = QApplication.instance() or QApplication(["wayland-overlay-test"])
    assert isinstance(application, QApplication)
    return application


def _native_backend(bridge: _FakeNativeBridge) -> WaylandOverlayBackend:
    return WaylandOverlayBackend(
        OverlayCapabilities(
            OverlayBackendKind.NATIVE_LAYER_SHELL,
            layer_shell_version=5,
            background_effect_version=1,
        ),
        bridge,
    )


def test_native_capability_description_does_not_claim_blur() -> None:
    capabilities = _native_backend(_FakeNativeBridge()).capabilities

    assert capabilities.native_layer_shell
    assert "layer-shell v5" in capabilities.description
    assert "unlocked" in capabilities.description
    assert "blur" not in capabilities.description.lower()


def test_locked_native_surface_reuses_workspace_and_restores_normal_role(
    qt_app: QApplication,
) -> None:
    bridge = _FakeNativeBridge()
    window = MainWindow(overlay_recovery_available=True)
    window._wayland_overlay_backend = _native_backend(bridge)
    window.show()
    window.set_window_mode(DesktopWindowMode.OVERLAY)
    window.setGeometry(40, 50, 760, 360)
    qt_app.processEvents()
    workspace = window.centralWidget()

    assert window.set_overlay_locked(True)
    qt_app.processEvents()
    host = window._locked_overlay_host
    assert host is not None
    assert window._native_overlay_active
    assert window.isHidden()
    assert host.isVisible()
    assert host.centralWidget() is workspace
    assert host.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert host.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert bridge.calls
    assert bridge.calls[-1][0] > 0
    assert bridge.calls[-1][-2:] == (760, 360)

    window.set_window_mode(DesktopWindowMode.NORMAL)
    qt_app.processEvents()
    assert not window.overlay_locked
    assert not window._native_overlay_active
    assert window.isVisible()
    assert host.isHidden()
    assert window.centralWidget() is workspace
    window.close()


def test_native_configuration_failure_falls_back_to_portable_clickthrough(
    qt_app: QApplication,
) -> None:
    bridge = _FakeNativeBridge(succeeds=False)
    window = MainWindow(overlay_recovery_available=True)
    window._wayland_overlay_backend = _native_backend(bridge)
    window.show()
    window.set_window_mode(DesktopWindowMode.OVERLAY)
    workspace = window.centralWidget()

    assert window.set_overlay_locked(True)
    qt_app.processEvents()
    assert not window._native_overlay_active
    assert window.centralWidget() is workspace
    assert window.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert bridge.calls

    assert window.set_overlay_locked(False)
    window.set_window_mode(DesktopWindowMode.NORMAL)
    window.close()
