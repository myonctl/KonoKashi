"""Desktop application entry point and Qt composition boundary."""

from __future__ import annotations

import signal
import sys
from collections.abc import Callable, Sequence
from importlib.resources import files
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from konokashi import APPLICATION_ID
from konokashi.presentation.desktop.coordinator import DesktopCoordinator
from konokashi.presentation.desktop.main_window import MainWindow


class DesktopLifecycle(Protocol):
    """Minimal lifecycle owned by the desktop entry point."""

    def start(self) -> None:
        """Start the desktop application services."""


DesktopCoordinatorFactory = Callable[
    [QApplication, MainWindow, Path | None, Path | None, str | None, int],
    DesktopLifecycle,
]


def _application_icon() -> QIcon:
    return QIcon(str(files("konokashi").joinpath("resources", f"{APPLICATION_ID}.svg")))


def _create_coordinator(
    application: QApplication,
    window: MainWindow,
    database_path: Path | None,
    config_path: Path | None,
    player_override: str | None,
    lyrics_offset_us: int,
) -> DesktopLifecycle:
    return DesktopCoordinator(
        application,
        window,
        database_path=database_path,
        config_path=config_path,
        player_override=player_override,
        lyrics_offset_us=lyrics_offset_us,
    )


def run_desktop(
    argv: Sequence[str] | None = None,
    *,
    database_path: Path | None = None,
    config_path: Path | None = None,
    player_override: str | None = None,
    lyrics_offset_us: int = 0,
    coordinator_factory: DesktopCoordinatorFactory = _create_coordinator,
) -> int:
    """Launch the normal resizable Desktop MVP and return its Qt exit code."""

    existing = QCoreApplication.instance()
    if existing is not None and not isinstance(existing, QApplication):
        raise RuntimeError("desktop launch requires a QApplication")
    application = (
        existing
        if isinstance(existing, QApplication)
        else QApplication(list(argv) if argv is not None else sys.argv)
    )
    application.setApplicationName("KonoKashi")
    application.setOrganizationName("KonoKashi")
    application.setDesktopFileName(APPLICATION_ID)
    application.setWindowIcon(_application_icon())
    window = MainWindow()
    window.show()
    try:
        coordinator = coordinator_factory(
            application,
            window,
            database_path,
            config_path,
            player_override,
            lyrics_offset_us,
        )
        coordinator.start()
    except (ImportError, RuntimeError) as error:
        from konokashi.application.desktop_state import DesktopStateController

        controller = DesktopStateController()
        window.render_state(
            controller.application_error(
                "KonoKashi could not connect to desktop media services.",
                (str(error),),
            )
        )
    previous_sigint = None
    try:
        previous_sigint = signal.getsignal(signal.SIGINT)

        def stop_from_terminal(_signum: int, _frame: object) -> None:
            application.exit(130)

        signal.signal(signal.SIGINT, stop_from_terminal)
    except ValueError:
        # Embedders may run the Qt application outside Python's main thread.
        previous_sigint = None
    try:
        return application.exec()
    finally:
        if previous_sigint is not None:
            signal.signal(signal.SIGINT, previous_sigint)
