"""Discovery and race-condition tests at the fake D-Bus boundary."""

from konokashi.domain.models import InspectionFailure
from konokashi.infrastructure.mpris.backend import (
    MprisBackendError,
    MprisServiceUnavailable,
)
from konokashi.infrastructure.mpris.metadata_mapper import (
    MPRIS_PLAYER_INTERFACE,
    MPRIS_ROOT_INTERFACE,
    full_service_name,
)
from konokashi.infrastructure.mpris.player_registry import MprisClient
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


def test_maximum_rate_not_supported_preserves_useful_player_snapshot() -> None:
    backend = FakeMprisBackend()
    bus_name = add_player(backend, "firefox.instance_1_58", title="Firefox video")
    backend.property_diagnostics[(bus_name, MPRIS_PLAYER_INTERFACE)] = (
        "MaximumRate: unavailable (org.freedesktop.DBus.Error.NotSupported: "
        "MaximumRate is not supported)",
    )

    inspection = MprisClient(backend).inspect_player("firefox.instance_1_58")

    assert inspection.succeeded
    assert inspection.snapshot is not None
    assert inspection.snapshot.metadata.title == "Firefox video"
    assert inspection.snapshot.playback_status == "Playing"
    assert inspection.snapshot.maximum_rate is None
    assert "MaximumRate: unavailable" in inspection.snapshot.diagnostics[0]


def test_several_property_failures_do_not_erase_successful_fields() -> None:
    backend = FakeMprisBackend()
    bus_name = add_player(backend, "partial", title="Still useful")
    backend.property_diagnostics[(bus_name, MPRIS_PLAYER_INTERFACE)] = (
        "MaximumRate: unavailable (not supported)",
        "Volume: unavailable (malformed reply)",
        "CanSeek: unavailable (unknown property)",
    )

    inspection = MprisClient(backend).inspect_player("partial")

    assert inspection.succeeded
    assert inspection.snapshot is not None
    assert inspection.snapshot.metadata.title == "Still useful"
    assert len(inspection.snapshot.diagnostics) == 3


def test_failed_root_interface_does_not_erase_useful_player_interface() -> None:
    backend = FakeMprisBackend()
    bus_name = add_player(backend, "player-only", title="Readable")
    backend.get_all_failures[(bus_name, MPRIS_ROOT_INTERFACE)] = MprisBackendError(
        "root interface is incomplete"
    )

    inspection = MprisClient(backend).inspect_player("player-only")

    assert inspection.succeeded
    assert inspection.snapshot is not None
    assert inspection.snapshot.identity is None
    assert inspection.snapshot.metadata.title == "Readable"
    assert "root properties: unavailable" in inspection.snapshot.diagnostics[0]


def test_mixed_quality_enumeration_keeps_every_player_result() -> None:
    backend = FakeMprisBackend()
    add_player(backend, "healthy", title="Healthy")
    partial_bus = add_player(backend, "partial", title="Partial")
    backend.property_diagnostics[(partial_bus, MPRIS_PLAYER_INTERFACE)] = (
        "MaximumRate: unavailable (not supported)",
    )
    broken_bus = add_player(backend, "broken", title="Unreadable")
    backend.get_all_failures[(broken_bus, MPRIS_ROOT_INTERFACE)] = MprisBackendError(
        "root failed"
    )
    backend.get_all_failures[(broken_bus, MPRIS_PLAYER_INTERFACE)] = MprisBackendError(
        "player failed"
    )
    backend.get_property_failures[(broken_bus, MPRIS_PLAYER_INTERFACE, "Position")] = (
        MprisBackendError("position failed")
    )

    result = MprisClient(backend).list_players()

    assert result.error is None
    assert [item.service_name for item in result.players] == [
        "broken",
        "healthy",
        "partial",
    ]
    assert not result.players[0].succeeded
    assert result.players[1].succeeded
    assert result.players[2].succeeded


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
