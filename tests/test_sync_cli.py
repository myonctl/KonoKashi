"""Stage 6 terminal synchronization diagnostics without live D-Bus or audio."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pytest

from konokashi import cli
from konokashi.application.ports import PlayerDiscoveryPort
from konokashi.domain.models import (
    PlayerEvent,
    PlayerEventKind,
    PlayerInspection,
    PlayerListResult,
    PlayerSnapshot,
)
from konokashi.domain.synchronization import (
    AudioLatencyProbeResult,
    AudioLatencyProbeStatus,
    ObservationReason,
    PlaybackState,
    PositionObservation,
)
from tests.test_lyrics_cli import _provider_result, _runtime


class FakeTiming:
    def __init__(self, positions_us: tuple[int, ...]) -> None:
        self._positions = iter(positions_us)
        self.reasons: list[ObservationReason] = []

    def sample(
        self,
        _snapshot: object,
        session_id: str,
        *,
        reason: ObservationReason = ObservationReason.PERIODIC,
    ) -> PositionObservation:
        self.reasons.append(reason)
        now_ns = time.monotonic_ns()
        return PositionObservation(
            session_id,
            next(self._positions),
            PlaybackState.PLAYING,
            1.0,
            now_ns,
            now_ns,
            reason,
        )


class FailingSecondTiming(FakeTiming):
    def sample(
        self,
        _snapshot: object,
        session_id: str,
        *,
        reason: ObservationReason = ObservationReason.PERIODIC,
    ) -> PositionObservation:
        if self.reasons:
            self.reasons.append(reason)
            raise RuntimeError("controlled Position timeout")
        return super().sample(_snapshot, session_id, reason=reason)


class SnapshotStateTiming(FakeTiming):
    def sample(
        self,
        snapshot: PlayerSnapshot,
        session_id: str,
        *,
        reason: ObservationReason = ObservationReason.PERIODIC,
    ) -> PositionObservation:
        self.reasons.append(reason)
        now_ns = time.monotonic_ns()
        return PositionObservation(
            session_id,
            next(self._positions),
            PlaybackState.from_mpris(snapshot.playback_status),
            snapshot.rate or 1.0,
            now_ns,
            now_ns,
            reason,
        )


class SequencedClient:
    def __init__(self, inspections: tuple[PlayerInspection, ...]) -> None:
        self.inspections = inspections
        self.calls = 0

    def list_players(self) -> PlayerListResult:
        inspection = self.inspections[min(self.calls, len(self.inspections) - 1)]
        self.calls += 1
        return PlayerListResult((inspection,))

    def inspect_player(self, _service_name: str) -> PlayerInspection:
        return self.inspections[min(self.calls, len(self.inspections) - 1)]


class SyncRuntime:
    def __init__(
        self,
        positions_us: tuple[int, ...],
        *,
        seek_during_wait: bool = False,
    ) -> None:
        base = _runtime()
        self.client: PlayerDiscoveryPort = base.client
        self.monitor = base.monitor
        self.timing = FakeTiming(positions_us)
        self.clock = _TestClock()
        self.seek_during_wait = seek_during_wait
        self.waits: list[int] = []
        self.wakes = 0

    def exec(self) -> int:
        return 0

    def quit(self) -> None:
        pass

    def wake(self) -> None:
        self.wakes += 1

    def wait(self, timeout_ms: int) -> None:
        self.waits.append(timeout_ms)
        self.clock.advance(timeout_ms)
        if self.seek_during_wait and self.monitor.handler is not None:
            self.monitor.handler(
                PlayerEvent(
                    PlayerEventKind.SEEKED,
                    "plasma-browser-integration",
                    position_us=4_200_000,
                )
            )
            self.seek_during_wait = False


class _TestClock:
    def __init__(self) -> None:
        self.now_ns = time.monotonic_ns()

    def monotonic_ns(self) -> int:
        return self.now_ns

    def boottime_ns(self) -> int | None:
        return None

    def advance(self, milliseconds: int) -> None:
        self.now_ns += milliseconds * 1_000_000


class InterruptingRuntime(SyncRuntime):
    def wait(self, timeout_ms: int) -> None:
        super().wait(timeout_ms)
        raise KeyboardInterrupt


class FakeLatencyProbe:
    def probe(self) -> AudioLatencyProbeResult:
        return AudioLatencyProbeResult(
            AudioLatencyProbeStatus.REPORTED,
            "Test sink",
            10_000,
            20_000,
            5_000,
            False,
            ("diagnostic only",),
            "pipewire-sha256:test-device",
            123,
        )


def test_sync_current_renders_separate_calibrations_and_pipewire_evidence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = SyncRuntime((2_150_000,))

    exit_code = cli.main(
        [
            "sync",
            "current",
            "--samples",
            "1",
            "--audio-latency-ms",
            "100",
            "--audio-uncertainty-ms",
            "5",
            "--lyrics-shift-ms",
            "50",
            "--provider-timing-uncertainty-ms",
            "6",
            "--presentation-latency-ms",
            "40",
            "--presentation-uncertainty-ms",
            "7",
        ],
        runtime_factory=lambda: runtime,
        lyrics_provider_factory=lambda: _CliProvider(),
        audio_latency_probe_factory=FakeLatencyProbe,
        database_path=tmp_path / "sync.sqlite3",
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert "--- sync frame 1 ---" in captured.out
    assert "clock update: initialized" in captured.out
    assert "clock health: Converging" in captured.out
    assert "last correction class: initial anchor" in captured.out
    assert "MPRIS accepted/rejected samples: 1 / 0" in captured.out
    assert "media position: 00:02.150" in captured.out
    assert "audible estimate: 00:02.050" in captured.out
    assert "audio-output compensation: 100.000 ms ± 5.000 ms" in captured.out
    assert "lyric timestamp shift: +50.000 ms" in captured.out
    assert "presentation lead: 40.000 ms ± 7.000 ms" in captured.out
    assert "active: two" in captured.out
    assert "PipeWire reported range: 10.000 ms to 20.000 ms" in captured.out
    assert "PipeWire auto-compensation: disabled" in captured.out
    assert runtime.timing.reasons == [ObservationReason.INITIAL]
    assert runtime.monitor.closed is True


def test_sync_current_reconciles_playback_state_after_monitor_subscription(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = SyncRuntime((2_150_000,))
    original = runtime.client.inspection.snapshot
    assert original is not None
    paused_snapshot = replace(original, playback_status="Paused")
    playing_snapshot = replace(original, playback_status="Playing")
    client = SequencedClient(
        (
            PlayerInspection(
                paused_snapshot.service_name,
                paused_snapshot.bus_name,
                paused_snapshot,
            ),
            PlayerInspection(
                playing_snapshot.service_name,
                playing_snapshot.bus_name,
                playing_snapshot,
            ),
        )
    )
    runtime.client = client
    runtime.timing = SnapshotStateTiming((2_150_000,))

    exit_code = cli.main(
        ["sync", "current", "--samples", "1", "--no-pipewire"],
        runtime_factory=lambda: runtime,
        lyrics_provider_factory=lambda: _CliProvider(),
        database_path=tmp_path / "startup-resume.sqlite3",
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert client.calls == 2
    assert "media position: 00:02.150 (Playing)" in output
    assert "clock health: Converging" in output


def test_seek_signal_causes_next_position_sample_to_reset_clock(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = SyncRuntime((1_500_000, 4_200_000), seek_during_wait=True)

    exit_code = cli.main(
        [
            "sync",
            "current",
            "--samples",
            "2",
            "--interval-ms",
            "250",
            "--no-pipewire",
        ],
        runtime_factory=lambda: runtime,
        lyrics_provider_factory=lambda: _CliProvider(),
        database_path=tmp_path / "seek.sqlite3",
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert runtime.waits == [250]
    assert runtime.wakes == 1
    assert runtime.timing.reasons == [ObservationReason.INITIAL, ObservationReason.SEEK]
    assert "--- sync frame 2 ---" in output
    assert "clock update: reset (seek)" in output
    assert "active: four" in output


def test_audio_uncertainty_requires_an_audio_calibration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(
        ["sync", "current", "--audio-uncertainty-ms", "5", "--samples", "1"]
    )

    assert exit_code == 2
    assert "requires --audio-latency-ms" in capsys.readouterr().err


def test_sync_probe_is_bounded_and_never_loads_or_dumps_lyrics(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = SyncRuntime((1_000_000, 1_250_000))

    database_path = tmp_path / "probe.sqlite3"
    exit_code = cli.main(
        ["sync", "probe", "--samples", "2"],
        runtime_factory=lambda: runtime,
        audio_latency_probe_factory=FakeLatencyProbe,
        database_path=database_path,
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "samples attempted: 2" in output
    assert "MPRIS RTT min/median/p95/max:" in output
    assert "phase residual |median|/p95/max:" in output
    assert "estimated drift:" in output
    assert "clock status:" in output
    assert "PipeWire reported range: 10.000 ms to 20.000 ms" in output
    assert "active:" not in output
    assert "lyrics_display_delay" not in output
    assert database_path.exists() is False


def test_sync_probe_reconciles_playback_state_after_monitor_subscription(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = SyncRuntime((1_000_000,))
    original = runtime.client.inspection.snapshot
    assert original is not None
    paused_snapshot = replace(original, playback_status="Paused")
    playing_snapshot = replace(original, playback_status="Playing")
    client = SequencedClient(
        (
            PlayerInspection(
                paused_snapshot.service_name,
                paused_snapshot.bus_name,
                paused_snapshot,
            ),
            PlayerInspection(
                playing_snapshot.service_name,
                playing_snapshot.bus_name,
                playing_snapshot,
            ),
        )
    )
    runtime.client = client
    runtime.timing = SnapshotStateTiming((1_000_000,))

    exit_code = cli.main(
        ["sync", "probe", "--samples", "1", "--no-pipewire"],
        runtime_factory=lambda: runtime,
        database_path=tmp_path / "startup-resume-probe.sqlite3",
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert client.calls == 2
    assert "playback status: Playing" in output


def test_sync_probe_ctrl_c_returns_130_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = InterruptingRuntime((1_000_000,))

    exit_code = cli.main(
        ["sync", "probe", "--duration-seconds", "30", "--no-pipewire"],
        runtime_factory=lambda: runtime,
        database_path=tmp_path / "interrupt.sqlite3",
    )
    captured = capsys.readouterr()

    assert exit_code == 130
    assert "clock status:" in captured.out
    assert "PipeWire latency status: unavailable" in captured.out
    assert "Traceback" not in captured.out + captured.err
    assert runtime.monitor.closed is True


def test_sync_probe_reports_sampling_failure_as_degraded_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = SyncRuntime((1_000_000,))
    runtime.timing = FailingSecondTiming((1_000_000,))

    exit_code = cli.main(
        ["sync", "probe", "--samples", "2", "--no-pipewire"],
        runtime_factory=lambda: runtime,
        database_path=tmp_path / "degraded-probe.sqlite3",
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "samples accepted: 1" in captured.out
    assert "samples rejected: 1" in captured.out
    assert "sampling failures: 1" in captured.out
    assert "clock status: Degraded" in captured.out
    assert "Traceback" not in captured.out + captured.err


def test_audio_residual_calibration_is_device_scoped_and_resettable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "audio.sqlite3"

    calibrated = cli.main(
        ["sync", "audio", "calibrate", "+25ms"],
        audio_latency_probe_factory=FakeLatencyProbe,
        database_path=path,
    )
    status = cli.main(
        ["sync", "audio", "status"],
        audio_latency_probe_factory=FakeLatencyProbe,
        database_path=path,
    )
    reset = cli.main(
        ["sync", "audio", "reset"],
        audio_latency_probe_factory=FakeLatencyProbe,
        database_path=path,
    )
    output = capsys.readouterr().out

    assert calibrated == status == reset == 0
    assert "device residual calibration: +25.000 ms" in output
    assert "positive residual = audio is heard later" in output
    assert "Removed this output device's residual calibration." in output


def test_document_delay_cli_persists_across_fresh_process_objects(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "delay.sqlite3"

    saved = cli.main(
        ["sync", "delay", "set", "+250ms"],
        runtime_factory=_runtime,
        lyrics_provider_factory=lambda: _CliProvider(),
        database_path=path,
    )
    shown = cli.main(
        ["sync", "delay", "show", "--offline"],
        runtime_factory=_runtime,
        lyrics_provider_factory=lambda: _CliProvider(),
        database_path=path,
    )
    reset = cli.main(
        ["sync", "delay", "reset", "--offline"],
        runtime_factory=_runtime,
        lyrics_provider_factory=lambda: _CliProvider(),
        database_path=path,
    )
    output = capsys.readouterr().out

    assert saved == shown == reset == 0
    assert "display delay: +250.000 ms" in output
    assert "positive = show lyric transitions later" in output
    assert "provider timestamps remain unchanged" in output
    assert "display delay: +0.000 ms" in output


class _CliProvider:
    name = "LRCLIB"

    def exact(self, _query: object) -> object:
        return _provider_result()

    def search(self, _query: object) -> object:
        return _provider_result()

    def parse_cached(self, _payload: bytes, *, search: bool) -> object:
        del search
        return _provider_result()
