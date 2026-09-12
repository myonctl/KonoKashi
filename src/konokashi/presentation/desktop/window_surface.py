"""Desktop window modes over one independently painted lyric surface."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QScreen,
)
from PySide6.QtWidgets import QMainWindow, QWidget


class DesktopWindowMode(Enum):
    """User-facing projections of the same desktop workspace and renderer."""

    NORMAL = "normal"
    COMPACT = "compact"
    OVERLAY = "overlay"
    FULLSCREEN = "fullscreen"


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

        if QGuiApplication.platformName().startswith("wayland"):
            return (
                "Portable Qt top-most fallback; this compositor may place "
                "fullscreen windows above the lyrics"
            )
        return "Portable Qt top-most overlay"

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
        was_visible = self.isVisible()
        if previous is not DesktopWindowMode.FULLSCREEN:
            self._mode_geometries[previous] = QRect(self.geometry())
        if self._overlay_locked:
            self._set_overlay_input_transparency(False, was_visible=was_visible)
            self._overlay_locked = False
            self.overlay_lock_changed.emit(False)

        if mode is DesktopWindowMode.FULLSCREEN:
            self._mode_before_fullscreen = previous
            self.setWindowFlags(self._decorated_flags)
            self._window_mode = mode
            if was_visible:
                self.showFullScreen()
        else:
            flags = self._decorated_flags
            if mode is DesktopWindowMode.OVERLAY:
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
        self._set_overlay_input_transparency(locked, was_visible=self.isVisible())
        self._overlay_locked = locked
        self.overlay_lock_changed.emit(locked)
        return True

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
        current = self.screen()
        screens = QGuiApplication.screens()
        return screens.index(current) if current in screens else 0

    def move_to_screen(self, index: int) -> bool:
        """Target a display and center where the window system permits placement."""

        screens = QGuiApplication.screens()
        if not 0 <= index < len(screens):
            return False
        screen: QScreen = screens[index]
        handle = self.windowHandle()
        if handle is None:
            self.winId()
            handle = self.windowHandle()
        if handle is None:
            return False
        handle.setScreen(screen)
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
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        color = QColor(self._background_color)
        wash = (
            None
            if self._background_wash_color is None
            else QColor(self._background_wash_color)
        )
        if not self.background_transparency_available:
            color.setAlpha(255)
            if wash is not None:
                wash.setAlpha(255)
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        if wash is None:
            painter.fillRect(event.rect(), color)
            return
        gradient = QLinearGradient(event.rect().topLeft(), event.rect().bottomRight())
        gradient.setColorAt(0.0, wash)
        gradient.setColorAt(0.62, color)
        gradient.setColorAt(1.0, color)
        painter.fillRect(event.rect(), gradient)
