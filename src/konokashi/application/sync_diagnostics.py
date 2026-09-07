"""Bounded, explainable terminal rendering for Stage 6 synchronization."""

from __future__ import annotations

from konokashi.application.lyrics_sync import SynchronizationFrame
from konokashi.domain.lyrics import RepresentationKind
from konokashi.domain.representations import EffectiveRepresentationLine
from konokashi.domain.synchronization import (
    AudioLatencyProbeResult,
    PlaybackPositionEstimate,
)


def _milliseconds(microseconds: int | None) -> str:
    return "unknown" if microseconds is None else f"{microseconds / 1_000:.3f} ms"


def _position(microseconds: int | None) -> str:
    if microseconds is None:
        return "unknown"
    total_ms = microseconds // 1_000
    minutes, remainder_ms = divmod(total_ms, 60_000)
    seconds, milliseconds = divmod(remainder_ms, 1_000)
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def render_sync_frame(
    estimate: PlaybackPositionEstimate,
    frame: SynchronizationFrame,
    representations: tuple[EffectiveRepresentationLine, ...] = (),
) -> str:
    """Render clock, error terms, active lines, and next deadline separately."""

    diagnostics = estimate.diagnostics
    budget = frame.error_budget
    active = (
        " | ".join(line.text for line in frame.lyrics.active)
        or "<before first timed line>"
    )
    next_text = " | ".join(line.text for line in frame.lyrics.next) or "<end>"
    selected = {
        (item.original_line.line_id, item.kind): item
        for item in representations
        if item.text is not None
    }

    def layer(kind: RepresentationKind) -> str:
        return " | ".join(
            item.text or ""
            for line in frame.lyrics.active
            if (item := selected.get((line.line_id, kind))) is not None
        )

    romanized = layer(RepresentationKind.ROMANIZED) or layer(
        RepresentationKind.TRANSLITERATED
    )
    translated = layer(RepresentationKind.TRANSLATED)
    active_line_shifts = (
        ", ".join(
            f"{line_id}:{shift_us / 1_000:+.3f} ms"
            for line_id, shift_us in frame.lyrics.active_line_shifts_us
            if shift_us
        )
        or "none"
    )
    next_line_shifts = (
        ", ".join(
            f"{line_id}:{shift_us / 1_000:+.3f} ms"
            for line_id, shift_us in frame.lyrics.next_line_shifts_us
            if shift_us
        )
        or "none"
    )
    drift = (
        "unmeasured"
        if diagnostics.drift_ppm is None
        else f"{diagnostics.drift_ppm:+.1f} ppm"
    )
    deadline = "none"
    if frame.deadline is not None:
        remaining_ms = (
            frame.deadline.display_deadline_ns - estimate.monotonic_ns
        ) / 1_000_000
        deadline = f"{remaining_ms:.3f} ms from this frame"
    lines = [
        "media position: "
        f"{_position(frame.media_position_us)} ({estimate.state.value})",
        f"audible estimate: {_position(frame.audible_position_us)}",
        f"clock health: {diagnostics.health.value}",
        f"clock quality: {diagnostics.quality.value}",
        "last correction class: "
        + (
            "none"
            if diagnostics.last_correction_class is None
            else diagnostics.last_correction_class.value
        ),
        f"MPRIS accepted/rejected samples: "
        f"{diagnostics.accepted_sample_count} / {diagnostics.rejected_sample_count}",
        f"MPRIS discipline-window samples: {diagnostics.sample_count}",
        f"MPRIS sample age: {_milliseconds(diagnostics.sample_age_us)}",
        f"MPRIS call half-RTT: {_milliseconds(diagnostics.last_round_trip_us)}",
        "MPRIS RTT min/median/p95/max: "
        f"{_milliseconds(diagnostics.minimum_recent_rtt_us)} / "
        f"{_milliseconds(diagnostics.median_recent_rtt_us)} / "
        f"{_milliseconds(diagnostics.p95_recent_rtt_us)} / "
        f"{_milliseconds(diagnostics.maximum_recent_rtt_us)}",
        f"MPRIS last residual: {_milliseconds(diagnostics.last_residual_us)}",
        f"MPRIS residual jitter: {_milliseconds(diagnostics.residual_jitter_us)}",
        "MPRIS phase residual |median|/p95/max: "
        f"{_milliseconds(diagnostics.median_absolute_residual_us)} / "
        f"{_milliseconds(diagnostics.p95_absolute_residual_us)} / "
        f"{_milliseconds(diagnostics.maximum_absolute_residual_us)}",
        "MPRIS phase error remaining: "
        f"{_milliseconds(diagnostics.phase_error_remaining_us)}",
        f"MPRIS base/effective rate: {estimate.base_rate:.9f} / "
        f"{estimate.effective_rate:.9f}",
        "MPRIS scheduled slew remaining: "
        f"{_milliseconds(estimate.slew_remaining_us)} over "
        f"{_milliseconds(estimate.slew_remaining_duration_us)}",
        "MPRIS observed error bound: "
        f"{_milliseconds(budget.mpris_clock_us)} (not total accuracy)",
        "MPRIS/local clock drift: "
        f"{drift}; correction "
        f"{'on' if diagnostics.drift_correction_active else 'off'}",
        f"MPRIS discontinuities: {diagnostics.discontinuity_count}",
        "MPRIS last sample source: "
        + (
            "none"
            if diagnostics.last_sample_source is None
            else diagnostics.last_sample_source.value
        ),
        "audio-output compensation: "
        f"{_milliseconds(budget.audio_output_us)} ± "
        f"{_milliseconds(budget.audio_uncertainty_us)}",
        "audio automatic/residual: "
        f"{_milliseconds(budget.audio_automatic_us)} / "
        f"{budget.audio_residual_us / 1_000:+.3f} ms",
        f"lyric timestamp shift: {budget.lyric_shift_us / 1_000:+.3f} ms; "
        "provider uncertainty "
        f"{_milliseconds(budget.provider_timing_uncertainty_us)}",
        f"next-line timestamp shift: {budget.next_line_shift_us / 1_000:+.3f} ms",
        f"active per-line shifts: {active_line_shifts}",
        f"next per-line shifts: {next_line_shifts}",
        f"presentation lead: {_milliseconds(budget.presentation_us)} ± "
        f"{_milliseconds(budget.presentation_uncertainty_us)}",
        "combined quantified uncertainty: "
        f"{_milliseconds(budget.combined_uncertainty_us)}",
        f"active: {active}",
        f"active romanized/transliterated: {romanized or '<unavailable>'}",
        f"active translation: {translated or '<unavailable>'}",
        f"next: {next_text}",
        f"next display deadline: {deadline}",
    ]
    if budget.unknown_sources:
        lines.append("unquantified: " + "; ".join(budget.unknown_sources))
    return "\n".join(lines)


