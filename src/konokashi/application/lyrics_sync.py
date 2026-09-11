"""Active-line lookup and frontend-neutral lyric transition scheduling."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

from konokashi.application.representations import original_lines
from konokashi.domain.lyrics import (
    LyricDocument,
    LyricLine,
    lyric_line_timing_start_ms,
)
from konokashi.domain.synchronization import (
    LineTimingCalibration,
    LyricTimingCalibration,
    PlaybackPositionEstimate,
    PlaybackState,
    SynchronizationCalibration,
)


@dataclass(frozen=True, slots=True)
class TimingErrorBudget:
    """Separate timing terms plus an optional computable end-to-end bound."""

    mpris_clock_us: int
    audio_output_us: int | None
    audio_uncertainty_us: int | None
    lyric_shift_us: int
    next_line_shift_us: int
    provider_timing_uncertainty_us: int | None
    presentation_us: int
    presentation_uncertainty_us: int | None
    combined_uncertainty_us: int | None
    unknown_sources: tuple[str, ...] = field(default_factory=tuple)
    audio_automatic_us: int | None = None
    audio_residual_us: int = 0


@dataclass(frozen=True, slots=True)
class ActiveLyricState:
    """The active equal-timestamp group and surrounding original lines."""

    timeline_position_us: int
    active: tuple[LyricLine, ...]
    active_line_shifts_us: tuple[tuple[str, int], ...]
    previous: tuple[LyricLine, ...]
    next: tuple[LyricLine, ...]
    next_line_shifts_us: tuple[tuple[str, int], ...]
    next_start_us: int | None


@dataclass(frozen=True, slots=True)
class TransitionDeadline:
    """Monotonic deadline a frontend can pre-compensate without clock mutation."""

    target_line_start_us: int
    target_media_position_us: int
    display_deadline_ns: int
    earliest_deadline_ns: int | None
    latest_deadline_ns: int | None
    due_now: bool
    error_budget: TimingErrorBudget


@dataclass(frozen=True, slots=True)
class SynchronizationFrame:
    """One frontend-neutral synchronized lyric state and future transition."""

    media_position_us: int
    audible_position_us: int | None
    lyrics: ActiveLyricState
    deadline: TransitionDeadline | None
    error_budget: TimingErrorBudget


@dataclass(frozen=True, slots=True)
class _TimedGroup:
    start_us: int
    lines: tuple[LyricLine, ...]


class LyricTimeline:
    """Immutable, binary-searchable original-line timeline for all frontends."""

    _CONTEXT_GROUP_LIMIT = 8

    def __init__(
        self,
        document: LyricDocument,
        calibration: LyricTimingCalibration | None = None,
    ) -> None:
        self.document = document
        self.calibration = calibration or LyricTimingCalibration()
        self.groups = _timed_groups(document, self.calibration)
        self.starts_us = tuple(group.start_us for group in self.groups)

    def active_at(self, timeline_position_us: int) -> ActiveLyricState:
        """Lookup exact half-open active intervals in logarithmic time."""

        timing = self.calibration
        groups = self.groups
        active_index = bisect_right(self.starts_us, timeline_position_us) - 1
        if active_index < 0:
            next_groups = groups[: self._CONTEXT_GROUP_LIMIT]
            next_group = next_groups[0] if next_groups else None
            next_lines = tuple(line for group in next_groups for line in group.lines)
            return ActiveLyricState(
                timeline_position_us,
                (),
                (),
                (),
                next_lines,
                tuple(
                    (line.line_id, timing.line_shift_us(line.line_id))
                    for line in next_lines
                ),
                None if next_group is None else next_group.start_us,
            )
        active = groups[active_index].lines
        previous_groups = groups[
            max(0, active_index - self._CONTEXT_GROUP_LIMIT) : active_index
        ]
        previous = tuple(line for group in previous_groups for line in group.lines)
        next_groups = groups[
            active_index + 1 : active_index + 1 + self._CONTEXT_GROUP_LIMIT
        ]
        next_group = next_groups[0] if next_groups else None
        next_lines = tuple(line for group in next_groups for line in group.lines)
        return ActiveLyricState(
            timeline_position_us,
            active,
            tuple(
                (line.line_id, timing.line_shift_us(line.line_id)) for line in active
            ),
            previous,
            next_lines,
            tuple(
                (line.line_id, timing.line_shift_us(line.line_id))
                for line in next_lines
            ),
            None if next_group is None else next_group.start_us,
        )


class LyricTimelineCache:
    """Retain one active immutable document/calibration timeline generation."""

    def __init__(self) -> None:
        self._document: LyricDocument | None = None
        self._line_adjustments: tuple[LineTimingCalibration, ...] | None = None
        self._timeline: LyricTimeline | None = None
        self._generation = 0

    @property
    def generation(self) -> int:
        """Identify the current document or lyric-calibration generation."""

        return self._generation

    def get(
        self,
        document: LyricDocument,
        calibration: LyricTimingCalibration,
    ) -> LyricTimeline:
        """Return the cached timeline, rebuilding only for relevant changes."""

        if (
            document is not self._document
            or calibration.line_adjustments != self._line_adjustments
        ):
            self._document = document
            self._line_adjustments = calibration.line_adjustments
            self._timeline = LyricTimeline(document, calibration)
            self._generation += 1
        assert self._timeline is not None
        return self._timeline


def _timed_groups(
    document: LyricDocument,
    calibration: LyricTimingCalibration,
) -> tuple[_TimedGroup, ...]:
    timed = sorted(
        (
            (start_ms * 1_000 + calibration.line_shift_us(line.line_id), line)
            for line in original_lines(document)
            if (start_ms := lyric_line_timing_start_ms(line)) is not None
        ),
        key=lambda item: item[0],
    )
    groups: list[list[LyricLine]] = []
    group_starts: list[int] = []
    group_start: int | None = None
    for effective_start_us, line in timed:
        if effective_start_us != group_start:
            groups.append([line])
            group_starts.append(effective_start_us)
            group_start = effective_start_us
        else:
            groups[-1].append(line)
    return tuple(
        _TimedGroup(start_us, tuple(group))
        for start_us, group in zip(group_starts, groups, strict=True)
    )


def active_lyrics_at(
    document: LyricDocument,
    timeline_position_us: int,
    calibration: LyricTimingCalibration | None = None,
) -> ActiveLyricState:
    """Find active lines while preserving simultaneous/repeated line instances."""

    return LyricTimeline(document, calibration).active_at(timeline_position_us)


def _error_budget(
    estimate: PlaybackPositionEstimate,
    calibration: SynchronizationCalibration,
    next_line_shift_us: int,
) -> TimingErrorBudget:
    unknown = list(estimate.diagnostics.unquantified_error_sources)
    audio_uncertainty = calibration.audio_output.uncertainty_us
    provider_uncertainty = calibration.lyrics.provider_uncertainty_us
    presentation_uncertainty = calibration.presentation.uncertainty_us
    if audio_uncertainty is None:
        unknown.extend(
            calibration.audio_output.limitations or ("audio latency uncertainty",)
        )
    if provider_uncertainty is None:
        unknown.extend(
            calibration.lyrics.limitations or ("provider lyric timestamp error",)
        )
    if presentation_uncertainty is None:
        unknown.append("presentation latency uncertainty")
    numeric = (audio_uncertainty, provider_uncertainty, presentation_uncertainty)
    combined = (
        None
        if any(value is None for value in numeric)
        else estimate.diagnostics.observed_error_bound_us
        + sum(value for value in numeric if value is not None)
    )
    return TimingErrorBudget(
        estimate.diagnostics.observed_error_bound_us,
        calibration.audio_output.total_compensation_us,
        audio_uncertainty,
        calibration.lyrics.shift_us,
        next_line_shift_us,
        provider_uncertainty,
        calibration.presentation.estimate_us,
        presentation_uncertainty,
        combined,
        tuple(dict.fromkeys(unknown)),
        calibration.audio_output.estimate_us,
        calibration.audio_output.residual_calibration_us,
    )


def _round_ratio(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise ValueError("deadline rate must be positive")
    if numerator >= 0:
        return (numerator + denominator // 2) // denominator
    return -((-numerator + denominator // 2) // denominator)


def synchronize(
    document: LyricDocument,
    estimate: PlaybackPositionEstimate,
    calibration: SynchronizationCalibration,
    *,
    timeline: LyricTimeline | None = None,
) -> SynchronizationFrame:
    """Map media time to audible lyric time and the next display deadline."""

    total_audio_us = calibration.audio_output.total_compensation_us
    audible_position_us = (
        None
        if total_audio_us is None
        else max(0, estimate.position_us - total_audio_us)
    )
    lookup_position_us = (
        estimate.position_us if audible_position_us is None else audible_position_us
    )
    timeline_position_us = lookup_position_us - calibration.lyrics.shift_us
    if timeline is None:
        timeline = LyricTimeline(document, calibration.lyrics)
    elif (
        timeline.document is not document
        or timeline.calibration.line_adjustments != calibration.lyrics.line_adjustments
    ):
        raise ValueError("lyric timeline does not match document calibration")
    lyrics = timeline.active_at(timeline_position_us)
    next_line_shift_us = (
        0 if not lyrics.next_line_shifts_us else lyrics.next_line_shifts_us[0][1]
    )
    budget = _error_budget(estimate, calibration, next_line_shift_us)
    deadline: TransitionDeadline | None = None
    if (
        lyrics.next_start_us is not None
        and estimate.state is PlaybackState.PLAYING
        and estimate.effective_rate > 0
    ):
        target_media_us = (
            lyrics.next_start_us + calibration.lyrics.shift_us + (total_audio_us or 0)
        )
        media_delta_us = target_media_us - estimate.position_us
        slew_progress_us = (
            _round_ratio(
                estimate.slew_remaining_duration_us * estimate.base_rate_ppb,
                1_000_000_000,
            )
            + estimate.slew_remaining_us
        )
        if media_delta_us <= slew_progress_us:
            until_transition_ns = _round_ratio(
                media_delta_us * 1_000_000_000_000,
                estimate.effective_rate_ppb,
            )
        else:
            until_transition_ns = (
                estimate.slew_remaining_duration_us * 1_000
                + _round_ratio(
                    (media_delta_us - slew_progress_us) * 1_000_000_000_000,
                    estimate.base_rate_ppb,
                )
            )
        display_deadline_ns = (
            estimate.monotonic_ns
            + until_transition_ns
            - calibration.presentation.estimate_us * 1_000
        )
        if budget.combined_uncertainty_us is None:
            earliest = latest = None
        else:
            conservative_rate_ppb = min(
                estimate.effective_rate_ppb, estimate.base_rate_ppb
            )
            uncertainty_ns = _round_ratio(
                budget.combined_uncertainty_us * 1_000_000_000_000,
                conservative_rate_ppb,
            )
            earliest = display_deadline_ns - uncertainty_ns
            latest = display_deadline_ns + uncertainty_ns
        deadline = TransitionDeadline(
            lyrics.next_start_us,
            target_media_us,
            display_deadline_ns,
            earliest,
            latest,
            display_deadline_ns <= estimate.monotonic_ns,
            budget,
        )
    return SynchronizationFrame(
        estimate.position_us,
        audible_position_us,
        lyrics,
        deadline,
        budget,
    )
