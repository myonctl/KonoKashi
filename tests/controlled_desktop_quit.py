"""Subprocess probe proving a non-cooperative worker cannot hold GUI shutdown."""

from __future__ import annotations

from threading import Event

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from konokashi.presentation.desktop.coordinator import DesktopCoordinator
from konokashi.presentation.desktop.main_window import MainWindow
from tests.test_desktop_coordinator import _Runtime


def main() -> int:
    application = QApplication.instance() or QApplication(["quit-probe"])
    assert isinstance(application, QApplication)
    window = MainWindow()
    coordinator = DesktopCoordinator(
        application,
        window,
        runtime=_Runtime(),  # type: ignore[arg-type]
    )
    started = Event()
    blocked = Event()

    def non_cooperative_job() -> None:
        started.set()
        blocked.wait(30)

    coordinator._start_job(non_cooperative_job, lambda *_arguments: None)
    if not started.wait(2):
        return 2
    QTimer.singleShot(0, coordinator.close)
    QTimer.singleShot(0, application.quit)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
