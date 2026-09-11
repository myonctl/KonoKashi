"""Native Qt application commands and canonical appearance action projection."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QApplication, QMenuBar, QWidget

from konokashi.application.appearance import AppearancePreset, AppearanceProfile
from konokashi.presentation.desktop.window_surface import DesktopWindowMode


class ApplicationMenu(QMenuBar):
    """Menu actions request canonical writes; checked state follows the profile."""

    setting_requested = Signal(str, object)
    about_requested = Signal()
    window_mode_requested = Signal(object)
    overlay_lock_requested = Signal(bool)
    screen_requested = Signal(int)

    def __init__(
        self,
        settings: QAction,
        review: QAction,
        details: QAction,
        scan: QAction,
        quit_action: QAction,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        # Navigation stays readable even when the lyric background is transparent.
        self.setPalette(QApplication.palette())
        file_menu = self.addMenu("&File")
        file_menu.addAction(settings)
        file_menu.addAction(scan)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)
        view_menu = self.addMenu("&View")
        modes = view_menu.addMenu("Window &mode")
        self.mode_group = QActionGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_actions: dict[DesktopWindowMode, QAction] = {}
        for mode, label, shortcut in (
            (DesktopWindowMode.NORMAL, "&Normal window", "Ctrl+Alt+1"),
            (DesktopWindowMode.COMPACT, "&Compact companion", "Ctrl+Alt+2"),
            (DesktopWindowMode.OVERLAY, "Desktop &overlay", "Ctrl+Alt+3"),
            (DesktopWindowMode.FULLSCREEN, "&Fullscreen lyrics", "F11"),
        ):
            action = modes.addAction(label)
            action.setCheckable(True)
            action.setShortcut(QKeySequence(shortcut))
            self.mode_group.addAction(action)
            action.triggered.connect(
                lambda _checked, mode=mode: self.window_mode_requested.emit(mode)
            )
            self.mode_actions[mode] = action
        self.overlay_lock_action = view_menu.addAction("&Click-through overlay")
        self.overlay_lock_action.setCheckable(True)
        self.overlay_lock_action.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self.overlay_lock_action.triggered.connect(self.overlay_lock_requested)
        self.screen_menu = view_menu.addMenu("Move to &screen")
        self.screen_group = QActionGroup(self)
        self.screen_group.setExclusive(True)
        view_menu.addSeparator()
        self.visibility_actions: dict[str, QAction] = {}
        for field, label in (
            ("title", "Track &title"),
            ("artist", "&Artist"),
            ("album", "A&lbum"),
            ("progress", "&Progress bar"),
            ("timestamps", "Time&stamps"),
            ("playback_status", "Playback stat&us"),
            ("source", "Lyrics s&ource"),
            ("chrome", "&Window controls"),
        ):
            action = view_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(
                lambda checked, field=field: self.setting_requested.emit(
                    f"appearance.visibility.{field}", checked
                )
            )
            self.visibility_actions[field] = action
        presets = view_menu.addMenu("Appearance &preset")
        self.preset_group = QActionGroup(self)
        self.preset_actions: dict[AppearancePreset, QAction] = {}
        for preset in AppearancePreset:
            action = presets.addAction(preset.value.replace("-", " ").title())
            action.setCheckable(True)
            self.preset_group.addAction(action)
            action.triggered.connect(
                lambda _checked, preset=preset: self.setting_requested.emit(
                    "appearance.preset", preset.value
                )
            )
            self.preset_actions[preset] = action
        lyrics_menu = self.addMenu("&Lyrics")
        lyrics_menu.addAction(review)
        help_menu = self.addMenu("&Help")
        help_menu.addAction(details)
        about = help_menu.addAction("&About KonoKashi")
        about.triggered.connect(self.about_requested)
        settings.setShortcut(QKeySequence("Ctrl+,"))
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))

    def project_window_state(
        self,
        mode: DesktopWindowMode,
        *,
        overlay_locked: bool,
        recovery_available: bool,
        screen_names: tuple[str, ...],
        current_screen: int,
        backend_description: str,
    ) -> None:
        """Project transient native state without creating a settings source."""

        for candidate, action in self.mode_actions.items():
            with QSignalBlocker(action):
                action.setChecked(candidate is mode)
        with QSignalBlocker(self.overlay_lock_action):
            self.overlay_lock_action.setChecked(overlay_locked)
        self.overlay_lock_action.setEnabled(
            mode is DesktopWindowMode.OVERLAY and recovery_available
        )
        self.overlay_lock_action.setToolTip(
            "Pass pointer input through the overlay; use the system tray to unlock"
            if recovery_available
            else "Click-through is unavailable without a system-tray escape route"
        )
        self.mode_actions[DesktopWindowMode.OVERLAY].setToolTip(backend_description)
        self._project_screens(screen_names, current_screen)

    def _project_screens(
        self, screen_names: tuple[str, ...], current_screen: int
    ) -> None:
        for action in self.screen_group.actions():
            self.screen_group.removeAction(action)
        self.screen_menu.clear()
        for index, name in enumerate(screen_names):
            action = self.screen_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(index == current_screen)
            action.triggered.connect(
                lambda _checked, index=index: self.screen_requested.emit(index)
            )
            self.screen_group.addAction(action)
        self.screen_menu.setEnabled(bool(screen_names))

    def project(self, profile: AppearanceProfile) -> None:
        for field, action in self.visibility_actions.items():
            with QSignalBlocker(action):
                action.setChecked(bool(getattr(profile.visibility, field)))
        for preset, action in self.preset_actions.items():
            with QSignalBlocker(action):
                action.setChecked(preset is profile.preset)
