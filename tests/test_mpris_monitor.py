"""Signal-driven lifecycle and property-event tests."""

from lyriflux.domain.models import PlayerEvent, PlayerEventKind
from lyriflux.infrastructure.mpris.backend import MprisBackendError
from lyriflux.infrastructure.mpris.metadata_mapper import full_service_name
from lyriflux.infrastructure.mpris.player_registry import MprisMonitor
from tests.mpris_fakes import FakeMprisBackend


def test_watch_reports_lifecycle_properties_metadata_and_seek() -> None:
    existing = full_service_name("existing")
    backend = FakeMprisBackend([existing])
    monitor = MprisMonitor(backend)
    events: list[PlayerEvent] = []

    start = monitor.start(events.append)
    new_service = full_service_name("new-player")
    backend.emit_registered(new_service)
    backend.emit_properties(new_service, {"PlaybackStatus": "Paused"})
    backend.emit_properties(
        new_service,
        {
            "Metadata": {
                "xesam:title": "新しい曲",
                "xesam:artist": ["歌手"],
            }
        },
    )
    backend.emit_seeked(new_service, 123456)
    backend.emit_unregistered(new_service)

    assert start.current_services == ("existing",)
    assert [event.kind for event in events] == [
        PlayerEventKind.PLAYER_APPEARED,
        PlayerEventKind.PLAYBACK_STATUS_CHANGED,
        PlayerEventKind.METADATA_CHANGED,
        PlayerEventKind.SEEKED,
        PlayerEventKind.PLAYER_DISAPPEARED,
    ]
    assert events[1].playback_status == "Paused"
    assert events[2].metadata is not None
    assert events[2].metadata.title == "新しい曲"
    assert events[2].metadata.artists == ("歌手",)
    assert events[3].position_us == 123456

    monitor.close()


def test_combined_property_change_emits_both_meaningful_events() -> None:
    service = full_service_name("combined")
    backend = FakeMprisBackend([service])
    events: list[PlayerEvent] = []
    monitor = MprisMonitor(backend)
    monitor.start(events.append)

    backend.emit_properties(
        service,
        {
            "PlaybackStatus": "Playing",
            "Metadata": {"xesam:title": "Together", "xesam:artist": []},
        },
    )

    assert [event.kind for event in events] == [
        PlayerEventKind.PLAYBACK_STATUS_CHANGED,
        PlayerEventKind.METADATA_CHANGED,
    ]
    assert events[1].metadata is not None
    assert events[1].metadata.artists == ()


def test_other_root_and_player_property_changes_are_observed() -> None:
    service = full_service_name("property-player")
    backend = FakeMprisBackend([service])
    events: list[PlayerEvent] = []
    monitor = MprisMonitor(backend)
    monitor.start(events.append)

    backend.emit_properties(
        service,
        {"Rate": 1.25, "Volume": 0.75},
        invalidated=("CanSeek",),
    )
    backend.emit_properties(
        service,
        {"Identity": "Raw Player", "CanQuit": True},
        interface="org.mpris.MediaPlayer2",
    )

    assert [event.kind for event in events] == [
        PlayerEventKind.PROPERTIES_CHANGED,
        PlayerEventKind.PROPERTIES_CHANGED,
    ]
    player_event, root_event = events
    assert player_event.changed_properties == ("Rate", "Volume")
    assert player_event.invalidated_properties == ("CanSeek",)
    assert player_event.snapshot is not None
    assert player_event.snapshot.rate == 1.25
    assert player_event.snapshot.volume == 0.75
    assert root_event.snapshot is not None
    assert root_event.snapshot.identity == "Raw Player"
    assert root_event.snapshot.capabilities.can_quit is True


def test_malformed_signal_values_produce_diagnostics() -> None:
    service = full_service_name("malformed")
    backend = FakeMprisBackend([service])
    events: list[PlayerEvent] = []
    monitor = MprisMonitor(backend)
    monitor.start(events.append)

    backend.emit_properties(service, {"PlaybackStatus": 99})
    backend.emit_seeked(service, "late")
    backend.emit_properties(service, {}, decode_error="cannot decode QVariant")

    assert events[0].playback_status is None
    assert "expected string" in events[0].diagnostics[0]
    assert events[1].position_us is None
    assert "expected integer microseconds" in events[1].diagnostics[0]
    assert events[2].kind is PlayerEventKind.DIAGNOSTIC


def test_unrelated_interface_and_non_mpris_lifecycle_are_ignored() -> None:
    service = full_service_name("player")
    backend = FakeMprisBackend([service])
    events: list[PlayerEvent] = []
    monitor = MprisMonitor(backend)
    monitor.start(events.append)

    backend.emit_registered("org.example.NotAPlayer")
    backend.emit_properties(
        service,
        {"PlaybackStatus": "Stopped"},
        interface="org.example.Other",
    )
    backend.emit_unregistered("org.example.NotAPlayer")

    assert events == []


def test_service_disappearing_before_signal_attach_does_not_crash() -> None:
    backend = FakeMprisBackend()
    events: list[PlayerEvent] = []
    monitor = MprisMonitor(backend)
    monitor.start(events.append)
    service = full_service_name("too-fast")
    backend.subscribe_player_failures[service] = MprisBackendError("no owner")

    backend.emit_registered(service)
    backend.emit_unregistered(service)

    assert len(events) == 2
    assert events[0].kind is PlayerEventKind.PLAYER_APPEARED
    assert events[0].diagnostics == ("could not attach player signals: no owner",)
    assert events[1].kind is PlayerEventKind.PLAYER_DISAPPEARED


def test_monitor_start_failure_and_close_are_safe() -> None:
    backend = FakeMprisBackend()
    backend.list_failure = MprisBackendError("cannot list names")
    monitor = MprisMonitor(backend)

    start = monitor.start(lambda _event: None)
    monitor.close()
    monitor.close()

    assert start.error == "cannot list names"
