"""Desktop window modes over one independently painted lyric surface."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QScreen,
)
from PySide6.QtWidgets import QMainWindow, QWidget

if TYPE_CHECKING:
    from konokashi.presentation.desktop.wayland_overlay import WaylandOverlayBackend


class DesktopWindowMode(Enum):
    """User-facing projections of the same desktop workspace and renderer."""

    NORMAL = "normal"
    COMPACT = "compact"
    OVERLAY = "overlay"
    FULLSCREEN = "fullscreen"


def _paint_background(
    window: QWidget,
    event: QPaintEvent,
    color_value: QColor,
    wash_value: QColor | None,
    *,
    transparency_available: bool,
) -> None:
    color = QColor(color_value)
    wash = None if wash_value is None else QColor(wash_value)
    if not transparency_available:
        color.setAlpha(255)
        if wash is not None:
            wash.setAlpha(255)
    painter = QPainter(window)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    if wash is None:
        painter.fillRect(event.rect(), color)
        return
    gradient = QLinearGradient(event.rect().topLeft(), event.rect().bottomRight())
    gradient.setColorAt(0.0, wash)
    gradient.setColorAt(0.62, color)
    gradient.setColorAt(1.0, color)
    painter.fillRect(event.rect(), gradient)


class _LockedOverlayHost(QMainWindow):
    """Dedicated role-safe surface used only for passive native layer-shell."""

    dismissed = Signal()

    def __init__(self, *, alpha_requested: bool) -> None:
        super().__init__()
        self._alpha_requested = alpha_requested
        self._background_color = QColor("#202124")
        self._background_wash_color: QColor | None = None
        self._allow_close = False
        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, self._alpha_requested
        )
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )

    def set_background_color(
        self, color: QColor, wash_color: QColor | None = None
    ) -> None:
        self._background_color = QColor(color)
        self._background_wash_color = None if wash_color is None else QColor(wash_color)
        self.update()

    @property
    def background_transparency_available(self) -> bool:
        if not self._alpha_requested:
            return False
        handle = self.windowHandle()
        return handle is None or handle.format().alphaBufferSize() > 0

    def paintEvent(self, event: QPaintEvent) -> None:
        _paint_background(
            self,
            event,
            self._background_color,
            self._background_wash_color,
            transparency_available=self.background_transparency_available,
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            super().closeEvent(event)
            return
        event.ignore()
        self.dismissed.emit()

    def shutdown(self) -> None:
        self._allow_close = True
        self.close()


class DesktopWindowSurface(QMainWindow):
    """Own native window mechanics without owning lyric or playback semantics."""

    window_mode_changed = Signal(object)
    overlay_lock_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        platform = QGuiApplication.platformName()
        self._alpha_requested = platform in {
            "wayland",
            "wayland-egl",
            "xcb",
            "offscreen",
        }
        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, self._alpha_requested
        )
        self._background_color = QColor("#202124")
        self._background_wash_color: QColor | None = None
        self._decorated_flags = self.windowFlags()
        self._window_mode = DesktopWindowMode.NORMAL
        self._mode_before_fullscreen = DesktopWindowMode.NORMAL
        self._overlay_locked = False
        self._overlay_recovery_available = False
        self._mode_geometries: dict[DesktopWindowMode, QRect] = {}
        self._wayland_overlay_backend: WaylandOverlayBackend | None = None
        self._locked_overlay_host: _LockedOverlayHost | None = None
        self._native_overlay_active = False
        self._native_overlay_geometry = QRect()

    @property
    def window_mode(self) -> DesktopWindowMode:
        return self._window_mode

    @property
    def overlay_locked(self) -> bool:
        return self._overlay_locked

    @property
    def overlay_recovery_available(self) -> bool:
        return self._overlay_recovery_available

    @property
    def overlay_backend_description(self) -> str:
        """Describe the honest capability of the currently shipped backend."""

        if self._wayland_overlay_backend is not None:
            return self._wayland_overlay_backend.capabilities.description
        if QGuiApplication.platformName().startswith("wayland"):
            return (
                "Portable Qt while unlocked; native layer-shell is used when "
                "available after click-through is enabled"
            )
        return "Portable Qt top-most floating lyrics window"

    def _discover_wayland_overlay_backend(self) -> None:
        if self._wayland_overlay_backend is not None:
            return
        from konokashi.presentation.desktop.wayland_overlay import (
            discover_wayland_overlay_backend,
        )

        self._wayland_overlay_backend = discover_wayland_overlay_backend()

    def set_overlay_recovery_available(self, available: bool) -> None:
        """Allow click-through only while an out-of-window escape route exists."""

        self._overlay_recovery_available = available
        if not available and self._overlay_locked:
            self.set_overlay_locked(False)

    def set_window_mode(self, mode: DesktopWindowMode) -> None:
        """Project a new native surface mode while retaining the same widgets."""

        if mode is self._window_mode:
            return
        previous = self._window_mode
        was_visible = self._surface_is_visible()
        if previous is not DesktopWindowMode.FULLSCREEN:
            self._mode_geometries[previous] = (
                QRect(self._native_overlay_geometry)
                if self._native_overlay_active
                else QRect(self.geometry())
            )
        if self._overlay_locked:
            self.set_overlay_locked(False)

        if mode is DesktopWindowMode.FULLSCREEN:
            self._mode_before_fullscreen = previous
            self.setWindowFlags(self._decorated_flags)
            self._window_mode = mode
            if was_visible:
                self.showFullScreen()
        else:
            flags = self._decorated_flags
            if mode is DesktopWindowMode.OVERLAY:
                self._discover_wayland_overlay_backend()
                flags |= (
                    Qt.WindowType.FramelessWindowHint
                    | Qt.WindowType.WindowStaysOnTopHint
                )
            self.setWindowFlags(flags)
            self._window_mode = mode
            if was_visible:
                self.showNormal()
            geometry = self._mode_geometries.get(mode)
            if geometry is not None:
                self.setGeometry(geometry)
            elif mode is DesktopWindowMode.COMPACT:
                self.resize(560, 420)
            elif mode is DesktopWindowMode.OVERLAY:
                self.resize(760, 360)
        self.window_mode_changed.emit(mode)

    def _surface_is_visible(self) -> bool:
        if self._native_overlay_active and self._locked_overlay_host is not None:
            return self._locked_overlay_host.isVisible()
        return self.isVisible()

    def restore_from_transient_mode(self) -> None:
        """Return from fullscreen/overlay using a predictable, interactive mode."""

        if self._window_mode is DesktopWindowMode.FULLSCREEN:
            target = self._mode_before_fullscreen
            if target is DesktopWindowMode.OVERLAY:
                target = DesktopWindowMode.NORMAL
            self.set_window_mode(target)
        elif self._window_mode is DesktopWindowMode.OVERLAY:
            self.set_window_mode(DesktopWindowMode.NORMAL)

    def set_overlay_locked(self, locked: bool) -> bool:
        """Toggle click-through, refusing states with no guaranteed recovery."""

        if self._window_mode is not DesktopWindowMode.OVERLAY:
            return False
        if locked and not self._overlay_recovery_available:
            return False
        if locked == self._overlay_locked:
            return True
        was_visible = self._surface_is_visible()
        if locked and self._activate_native_locked_overlay(was_visible=was_visible):
            pass
        elif not locked and self._native_overlay_active:
            self._deactivate_native_locked_overlay(was_visible=was_visible)
        else:
            self._set_overlay_input_transparency(locked, was_visible=was_visible)
        self._overlay_locked = locked
        self.overlay_lock_changed.emit(locked)
        return True

    def _activate_native_locked_overlay(self, *, was_visible: bool) -> bool:
        self._discover_wayland_overlay_backend()
        backend = self._wayland_overlay_backend
        if backend is None or not backend.capabilities.native_layer_shell:
            return False
        host = self._locked_overlay_host
        if host is None:
            host = _LockedOverlayHost(alpha_requested=self._alpha_requested)
            host.dismissed.connect(lambda: self.set_overlay_locked(False))
            host.setWindowIcon(self.windowIcon())
            host.setWindowTitle("KonoKashi — Locked floating lyrics")
            host.set_background_color(
                self._background_color, self._background_wash_color
            )
            self._locked_overlay_host = host
        geometry = QRect(self.geometry())
        host.setGeometry(geometry)
        host.winId()
        handle = host.windowHandle()
        if handle is None:
            return False
        current_screen = self.screen()
        if current_screen is not None:
            handle.setScreen(current_screen)
        if not backend.configure_locked_overlay(handle, geometry):
            return False
        content = self.takeCentralWidget()
        if content is None:
            return False
        host.setCentralWidget(content)
        self._native_overlay_geometry = geometry
        self._native_overlay_active = True
        self.hide()
        if was_visible:
            host.show()
        return True

    def _deactivate_native_locked_overlay(self, *, was_visible: bool) -> None:
        host = self._locked_overlay_host
        if host is None:
            self._native_overlay_active = False
            return
        content = host.takeCentralWidget()
        host.hide()
        if content is not None:
            self.setCentralWidget(content)
        self.setGeometry(self._native_overlay_geometry)
        self._native_overlay_active = False
        if was_visible:
            self.show()

    def _set_overlay_input_transparency(
        self, locked: bool, *, was_visible: bool
    ) -> None:
        geometry = QRect(self.geometry())
        flags = self.windowFlags()
        for flag in (
            Qt.WindowType.WindowTransparentForInput,
            Qt.WindowType.WindowDoesNotAcceptFocus,
        ):
            if locked:
                flags |= flag
            else:
                flags &= ~flag
        self.setWindowFlags(flags)
        self.setGeometry(geometry)
        if was_visible:
            self.show()

    def start_overlay_move(self) -> bool:
        """Ask the compositor to move an unlocked overlay interactively."""

        if self._window_mode is not DesktopWindowMode.OVERLAY or self._overlay_locked:
            return False
        handle = self.windowHandle()
        return False if handle is None else handle.startSystemMove()

    @property
    def screen_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for index, screen in enumerate(QGuiApplication.screens(), start=1):
            names.append(screen.name() or f"Display {index}")
        return tuple(names)

    @property
    def current_screen_index(self) -> int:
        current = (
            self._locked_overlay_host.screen()
            if self._native_overlay_active and self._locked_overlay_host is not None
            else self.screen()
        )
        screens = QGuiApplication.screens()
        return screens.index(current) if current in screens else 0

    def move_to_screen(self, index: int) -> bool:
        """Target a display and center where the window system permits placement."""

        screens = QGuiApplication.screens()
        if not 0 <= index < len(screens):
            return False
        screen: QScreen = screens[index]
        surface: QMainWindow = (
            self._locked_overlay_host
            if self._native_overlay_active and self._locked_overlay_host is not None
            else self
        )
        handle = surface.windowHandle()
        if handle is None:
            surface.winId()
            handle = surface.windowHandle()
        if handle is None:
            return False
        handle.setScreen(screen)
        if self._native_overlay_active and self._wayland_overlay_backend is not None:
            available = screen.availableGeometry()
            geometry = QRect(self._native_overlay_geometry)
            geometry.moveCenter(available.center())
            surface.setGeometry(geometry)
            if not self._wayland_overlay_backend.configure_locked_overlay(
                handle, geometry
            ):
                return False
            self._native_overlay_geometry = geometry
            return True
        if not QGuiApplication.platformName().startswith("wayland"):
            available = screen.availableGeometry()
            frame = self.frameGeometry()
            frame.moveCenter(available.center())
            self.move(frame.topLeft())
        return True

    @property
    def background_transparency_available(self) -> bool:
        if not self._alpha_requested:
            return False
        handle = self.windowHandle()
        return handle is None or handle.format().alphaBufferSize() > 0

    def set_background_color(
        self, color: QColor, wash_color: QColor | None = None
    ) -> None:
        self._background_color = QColor(color)
        self._background_wash_color = None if wash_color is None else QColor(wash_color)
        if self._locked_overlay_host is not None:
            self._locked_overlay_host.set_background_color(color, wash_color)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        _paint_background(
            self,
            event,
            self._background_color,
            self._background_wash_color,
            transparency_available=self.background_transparency_available,
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._locked_overlay_host is not None:
            self._locked_overlay_host.shutdown()
        super().closeEvent(event)
