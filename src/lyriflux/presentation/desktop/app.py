"""Desktop application entry point and Qt composition boundary."""

from __future__ import annotations

import signal
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from lyriflux import APPLICATION_ID
from lyriflux.presentation.desktop.coordinator import DesktopCoordinator
from lyriflux.presentation.desktop.main_window import MainWindow


class DesktopLifecycle(Protocol):
    """Minimal lifecycle owned by the desktop entry point."""

    def start(self) -> None:
        """Start the desktop application services."""


DesktopCoordinatorFactory = Callable[
    [QApplication, MainWindow, Path | None], DesktopLifecycle
]


def _create_coordinator(
    application: QApplication,
    window: MainWindow,
    database_path: Path | None,
) -> DesktopLifecycle:
    return DesktopCoordinator(application, window, database_path=database_path)


def run_desktop(
    argv: Sequence[str] | None = None,
    *,
    database_path: Path | None = None,
    config_path: Path | None = None,
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
    application.setApplicationName("LyriFlux")
    application.setOrganizationName("LyriFlux")
    application.setDesktopFileName(APPLICATION_ID)
    window = MainWindow()
    window.show()
    try:
        coordinator: DesktopLifecycle
        if config_path is not None and coordinator_factory is _create_coordinator:
            coordinator = DesktopCoordinator(
                application,
                window,
                database_path=database_path,
                config_path=config_path,
            )
        else:
            coordinator = coordinator_factory(application, window, database_path)
        coordinator.start()
    except (ImportError, RuntimeError) as error:
        from lyriflux.application.desktop_state import DesktopStateController

        controller = DesktopStateController()
        window.render_state(
            controller.application_error(
                "LyriFlux could not connect to desktop media services.",
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
