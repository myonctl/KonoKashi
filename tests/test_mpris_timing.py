"""MPRIS timing-boundary tests with no live session bus."""

import pytest

from lyricflow.domain.models import PlayerCapabilities, PlayerSnapshot, RawTrackMetadata
from lyricflow.domain.synchronization import ObservationReason, PlaybackState
from lyricflow.infrastructure.mpris.backend import MprisBackendError
from lyricflow.infrastructure.mpris.metadata_mapper import MPRIS_PLAYER_INTERFACE
from lyricflow.infrastructure.mpris.player_registry import MprisPositionSampler
from tests.mpris_fakes import FakeMprisBackend


def snapshot(*, rate: float | None = 1.0) -> PlayerSnapshot:
    return PlayerSnapshot(
        "player",
        "org.mpris.MediaPlayer2.player",
        "Player",
        "player",
        "Playing",
        RawTrackMetadata(track_id="/track/1"),
        None,
        PlayerCapabilities(),
        rate=rate,
    )


def test_sampler_brackets_only_position_read_with_monotonic_time() -> None:
    backend = FakeMprisBackend()
    key = (snapshot().bus_name, MPRIS_PLAYER_INTERFACE, "Position")
    backend.single_properties[key] = 1_234_567
    values = iter((1_000_000_000, 1_006_000_000))
    sampler = MprisPositionSampler(backend, lambda: next(values))

    result = sampler.sample(
        snapshot(), "source:track", reason=ObservationReason.INITIAL
    )

    assert result.position_us == 1_234_567
    assert result.midpoint_ns == 1_003_000_000
    assert result.half_round_trip_us == 3_000
    assert result.state is PlaybackState.PLAYING
    assert result.rate == 1.0


@pytest.mark.parametrize("value", [True, "123", -1])
def test_sampler_rejects_malformed_or_negative_position(value: object) -> None:
    player = snapshot()
    backend = FakeMprisBackend()
    backend.single_properties[(player.bus_name, MPRIS_PLAYER_INTERFACE, "Position")] = (
        value
    )
    sampler = MprisPositionSampler(backend, iter((0, 0)).__next__)

    with pytest.raises(MprisBackendError, match="Position returned"):
        sampler.sample(player, "source:track")


def test_sampler_rejects_invalid_rate_as_controlled_backend_error() -> None:
    player = snapshot(rate=0.0)
    backend = FakeMprisBackend()
    backend.single_properties[(player.bus_name, MPRIS_PLAYER_INTERFACE, "Position")] = 1
    sampler = MprisPositionSampler(backend, iter((0, 0)).__next__)

    with pytest.raises(MprisBackendError, match="playback rate"):
        sampler.sample(player, "source:track")


def test_sampler_rejects_rate_below_fixed_point_resolution() -> None:
    player = snapshot(rate=1e-20)
    backend = FakeMprisBackend()
    backend.single_properties[(player.bus_name, MPRIS_PLAYER_INTERFACE, "Position")] = 1
    sampler = MprisPositionSampler(backend, iter((0, 0)).__next__)

    with pytest.raises(MprisBackendError, match="fixed-point resolution"):
        sampler.sample(player, "source:track")


def test_sampler_does_not_assume_rate_for_a_playing_player() -> None:
    player = snapshot(rate=None)
    backend = FakeMprisBackend()
    backend.single_properties[(player.bus_name, MPRIS_PLAYER_INTERFACE, "Position")] = 1
    sampler = MprisPositionSampler(backend, iter((0, 0)).__next__)

    with pytest.raises(MprisBackendError, match="Rate is unavailable"):
        sampler.sample(player, "source:track")
