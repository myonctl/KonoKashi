"""Focused synchronized lyrics Textual frontend regressions."""

from __future__ import annotations

from dataclasses import replace
from threading import Event

from textual.pilot import Pilot
from textual.widgets import Static

from konokashi.presentation.tui.lyrics_app import LyricsApp
from tests.test_desktop_state import _snapshot, _track
from tests.test_settings_tui import _run_app, _wait_until


def test_tui_projects_shared_snapshot_and_treats_lyrics_as_plain_text() -> None:
    base = _snapshot(_track("xa4WrgqI7q0", "Synthetic track"), 1)
    snapshot = replace(
        base,
        active=(replace(base.active[0], original="[bold]literal[/bold]"),),
    )

    class SnapshotApp(LyricsApp):
        def on_mount(self) -> None:
            self._ui_ready = True
            self._render_snapshot(snapshot)

    async def scenario(app: LyricsApp, pilot: Pilot[int]) -> None:
        assert "Synthetic track" in str(app.query_one("#track", Static).render())
        assert "literal" in str(app.query_one("#active", Static).render())
        assert "[bold]" in str(app.query_one("#active", Static).render())
        assert "fixture" in str(app.query_one("#state", Static).render())
        await pilot.press("q")

    _run_app(  # type: ignore[arg-type]
        SnapshotApp(lambda _consumer, _stopped: 0), scenario, size=(90, 28)
    )


def test_tui_worker_stops_cleanly_when_user_quits() -> None:
    snapshot = _snapshot(_track("xa4WrgqI7q0", "Worker track"), 1)
    pause = Event()
    stopped_cleanly = Event()

    def stream(consumer, stopped):  # type: ignore[no-untyped-def]
        consumer(snapshot)
        while not stopped():
            pause.wait(0.01)
        stopped_cleanly.set()
        return 0

    async def scenario(app: LyricsApp, pilot: Pilot[int]) -> None:
        await _wait_until(
            lambda: "Worker track" in str(app.query_one("#track", Static).render())
        )
        await pilot.press("q")

    _run_app(  # type: ignore[arg-type]
        LyricsApp(stream), scenario, size=(80, 24)
    )
    assert stopped_cleanly.wait(1)
