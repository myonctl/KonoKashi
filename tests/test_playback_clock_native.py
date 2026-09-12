"""Differential validation for the bounded native PlaybackClock experiment."""

from __future__ import annotations

import random
from collections.abc import Callable

import pytest

from konokashi.application.playback_clock import (
    NativePlaybackClock,
    PlaybackClock,
    PlaybackClockPolicy,
    PythonPlaybackClock,
)
from konokashi.domain.synchronization import (
    ObservationReason,
    PlaybackState,
    PositionObservation,
    PositionSampleSource,
)


class FakeMonotonic:
    def __init__(self) -> None:
        self.now_ns = 0

    def __call__(self) -> int:
        return self.now_ns


def _paired(
    monotonic: Callable[[], int],
    policy: PlaybackClockPolicy | None = None,
) -> tuple[PythonPlaybackClock, NativePlaybackClock]:
    return PythonPlaybackClock(monotonic, policy), NativePlaybackClock(
        monotonic, policy
    )


def _assert_estimate_parity(
    clocks: tuple[PythonPlaybackClock, NativePlaybackClock],
    *,
    now_ns: int,
    duration_us: int | None = None,
) -> None:
    assert clocks[1].estimate(now_ns=now_ns, duration_us=duration_us) == clocks[
        0
    ].estimate(now_ns=now_ns, duration_us=duration_us)


def test_public_clock_selects_native_implementation_and_retains_oracle() -> None:
    assert PlaybackClock is NativePlaybackClock
    assert PythonPlaybackClock is not NativePlaybackClock
    native = PlaybackClock(lambda: 0)

    assert isinstance(native, NativePlaybackClock)
    assert type(native).__name__ == "PlaybackClock"
    assert native.available is False


def test_native_and_python_policy_validation_and_custom_policy_parity() -> None:
    with pytest.raises(ValueError, match="positive"):
        PlaybackClockPolicy(history_size=0)

    monotonic = FakeMonotonic()
    policy = PlaybackClockPolicy(
        discontinuity_us=90_000,
        maximum_phase_step_us=17_000,
        phase_correction_horizon_us=750_000,
        history_size=9,
    )
    python, native = _paired(monotonic, policy)
    assert native.policy == python.policy == policy
    first = PositionObservation(
        "custom",
        2_000_000,
        PlaybackState.PLAYING,
        1.25,
        0,
        10_000_000,
        ObservationReason.INITIAL,
    )
    assert native.observe(first) == python.observe(first)
    monotonic.now_ns = 400_000_000
    correction = PositionObservation(
        "custom",
        2_470_000,
        PlaybackState.PLAYING,
        1.25,
        390_000_000,
        400_000_000,
    )
    assert native.observe(correction) == python.observe(correction)
    _assert_estimate_parity((python, native), now_ns=800_000_000)


@pytest.mark.parametrize("seed", range(32))
def test_randomized_native_python_event_stream_parity(seed: int) -> None:
    generator = random.Random(seed)
    monotonic = FakeMonotonic()
    python, native = _paired(monotonic)
    clocks = (python, native)
    session_index = 0
    session_id = f"track-{session_index}"
    state = PlaybackState.PLAYING
    rate = 1.0
    newest_response_ns = -1

    for _step in range(240):
        monotonic.now_ns += generator.randint(0, 450_000_000)
        action = generator.randrange(100)
        if action < 58:
            reason = generator.choices(
                tuple(ObservationReason),
                weights=(3, 55, 5, 7, 5, 5, 4),
                k=1,
            )[0]
            if reason is ObservationReason.TRACK:
                session_index += 1
                session_id = f"track-{session_index}"
            if reason is ObservationReason.STATUS:
                state = generator.choice(tuple(PlaybackState))
            if reason is ObservationReason.RATE:
                rate = generator.choice((0.5, 0.75, 1.0, 1.25, 1.5, 2.0))
            round_trip_ns = generator.choice(
                (0, 200_000, 2_000_000, 20_000_000, 240_000_000)
            )
            response_ns = monotonic.now_ns
            stale_mode = generator.randrange(20)
            if newest_response_ns >= 0 and stale_mode == 0:
                response_ns = newest_response_ns
            elif newest_response_ns >= 0 and stale_mode == 1:
                response_ns = max(0, newest_response_ns - 1)
            request_ns = max(0, response_ns - round_trip_ns)
            midpoint_ns = request_ns + (response_ns - request_ns) // 2
            reference = python.estimate(now_ns=midpoint_ns)
            assert native.estimate(now_ns=midpoint_ns) == reference
            predicted = 0 if reference is None else reference.position_us
            if reason in {
                ObservationReason.SEEK,
                ObservationReason.TRACK,
                ObservationReason.SUSPEND_RESUME,
            }:
                position_us = generator.randint(0, 360_000_000)
            else:
                residual = generator.choice(
                    (-300_000, -80_000, -2_000, 0, 1_500, 40_000, 249_999, 300_000)
                )
                position_us = max(0, predicted + residual)
            trusted = generator.randrange(17) != 0
            source = generator.choice(tuple(PositionSampleSource))
            observation = PositionObservation(
                session_id,
                position_us,
                state,
                rate,
                request_ns,
                response_ns,
                reason,
                source,
                trusted,
            )
            assert native.observe(observation) == python.observe(observation)
            if response_ns > newest_response_ns:
                newest_response_ns = response_ns
        elif action < 68:
            state = generator.choice(tuple(PlaybackState))
            assert native.transition_state(
                state, monotonic_ns=monotonic.now_ns
            ) == python.transition_state(state, monotonic_ns=monotonic.now_ns)
        elif action < 77:
            rate = generator.choice((0.5, 0.75, 1.0, 1.25, 1.5, 2.0))
            assert native.transition_rate(
                rate, monotonic_ns=monotonic.now_ns
            ) == python.transition_rate(rate, monotonic_ns=monotonic.now_ns)
        elif action < 85:
            position_us = generator.randint(0, 360_000_000)
            assert native.reanchor_seek(
                session_id,
                position_us,
                state,
                rate,
                monotonic_ns=monotonic.now_ns,
            ) == python.reanchor_seek(
                session_id,
                position_us,
                state,
                rate,
                monotonic_ns=monotonic.now_ns,
            )
            newest_response_ns = max(newest_response_ns, monotonic.now_ns)
        elif action < 91:
            native.mark_suspend_resume(monotonic_ns=monotonic.now_ns)
            python.mark_suspend_resume(monotonic_ns=monotonic.now_ns)
        elif action < 96:
            reason = generator.choice(("D-Bus timeout", "", "  unavailable  "))
            assert native.mark_sampling_failure(reason) == python.mark_sampling_failure(
                reason
            )
        duration_us = (
            None if generator.randrange(4) else generator.randint(0, 400_000_000)
        )
        _assert_estimate_parity(
            clocks,
            now_ns=monotonic.now_ns + generator.randint(0, 2_500_000_000),
            duration_us=duration_us,
        )


@pytest.mark.parametrize("rate", (0.0, -1.0, float("inf"), float("nan"), 1e-12))
def test_native_transition_rate_preserves_domain_validation(rate: float) -> None:
    native = NativePlaybackClock(lambda: 0)
    assert native.transition_rate(rate) is None
    native.observe(
        PositionObservation(
            "track",
            0,
            PlaybackState.PLAYING,
            1.0,
            0,
            0,
        )
    )

    with pytest.raises(ValueError):
        native.transition_rate(rate)
