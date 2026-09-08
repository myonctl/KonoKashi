"""Native Qt application commands and canonical appearance action projection."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QApplication, QMenuBar, QWidget

from konokashi.application.appearance import AppearancePreset, AppearanceProfile


class ApplicationMenu(QMenuBar):
    """Menu actions request canonical writes; checked state follows the profile."""

    setting_requested = Signal(str, object)
    about_requested = Signal()

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

    def project(self, profile: AppearanceProfile) -> None:
        for field, action in self.visibility_actions.items():
            with QSignalBlocker(action):
                action.setChecked(bool(getattr(profile.visibility, field)))
        for preset, action in self.preset_actions.items():
            with QSignalBlocker(action):
                action.setChecked(preset is profile.preset)
