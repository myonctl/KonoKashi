"""Focused Textual frontend for the shared synchronized-lyrics snapshot."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event
from typing import ClassVar, TypeAlias

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.widgets import Footer, Header, Static

from konokashi.application.sync_events import active_timing_segment
from konokashi.application.sync_state import SynchronizationSnapshot

SnapshotConsumer: TypeAlias = Callable[[SynchronizationSnapshot], None]
StopPredicate: TypeAlias = Callable[[], bool]
LyricsStreamRunner: TypeAlias = Callable[[SnapshotConsumer, StopPredicate], int]


class LyricsApp(App[int]):
    """Minimal synchronized lyrics TUI without a second timing engine."""

    TITLE = "KonoKashi Lyrics"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit_lyrics", "Quit"),
        Binding("escape", "quit_lyrics", "Quit", show=False),
    ]
    CSS = """
    Screen {
        align: center middle;
        background: $surface;
        padding: 1 3;
    }
    #track {
        height: auto;
        text-align: center;
        text-style: bold;
        color: $text;
        margin-bottom: 1;
    }
    #state {
        height: auto;
        text-align: center;
        color: $text-muted;
        margin-bottom: 2;
    }
    #previous, #next {
        height: 1fr;
        content-align: center bottom;
        text-align: center;
        color: $text-muted;
    }
    #next {
        content-align: center top;
    }
    #active {
        height: auto;
        min-height: 3;
        content-align: center middle;
        text-align: center;
        color: $accent;
        text-style: bold;
        padding: 1;
        border: round $accent;
    }
    #reading, #translation {
        height: auto;
        min-height: 1;
        text-align: center;
        color: $text-muted;
    }
    #diagnostic {
        height: auto;
        text-align: center;
        color: $warning;
        margin-top: 1;
    }
    """

    def __init__(self, stream_runner: LyricsStreamRunner) -> None:
        super().__init__()
        self._stream_runner = stream_runner
        self._stop = Event()
        self._closed = False
        self._ui_ready = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(id="track")
        yield Static("Waiting for synchronized lyrics…", id="state")
        yield Static(id="previous")
        yield Static(id="active")
        yield Static(id="reading")
        yield Static(id="translation")
        yield Static(id="next")
        yield Static(id="diagnostic")
        yield Footer()

    def on_mount(self) -> None:
        self._ui_ready = True
        self.run_worker(
            self._run_stream,
            name="synchronized lyrics stream",
            group="lyrics-stream",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def on_unmount(self) -> None:
        self._closed = True
        self._ui_ready = False
        self._stop.set()

    def _run_stream(self) -> None:
        def deliver(snapshot: SynchronizationSnapshot) -> None:
            if not self._closed:
                self.call_from_thread(self._render_snapshot, snapshot)

        exit_code = self._stream_runner(
            deliver,
            self._stop.is_set,
        )
        if not self._closed:
            self.call_from_thread(self.exit, exit_code)

    def action_quit_lyrics(self) -> None:
        self._stop.set()
        self.exit(0)

    def _render_snapshot(self, snapshot: SynchronizationSnapshot) -> None:
        artists = " · ".join(snapshot.artists) or "Unknown artist"
        title = snapshot.track_title or "Unknown track"
        self.query_one("#track", Static).update(Text(f"{title}\n{artists}"))
        position = _time_text(snapshot.disciplined_player_position_us)
        duration = _time_text(snapshot.duration_us)
        self.query_one("#state", Static).update(
            Text(
                f"{snapshot.playback_status.value} · {position} / {duration} · "
                f"{snapshot.lyrics_source} ({snapshot.lyrics_provenance}, "
                f"{snapshot.lyrics_timing_level})"
            )
        )
        self.query_one("#previous", Static).update(
            Text("\n".join(line.original for line in snapshot.previous[-3:]))
        )
        active = self.query_one("#active", Static)
        active.update(_active_text(snapshot))
        first = snapshot.active[0] if snapshot.active else None
        self.query_one("#reading", Static).update(
            Text("" if first is None or first.reading is None else first.reading.text)
        )
        self.query_one("#translation", Static).update(
            Text(
                ""
                if first is None or first.translated is None
                else first.translated.text
            )
        )
        self.query_one("#next", Static).update(
            Text("\n".join(line.original for line in snapshot.next[:3]))
        )
        self.query_one("#diagnostic", Static).update(
            Text(" · ".join(snapshot.diagnostics[:2]))
        )


def _active_text(snapshot: SynchronizationSnapshot) -> Text:
    if not snapshot.active:
        return Text("Waiting for the first timed line…")
    active_segment = active_timing_segment(snapshot)
    if len(snapshot.active) != 1 or not snapshot.active[0].timing_segments:
        return Text("\n".join(line.original for line in snapshot.active))
    selected_id = None if active_segment is None else active_segment[1].segment_id
    rendered = Text()
    for segment in snapshot.active[0].timing_segments:
        rendered.append(
            segment.text,
            style="bold reverse" if segment.segment_id == selected_id else None,
        )
    return rendered


def _time_text(value_us: int | None) -> str:
    if value_us is None:
        return "--:--"
    minutes, remainder = divmod(max(0, value_us), 60_000_000)
    seconds = remainder // 1_000_000
    return f"{minutes:02d}:{seconds:02d}"


def run_lyrics_tui(stream_runner: LyricsStreamRunner) -> int:
    """Run the focused terminal frontend and return its controlled exit code."""

    result = LyricsApp(stream_runner).run()
    return 0 if result is None else result
