"""Shared MPRIS event-to-clock session policy for every future frontend."""

from __future__ import annotations

from dataclasses import replace

from lyriflux.application.playback_clock import PlaybackClock
from lyriflux.application.ports import PlayerPositionSamplerPort
from lyriflux.domain.models import PlayerEvent, PlayerEventKind, PlayerSnapshot
from lyriflux.domain.synchronization import (
    ClockCorrectionClass,
    ClockUpdate,
    ClockUpdateKind,
    ObservationReason,
    PlaybackPositionEstimate,
    PlaybackState,
)

_REASON_PRIORITY = {
    ObservationReason.PERIODIC: 0,
    ObservationReason.INITIAL: 1,
    ObservationReason.RATE: 2,
    ObservationReason.STATUS: 3,
    ObservationReason.SEEK: 4,
    ObservationReason.TRACK: 5,
    ObservationReason.SUSPEND_RESUME: 6,
}


class PlaybackSyncSession:
    """Own one selected player's event priority and resynchronization context."""

    def __init__(
        self,
        clock: PlaybackClock,
        snapshot: PlayerSnapshot,
        session_id: str,
    ) -> None:
        if not session_id:
            raise ValueError("playback synchronization requires a session ID")
        self._clock = clock
        self._snapshot = snapshot
        self._session_id = session_id
        self._reason = ObservationReason.INITIAL
        self._track_dirty = False
        self._selection_dirty = False
        self._player_disappeared = False
        self._event_serial = 0
        self._generation = 1
        self._accept_position_events = True
        self._last_event_update: ClockUpdate | None = None

    @property
    def snapshot(self) -> PlayerSnapshot:
        """Return the latest signal/refreshed player context."""

        return self._snapshot

    @property
    def requires_reload(self) -> bool:
        """Return whether selection/lyrics must be resolved from fresh metadata."""

        return self._track_dirty or self._selection_dirty or self._player_disappeared

    @property
    def generation(self) -> int:
        """Monotonically identify selected source state for stale-result checks."""

        return self._generation

    @property
    def event_serial(self) -> int:
        """Identify whether a signal arrived across an external refresh."""

        return self._event_serial

    @property
    def last_event_update(self) -> ClockUpdate | None:
        """Expose an immediate signal-driven update to presentation subscribers."""

        return self._last_event_update

    @property
    def requested_reason(self) -> ObservationReason:
        """Expose pending sample urgency to the adaptive scheduler."""

        return self._reason

    def system_resumed(self) -> None:
        """Invalidate source and drift assumptions after detected suspend."""

        self._clock.mark_suspend_resume()
        self._track_dirty = True
        self._accept_position_events = False
        self._generation += 1
        self._event_serial += 1
        self._request_reason(ObservationReason.SUSPEND_RESUME)

    def invalidate_selection(self) -> None:
        """Request a fresh selected source before another clock observation."""

        self._selection_dirty = True
        self._event_serial += 1
        self._request_reason(ObservationReason.TRACK)

    def replace_source(self, snapshot: PlayerSnapshot, session_id: str) -> None:
        """Install refreshed source context and request a track reset if changed."""

        if not session_id:
            raise ValueError("playback synchronization requires a session ID")
        changed = session_id != self._session_id
        self._snapshot = snapshot
        self._session_id = session_id
        self._track_dirty = False
        self._selection_dirty = False
        self._player_disappeared = False
        self._accept_position_events = True
        if changed:
            self._generation += 1
            self._request_reason(ObservationReason.TRACK)

    def handle_event(self, event: PlayerEvent) -> bool:
        """Merge selected-player events and report whether sync state changed."""

        if event.service_name != self._snapshot.service_name:
            if event.kind in {
                PlayerEventKind.PLAYER_APPEARED,
                PlayerEventKind.PLAYER_DISAPPEARED,
                PlayerEventKind.PLAYBACK_STATUS_CHANGED,
                PlayerEventKind.METADATA_CHANGED,
                PlayerEventKind.PROPERTIES_CHANGED,
            }:
                self._selection_dirty = True
                self._event_serial += 1
                return True
            return False
        if event.kind is PlayerEventKind.PLAYER_APPEARED:
            self._selection_dirty = True
            self._event_serial += 1
            return True
        elif event.kind is PlayerEventKind.PLAYER_DISAPPEARED:
            self._player_disappeared = True
            self._accept_position_events = False
            self._generation += 1
            self._event_serial += 1
            return True
        elif event.kind is PlayerEventKind.METADATA_CHANGED:
            self._track_dirty = True
            self._accept_position_events = False
            self._generation += 1
            self._event_serial += 1
            return True
        elif event.kind is PlayerEventKind.PLAYBACK_STATUS_CHANGED:
            self._snapshot = replace(
                self._snapshot,
                playback_status=event.playback_status,
            )
            self._event_serial += 1
            self._last_event_update = self._clock.transition_state(
                PlaybackState.from_mpris(event.playback_status)
            )
            self._request_reason(ObservationReason.STATUS)
            return True
        elif event.kind is PlayerEventKind.SEEKED:
            if not self._accept_position_events:
                return False
            self._event_serial += 1
            if (
                event.position_us is not None
                and event.position_us >= 0
                and self._snapshot.rate is not None
                and self._snapshot.rate > 0
            ):
                try:
                    self._last_event_update = self._clock.reanchor_seek(
                        self._session_id,
                        event.position_us,
                        PlaybackState.from_mpris(self._snapshot.playback_status),
                        self._snapshot.rate,
                    )
                except ValueError:
                    self._last_event_update = None
            self._request_reason(ObservationReason.SEEK)
            return True
        elif (
            event.kind is PlayerEventKind.PROPERTIES_CHANGED
            and event.snapshot is not None
            and "Rate" in event.changed_properties
            and event.snapshot.rate is not None
        ):
            self._snapshot = replace(self._snapshot, rate=event.snapshot.rate)
            self._event_serial += 1
            try:
                self._last_event_update = self._clock.transition_rate(
                    event.snapshot.rate
                )
            except ValueError:
                self._last_event_update = None
            self._request_reason(ObservationReason.RATE)
            return True
        return False

    def sample(self, sampler: PlayerPositionSamplerPort) -> ClockUpdate:
        """Sample once without losing a higher-priority event received in-flight."""

        sample_reason = self._reason
        event_serial = self._event_serial
        observation = sampler.sample(
            self._snapshot,
            self._session_id,
            reason=sample_reason,
        )
        if event_serial != self._event_serial:
            return ClockUpdate(
                ClockUpdateKind.REJECTED_STALE,
                "an MPRIS event superseded the in-flight Position request",
                correction_class=ClockCorrectionClass.REJECTED,
            )
        update = self._clock.observe(observation)
        if self._reason is sample_reason:
            self._reason = ObservationReason.PERIODIC
        return update

    def estimate(self, *, duration_us: int | None) -> PlaybackPositionEstimate | None:
        """Return the clock estimate without exposing clock mutation to a frontend."""

        return self._clock.estimate(duration_us=duration_us)

    def _request_reason(self, requested: ObservationReason) -> None:
        if _REASON_PRIORITY[requested] >= _REASON_PRIORITY[self._reason]:
            self._reason = requested