def render_audio_latency_probe(result: AudioLatencyProbeResult) -> str:
    """Render PipeWire evidence without presenting it as applied calibration."""

    route = result.route or "unknown"
    reported = (
        "unknown"
        if result.minimum_us is None or result.maximum_us is None
        else f"{_milliseconds(result.minimum_us)} to {_milliseconds(result.maximum_us)}"
    )
    lines = [
        f"PipeWire latency status: {result.status.value}",
        f"PipeWire default sink: {route}",
        f"PipeWire reported range: {reported}",
        f"PipeWire graph quantum: {_milliseconds(result.graph_quantum_us)}",
        f"PipeWire chosen estimate: {_milliseconds(result.chosen_estimate_us)}",
        f"PipeWire range uncertainty: ±{_milliseconds(result.uncertainty_us)}",
        "PipeWire stable device key: "
        + ("available" if result.device_key is not None else "unavailable"),
        "PipeWire auto-compensation: "
        f"{'enabled' if result.safe_for_automatic_compensation else 'disabled'}",
    ]
    lines.extend(f"PipeWire limitation: {item}" for item in result.diagnostics)
    return "\n".join(lines)


def render_probe_report(
    *,
    player: str,
    source: str,
    playback_status: str,
    attempts: int,
    sampling_failures: int,
    estimate: PlaybackPositionEstimate,
    audio: AudioLatencyProbeResult,
    document_delay_us: int | None = None,
    device_residual_us: int | None = None,
) -> str:
    """Render bounded clock-quality statistics without lyric content."""

    diagnostics = estimate.diagnostics
    drift = (
        "unmeasured"
        if diagnostics.drift_ppm is None
        else f"{diagnostics.drift_ppm:+.1f} ppm"
    )
    lines = [
        f"player: {player}",
        f"source: {source}",
        f"playback status: {playback_status}",
        f"samples attempted: {attempts}",
        f"samples accepted: {diagnostics.accepted_sample_count}",
        f"samples rejected: {diagnostics.rejected_sample_count}",
        f"sampling failures: {sampling_failures}",
        "MPRIS RTT min/median/p95/max: "
        f"{_milliseconds(diagnostics.minimum_recent_rtt_us)} / "
        f"{_milliseconds(diagnostics.median_recent_rtt_us)} / "
        f"{_milliseconds(diagnostics.p95_recent_rtt_us)} / "
        f"{_milliseconds(diagnostics.maximum_recent_rtt_us)}",
        "phase residual |median|/p95/max: "
        f"{_milliseconds(diagnostics.median_absolute_residual_us)} / "
        f"{_milliseconds(diagnostics.p95_absolute_residual_us)} / "
        f"{_milliseconds(diagnostics.maximum_absolute_residual_us)}",
        f"estimated drift: {drift}",
        f"clock status: {diagnostics.health.value}",
        f"discontinuities: {diagnostics.discontinuity_count}",
        render_audio_latency_probe(audio),
        "document display delay: "
        + (
            "not loaded by clock-only probe"
            if document_delay_us is None
            else f"{document_delay_us / 1_000:+.3f} ms"
        ),
        "device residual calibration: "
        + (
            "unavailable"
            if device_residual_us is None
            else f"{device_residual_us / 1_000:+.3f} ms"
        ),
        "effective clock uncertainty: "
        f"±{_milliseconds(diagnostics.observed_error_bound_us)} plus listed "
        "unquantified sources",
    ]
    return "\n".join(lines)
