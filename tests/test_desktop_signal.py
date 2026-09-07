"""Subprocess regression for terminal interruption of the Qt desktop."""

from __future__ import annotations

import os
import subprocess
import sys


def test_desktop_sigint_exits_130_without_traceback() -> None:
    script = r"""
import os
import signal
from PySide6.QtCore import QTimer
from konokashi.presentation.desktop.app import run_desktop

class Lifecycle:
    def start(self):
        QTimer.singleShot(20, lambda: os.kill(os.getpid(), signal.SIGINT))

def factory(application, window, database_path):
    return Lifecycle()

raise SystemExit(run_desktop(["konokashi"], coordinator_factory=factory))
"""
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        env=environment,
    )

    assert result.returncode == 130
    assert "Traceback" not in result.stderr
