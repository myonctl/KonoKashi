#!/usr/bin/env python3
"""Compare the production native LRC parser with its Python oracle."""

from __future__ import annotations

import argparse
import gc
import json
import resource
import statistics
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict, cast

from konokashi.infrastructure.lyrics.lrc import (
    MAX_LYRICS_TEXT_CHARS,
    parse_lyrics_text,
)
from konokashi.infrastructure.lyrics.lrc_reference import parse_lyrics_text_python

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_CASES = ("plain", "line", "enhanced", "malformed", "maximum")


class Measurement(TypedDict):
    implementation: str
    case: str
    median_ms: float
    python_peak_bytes: int
    peak_rss_kib: int


def benchmark_case(case: str) -> str:
    """Build one deterministic input from the frozen Stage 11 workloads."""

    if case == "plain":
        return "\n".join(f"plain line {index} 君" for index in range(1_000))
    if case == "line":
        return "\n".join(
            f"[{index // 60:02d}:{index % 60:02d}.00]line {index} 君"
            for index in range(1_000)
        )
    if case == "enhanced":
        return "\n".join(
            f"[{index // 60:02d}:{index % 60:02d}.00]<00:00.00>A <00:00.50>B"
            for index in range(600)
        )
    if case == "malformed":
        return "\n".join("[00:61.00]bad" for _ in range(1_000))
    if case == "maximum":
        return "界" * MAX_LYRICS_TEXT_CHARS
    raise ValueError(f"unknown benchmark case: {case}")


def _parser(implementation: str) -> Callable[[str], object]:
    if implementation == "native":
        return parse_lyrics_text
    if implementation == "python":
        return parse_lyrics_text_python
    raise ValueError(f"unknown implementation: {implementation}")


def _measure(implementation: str, case: str, samples: int) -> Measurement:
    text = benchmark_case(case)
    parser = _parser(implementation)
    gc.collect()
    tracemalloc.start()
    measured = parser(text)
    _, python_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    del measured
    gc.collect()

    durations: list[float] = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        parser(text)
        durations.append((time.perf_counter_ns() - started) / 1_000_000)
    return {
        "implementation": implementation,
        "case": case,
        "median_ms": statistics.median(durations),
        "python_peak_bytes": python_peak,
        "peak_rss_kib": peak_rss,
    }


def _child_measurement(implementation: str, case: str, samples: int) -> Measurement:
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            implementation,
            case,
            "--samples",
            str(samples),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return cast(Measurement, json.loads(completed.stdout))


def _render(measurements: list[Measurement]) -> str:
    by_case = {
        (measurement["case"], measurement["implementation"]): measurement
        for measurement in measurements
    }
    lines = [
        "| Case | Python ms | Native ms | Runtime change | "
        "Python peak MiB | Native peak MiB | Process peak RSS Python/native MiB |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for case in BENCHMARK_CASES:
        python = by_case[(case, "python")]
        native = by_case[(case, "native")]
        change = (native["median_ms"] / python["median_ms"] - 1.0) * 100.0
        lines.append(
            f"| {case} | {python['median_ms']:.3f} | {native['median_ms']:.3f} "
            f"| {change:+.1f}% | {python['python_peak_bytes'] / 2**20:.3f} "
            f"| {native['python_peak_bytes'] / 2**20:.3f} | "
            f"{python['peak_rss_kib'] / 2**10:.3f}/"
            f"{native['peak_rss_kib'] / 2**10:.3f} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=11)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--worker", nargs=2, metavar=("IMPLEMENTATION", "CASE"))
    arguments = parser.parse_args()
    if arguments.samples < 3:
        parser.error("--samples must be at least 3")
    if arguments.worker:
        implementation, case = arguments.worker
        print(json.dumps(_measure(implementation, case, arguments.samples)))
        return 0

    for case in BENCHMARK_CASES:
        text = benchmark_case(case)
        if parse_lyrics_text(text) != parse_lyrics_text_python(text):
            raise RuntimeError("benchmark input failed native/reference parity")
    measurements = [
        _child_measurement(implementation, case, arguments.samples)
        for case in BENCHMARK_CASES
        for implementation in ("python", "native")
    ]
    if arguments.json:
        print(json.dumps(measurements, indent=2))
    else:
        print(_render(measurements))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
