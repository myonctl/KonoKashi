"""Best-effort, read-only PipeWire default-sink latency diagnostics."""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256

from lyriflux.domain.synchronization import (
    AudioLatencyProbeResult,
    AudioLatencyProbeStatus,
)

_MAX_OUTPUT_BYTES = 256 * 1024
_ID_PATTERN = re.compile(r"\bid\s+(\d+)\s*,")
_ROUTE_PATTERNS = (
    re.compile(r'node\.description\s*=\s*"([^"]+)"'),
    re.compile(r'node\.name\s*=\s*"([^"]+)"'),
)
_QUANTUM_PATTERN = re.compile(r'node\.latency\s*=\s*"(\d+)/(\d+)"')
_STABLE_NODE_PATTERN = re.compile(r'node\.name\s*=\s*"([^"]+)"')


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Bounded subprocess output used by the injectable probe boundary."""

    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str]], CommandResult]


def _run(command: Sequence[str]) -> CommandResult:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=2.0,
        )
    except FileNotFoundError as error:
        return CommandResult(127, "", f"{command[0]} is not installed ({error})")
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", f"{command[0]} timed out after 2 seconds")
    stdout = result.stdout[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    stderr = result.stderr[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    return CommandResult(result.returncode, stdout, stderr)


def _field_values(output: str, field: str) -> tuple[float, ...]:
    pattern = re.compile(
        rf"{re.escape(field)}[^\n]*\n?[^\n]*?(?:Float|Int|Long)\s+"
        r"(-?[0-9]+(?:\.[0-9]+)?)",
        re.IGNORECASE,
    )
    return tuple(float(match) for match in pattern.findall(output))


def _latency_range_us(
    output: str,
    *,
    quantum_frames: int | None,
    graph_rate: int | None,
) -> tuple[int, int] | None:
    minimum_quantum = _field_values(output, "minQuantum")
    maximum_quantum = _field_values(output, "maxQuantum")
    minimum_rate = _field_values(output, "minRate")
    maximum_rate = _field_values(output, "maxRate")
    minimum_ns = _field_values(output, "minNs")
    maximum_ns = _field_values(output, "maxNs")
    if not any(
        (
            minimum_quantum,
            maximum_quantum,
            minimum_rate,
            maximum_rate,
            minimum_ns,
            maximum_ns,
        )
    ):
        return None
    if (graph_rate is None or graph_rate <= 0) and not (minimum_ns or maximum_ns):
        return None

    def side(
        quantum: tuple[float, ...],
        rate: tuple[float, ...],
        nanoseconds: tuple[float, ...],
        chooser: Callable[[tuple[float, ...]], float],
    ) -> int:
        quantum_value = chooser(quantum) if quantum else 0.0
        rate_value = chooser(rate) if rate else 0.0
        ns_value = chooser(nanoseconds) if nanoseconds else 0.0
        quantum_seconds = (
            quantum_value * quantum_frames / graph_rate
            if quantum_frames is not None
            and graph_rate is not None
            and quantum_frames > 0
            and graph_rate > 0
            else 0.0
        )
        rate_seconds = (
            rate_value / graph_rate
            if graph_rate is not None and graph_rate > 0
            else 0.0
        )
        total_seconds = quantum_seconds + rate_seconds + ns_value / 1_000_000_000
        return round(total_seconds * 1_000_000)

    lower = side(minimum_quantum, minimum_rate, minimum_ns, min)
    upper = side(maximum_quantum, maximum_rate, maximum_ns, max)
    if lower < 0 or upper < 0:
        return None
    return min(lower, upper), max(lower, upper)


class PipeWireLatencyProbe:
    """Inspect SPA latency parameters without audio capture or graph mutation."""

    def __init__(
        self,
        runner: CommandRunner = _run,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._runner = runner
        self._monotonic_ns = monotonic_ns

    def probe(self) -> AudioLatencyProbeResult:
        """Report default-sink graph evidence with explicit mapping limitations."""

        inspected = self._runner(("wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"))
        if inspected.returncode != 0:
            detail = inspected.stderr.strip() or "default sink inspection failed"
            return AudioLatencyProbeResult(
                AudioLatencyProbeStatus.UNAVAILABLE,
                diagnostics=(detail,),
            )
        identifier = _ID_PATTERN.search(inspected.stdout)
        quantum = _QUANTUM_PATTERN.search(inspected.stdout)
        stable_node = _STABLE_NODE_PATTERN.search(inspected.stdout)
        route = next(
            (
                match.group(1)
                for pattern in _ROUTE_PATTERNS
                if (match := pattern.search(inspected.stdout)) is not None
            ),
            None,
        )
        device_key = (
            None
            if stable_node is None
            else "pipewire-sha256:"
            + sha256(stable_node.group(1).encode("utf-8")).hexdigest()
        )
        measured_at_ns = self._monotonic_ns()
        if identifier is None:
            return AudioLatencyProbeResult(
                AudioLatencyProbeStatus.PARTIAL,
                route=route,
                diagnostics=("default sink did not expose a PipeWire object ID",),
                device_key=device_key,
                measured_at_ns=measured_at_ns,
            )
        frames = None if quantum is None else int(quantum.group(1))
        graph_rate = None if quantum is None else int(quantum.group(2))
        quantum_us = (
            None
            if frames is None or graph_rate is None or graph_rate <= 0
            else round(frames / graph_rate * 1_000_000)
        )
        parameters = self._runner(
            ("pw-cli", "enum-params", identifier.group(1), "Latency")
        )
        if parameters.returncode != 0:
            detail = parameters.stderr.strip() or "SPA Latency enumeration failed"
            return AudioLatencyProbeResult(
                AudioLatencyProbeStatus.PARTIAL,
                route=route,
                graph_quantum_us=quantum_us,
                diagnostics=(detail, *_LIMITATIONS),
                device_key=device_key,
                measured_at_ns=measured_at_ns,
            )
        latency_range = _latency_range_us(
            parameters.stdout,
            quantum_frames=frames,
            graph_rate=graph_rate,
        )
        if latency_range is None:
            return AudioLatencyProbeResult(
                AudioLatencyProbeStatus.PARTIAL,
                route=route,
                graph_quantum_us=quantum_us,
                diagnostics=(
                    "SPA Latency output contained no usable range",
                    *_LIMITATIONS,
                ),
                device_key=device_key,
                measured_at_ns=measured_at_ns,
            )
        return AudioLatencyProbeResult(
            AudioLatencyProbeStatus.REPORTED,
            route=route,
            minimum_us=latency_range[0],
            maximum_us=latency_range[1],
            graph_quantum_us=quantum_us,
            safe_for_automatic_compensation=False,
            diagnostics=_LIMITATIONS,
            device_key=device_key,
            measured_at_ns=measured_at_ns,
        )


_LIMITATIONS = (
    "the default sink is not proof of the selected MPRIS stream's current route",
    "reported graph/device latency does not quantify every codec, wireless, "
    "acoustic, or listener delay",
    "PipeWire latency is diagnostic evidence and is not applied without "
    "explicit calibration",
)
