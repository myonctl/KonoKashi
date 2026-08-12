"""Discovery and race-condition tests at the fake D-Bus boundary."""

from lyricflow.domain.models import InspectionFailure
from lyricflow.infrastructure.mpris.backend import (
    MprisBackendError,
    MprisServiceUnavailable,
)
from lyricflow.infrastructure.mpris.metadata_mapper import (
    MPRIS_PLAYER_INTERFACE,
    MPRIS_ROOT_INTERFACE,
    full_service_name,
)
from lyricflow.infrastructure.mpris.player_registry import MprisClient
from tests.mpris_fakes import FakeMprisBackend


def add_player(
    backend: FakeMprisBackend,
    short_name: str,
    *,
    title: str = "Track",
) -> str:
    bus_name = full_service_name(short_name)
    backend.names.append(bus_name)
    backend.properties[(bus_name, MPRIS_ROOT_INTERFACE)] = {
        "Identity": short_name,
    }
    backend.properties[(bus_name, MPRIS_PLAYER_INTERFACE)] = {
        "PlaybackStatus": "Playing",
        "Metadata": {"xesam:title": title},
        "Position": 10,
    }
    return bus_name


def test_zero_players_ignores_unrelated_bus_services() -> None:
    client = MprisClient(FakeMprisBackend(["org.freedesktop.DBus", ":1.42"]))

    result = client.list_players()

    assert result.error is None
    assert result.players == ()


def test_one_player_is_inspected() -> None:
    backend = FakeMprisBackend()
    add_player(backend, "strawberry", title="One")

    result = MprisClient(backend).list_players()

    assert len(result.players) == 1
    assert result.players[0].snapshot is not None
    assert result.players[0].snapshot.metadata.title == "One"


def test_several_players_are_all_returned_in_service_order() -> None:
    backend = FakeMprisBackend(["org.example.Unrelated"])
    add_player(backend, "z-player", title="Last")
    add_player(backend, "a-player", title="First")
    add_player(backend, "middle-player", title="Middle")

    result = MprisClient(backend).list_players()

    assert [player.service_name for player in result.players] == [
        "a-player",
        "middle-player",
        "z-player",
    ]


def test_missing_position_is_read_separately_when_available() -> None:
    backend = FakeMprisBackend()
    bus_name = add_player(backend, "position-player")
    backend.properties[(bus_name, MPRIS_PLAYER_INTERFACE)] = {
        "PlaybackStatus": "Paused",
        "Metadata": {},
    }
    backend.single_properties[(bus_name, MPRIS_PLAYER_INTERFACE, "Position")] = 77

    inspection = MprisClient(backend).inspect_player("position-player")

    assert inspection.snapshot is not None
    assert inspection.snapshot.position_us == 77


def test_missing_position_remains_normal_incomplete_metadata() -> None:
    backend = FakeMprisBackend()
    bus_name = add_player(backend, "no-position")
    backend.properties[(bus_name, MPRIS_PLAYER_INTERFACE)] = {
        "PlaybackStatus": "Playing",
        "Metadata": {},
    }

    inspection = MprisClient(backend).inspect_player("no-position")

    assert inspection.succeeded
    assert inspection.snapshot is not None
    assert inspection.snapshot.position_us is None
    assert inspection.snapshot.diagnostics == (
        "Position: unavailable (property is not available)",
    )


def test_service_disappearing_during_player_read_is_expected_failure() -> None:
    backend = FakeMprisBackend()
    bus_name = add_player(backend, "vanishing")
    backend.get_all_failures[(bus_name, MPRIS_PLAYER_INTERFACE)] = (
        MprisServiceUnavailable("name has no owner")
    )

    inspection = MprisClient(backend).inspect_player("vanishing")

    assert not inspection.succeeded
    assert inspection.failure is InspectionFailure.UNAVAILABLE
    assert inspection.message is not None
    assert "disappeared during inspection" in inspection.message


def test_discovery_bus_error_is_a_result_not_an_exception() -> None:
    backend = FakeMprisBackend()
    backend.list_failure = MprisBackendError("session bus unavailable")

    result = MprisClient(backend).list_players()

    assert result.players == ()
    assert result.error == "session bus unavailable"
