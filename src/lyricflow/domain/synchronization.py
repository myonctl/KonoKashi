"""Provider-neutral timing values for playback and lyric synchronization."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from math import isfinite


@dataclass(frozen=True, slots=True)
class LyricDocumentTiming:
    """Durable whole-document display delay without changing provider data.

    A positive value displays every provider transition later. The provider's
    canonical timestamps remain unchanged in ``LyricDocument``.
    """

    document_id: str
    lyrics_display_delay_us: int = 0

    def __post_init__(self) -> None:
        if not self.document_id.strip():
            raise ValueError("lyric document timing requires a document ID")


@dataclass(frozen=True, slots=True)
class OutputDeviceCalibration:
    """Durable residual delay for one privacy-safe stable output identity.

    A positive residual means audio is heard later than the automatic output
    latency estimate predicts, so it increases total output compensation.
    """

    device_key: str
    device_label: str
    residual_delay_us: int = 0

    def __post_init__(self) -> None:
        if not self.device_key.strip():
            raise ValueError("output calibration requires a stable device key")
        if not self.device_label.strip():
            raise ValueError("output calibration requires a device label")


class PlaybackState(Enum):
    """Playback states that affect media-clock progression."""

    PLAYING = "Playing"
    PAUSED = "Paused"
    STOPPED = "Stopped"
    UNKNOWN = "Unknown"

    @classmethod
    def from_mpris(cls, value: str | None) -> PlaybackState:
        """Map an MPRIS status without treating malformed data as playing."""

        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN


class ObservationReason(Enum):
    """Why a media-position observation was requested or received."""

    INITIAL = "initial snapshot"
    PERIODIC = "periodic correction"
    SEEK = "seek"
    STATUS = "playback status change"
    RATE = "playback rate change"
    TRACK = "track change"
    SUSPEND_RESUME = "system resume"


class PositionSampleSource(Enum):
    """Origin of authoritative playback-position evidence."""

    POSITION_PROPERTY = "MPRIS Position property"
    SEEKED_SIGNAL = "MPRIS Seeked signal"
    LOCAL_STATE_TRANSITION = "local state transition"


@dataclass(frozen=True, slots=True)
class PositionObservation:
    """One MPRIS position measurement bracketed by local monotonic time."""

    session_id: str
    position_us: int
    state: PlaybackState
    rate: float
    request_started_ns: int
    response_received_ns: int
    reason: ObservationReason = ObservationReason.PERIODIC
    source: PositionSampleSource = PositionSampleSource.POSITION_PROPERTY
    trusted: bool = True

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("position observation requires a session identity")
        if self.position_us < 0:
            raise ValueError("position observation cannot be negative")
        if self.request_started_ns < 0:
            raise ValueError("monotonic start cannot be negative")
        if self.response_received_ns < self.request_started_ns:
            raise ValueError("position response precedes its request")
        if not isfinite(self.rate) or self.rate <= 0:
            raise ValueError("playback rate must be finite and positive")
        if self.rate_ppb <= 0:
            raise ValueError("playback rate is below fixed-point resolution")

    @property
    def midpoint_ns(self) -> int:
        """Best local timestamp when remote sampling time is unspecified."""

        return (
            self.request_started_ns
            + (self.response_received_ns - self.request_started_ns) // 2
        )

    @property
    def half_round_trip_us(self) -> int:
        """Measured lower-bound uncertainty from request/response duration."""

        duration_ns = self.response_received_ns - self.request_started_ns
        return (duration_ns + 1_999) // 2_000

    @property
    def round_trip_us(self) -> int:
        """Full locally measured request/reply duration rounded upward."""

        return (self.response_received_ns - self.request_started_ns + 999) // 1_000

    @property
    def rate_ppb(self) -> int:
        """Canonical fixed-point rate in parts per billion.

        MPRIS transports Rate as a D-Bus double. Conversion through its decimal
        spelling happens once per observation; all trajectory math is integer.
        """

        try:
            decimal_rate = Decimal(str(self.rate))
        except InvalidOperation as error:  # pragma: no cover - validated above
            raise ValueError("playback rate is not representable") from error
        return int(
            (decimal_rate * Decimal(1_000_000_000)).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )


class ClockUpdateKind(Enum):
    """How one observation affected the playback clock."""

    INITIALIZED = "initialized"
    CORRECTED = "corrected"
    RESET = "reset"
    REJECTED_STALE = "rejected stale"
    REJECTED_OUTLIER = "rejected outlier"
    REJECTED_DUPLICATE = "rejected duplicate"


class ClockCorrectionClass(Enum):
    """Observable discipline decision for one authoritative sample."""

    INITIAL = "initial anchor"
    WITHIN_NOISE = "within measurement noise"
    PHASE_SLEW = "bounded phase slew"
    DISCONTINUITY = "discontinuity snap"
    REJECTED = "rejected sample"


@dataclass(frozen=True, slots=True)
class ClockUpdate:
    """Explain one accepted, reset, or rejected clock update."""

    kind: ClockUpdateKind
    reason: str
    residual_us: int | None = None
    scheduled_correction_us: int = 0
    correction_class: ClockCorrectionClass = ClockCorrectionClass.INITIAL


class ClockQuality(Enum):
    """Evidence level of the current interpolated playback estimate."""

    UNAVAILABLE = "unavailable"
    WARMING = "warming"
    STABLE = "stable"
    DEGRADED = "degraded"
    HELD = "held"


class ClockHealth(Enum):
    """Frontend-facing clock lifecycle, separate from numeric diagnostics."""

    LOCKED = "Locked"
    CONVERGING = "Converging"
    DEGRADED = "Degraded"
    STALE = "Stale"
    UNAVAILABLE = "Unavailable"
    DISCONTINUITY = "Discontinuity"
    PAUSED = "Paused"


@dataclass(frozen=True, slots=True)
class PlaybackClockDiagnostics:
    """Measured clock errors without implying a complete accuracy bound."""

    quality: ClockQuality
    sample_count: int
    sample_age_us: int
    last_round_trip_us: int
    last_residual_us: int | None
    residual_jitter_us: int
    phase_error_remaining_us: int
    observed_error_bound_us: int
    drift_ppm: float | None
    drift_correction_active: bool
    unquantified_error_sources: tuple[str, ...] = field(default_factory=tuple)
    health: ClockHealth = ClockHealth.CONVERGING
    last_rtt_us: int = 0
    minimum_recent_rtt_us: int = 0
    median_recent_rtt_us: int = 0
    p95_recent_rtt_us: int = 0
    maximum_recent_rtt_us: int = 0
    accepted_sample_count: int = 0
    rejected_sample_count: int = 0
    discontinuity_count: int = 0
    last_sample_source: PositionSampleSource | None = None
    median_absolute_residual_us: int = 0
    p95_absolute_residual_us: int = 0
    maximum_absolute_residual_us: int = 0
    last_correction_class: ClockCorrectionClass | None = None


@dataclass(frozen=True, slots=True)
class PlaybackPositionEstimate:
    """Current media position plus its evidence and uncertainty diagnostics."""

    session_id: str
    position_us: int
    state: PlaybackState
    reported_rate: float
    effective_rate: float
    base_rate: float
    slew_remaining_us: int
    slew_remaining_duration_us: int
    monotonic_ns: int
    diagnostics: PlaybackClockDiagnostics
    base_rate_ppb: int = 1_000_000_000
    effective_rate_ppb: int = 1_000_000_000
    latest_authoritative_position_us: int | None = None


class AudioLatencyQuality(Enum):
    """How safely an audio latency value can affect audible position."""

    EXACT = "exact reported value"
    RANGE = "reported range"
    DIAGNOSTIC_ONLY = "diagnostic only"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class AudioOutputLatency:
    """Audio path delay kept separate from the MPRIS media clock."""

    estimate_us: int | None
    uncertainty_us: int | None
    source: str
    route: str | None = None
    measured_at_ns: int | None = None
    limitations: tuple[str, ...] = field(default_factory=tuple)
    residual_calibration_us: int = 0
    device_key: str | None = None
    minimum_us: int | None = None
    maximum_us: int | None = None
    quality: AudioLatencyQuality = AudioLatencyQuality.UNAVAILABLE

    def __post_init__(self) -> None:
        if self.estimate_us is not None and self.estimate_us < 0:
            raise ValueError("audio-output latency cannot be negative")
        if self.uncertainty_us is not None and self.uncertainty_us < 0:
            raise ValueError("audio-output uncertainty cannot be negative")
        if not self.source.strip():
            raise ValueError("audio-output latency requires a source label")
        if self.minimum_us is not None and self.minimum_us < 0:
            raise ValueError("minimum audio-output latency cannot be negative")
        if self.maximum_us is not None and self.maximum_us < 0:
            raise ValueError("maximum audio-output latency cannot be negative")
        if (
            self.minimum_us is not None
            and self.maximum_us is not None
            and self.maximum_us < self.minimum_us
        ):
            raise ValueError("audio-output latency range is reversed")

    @property
    def total_compensation_us(self) -> int | None:
        """Compose automatic/manual estimate and exact-device residual."""

        if self.estimate_us is None:
            return None
        return max(0, self.estimate_us + self.residual_calibration_us)


@dataclass(frozen=True, slots=True)
class LineTimingCalibration:
    """One line-specific timestamp correction independent of document shift."""

    line_id: str
    shift_us: int
    uncertainty_us: int | None = None
    source: str = "manual line calibration"

    def __post_init__(self) -> None:
        if not self.line_id:
            raise ValueError("line timing calibration requires a line ID")
        if self.uncertainty_us is not None and self.uncertainty_us < 0:
            raise ValueError("line timing uncertainty cannot be negative")
        if not self.source.strip():
            raise ValueError("line timing calibration requires a source label")


@dataclass(frozen=True, slots=True)
class LyricTimingCalibration:
    """Document and line timing corrections, independent of playback error."""

    shift_us: int = 0
    provider_uncertainty_us: int | None = None
    source: str = "provider timestamps; no user calibration"
    limitations: tuple[str, ...] = field(default_factory=tuple)
    line_adjustments: tuple[LineTimingCalibration, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if (
            self.provider_uncertainty_us is not None
            and self.provider_uncertainty_us < 0
        ):
            raise ValueError("provider timing uncertainty cannot be negative")
        line_ids = [item.line_id for item in self.line_adjustments]
        if len(line_ids) != len(set(line_ids)):
            raise ValueError("line timing calibrations require unique line IDs")

    def line_shift_us(self, line_id: str) -> int:
        """Return a line correction or leave its provider timestamp unchanged."""

        return next(
            (
                adjustment.shift_us
                for adjustment in self.line_adjustments
                if adjustment.line_id == line_id
            ),
            0,
        )


@dataclass(frozen=True, slots=True)
class PresentationLatency:
    """Frontend render delay compensated in deadlines, never in media position."""

    estimate_us: int = 0
    uncertainty_us: int | None = None
    surface: str = "diagnostic-terminal"

    def __post_init__(self) -> None:
        if self.estimate_us < 0:
            raise ValueError("presentation latency cannot be negative")
        if self.uncertainty_us is not None and self.uncertainty_us < 0:
            raise ValueError("presentation uncertainty cannot be negative")
        if not self.surface.strip():
            raise ValueError("presentation latency requires a surface identity")


@dataclass(frozen=True, slots=True)
class SynchronizationCalibration:
    """Explicitly separated terms used to map media time to displayed lyrics."""

    audio_output: AudioOutputLatency = AudioOutputLatency(
        None,
        None,
        "unknown; compensation unavailable",
        limitations=("actual output path latency is unmeasured",),
    )
    lyrics: LyricTimingCalibration = LyricTimingCalibration()
    presentation: PresentationLatency = PresentationLatency()


class AudioLatencyProbeStatus(Enum):
    """Whether a system audio graph supplied useful latency evidence."""

    REPORTED = "reported"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class AudioLatencyProbeResult:
    """Read-only graph evidence that is not automatically a calibration."""

    status: AudioLatencyProbeStatus
    route: str | None = None
    minimum_us: int | None = None
    maximum_us: int | None = None
    graph_quantum_us: int | None = None
    safe_for_automatic_compensation: bool = False
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    device_key: str | None = None
    measured_at_ns: int | None = None
    source: str = "PipeWire SPA Latency"

    @property
    def chosen_estimate_us(self) -> int | None:
        """Return the integer midpoint while preserving the reported range."""

        if self.minimum_us is None or self.maximum_us is None:
            return None
        return self.minimum_us + (self.maximum_us - self.minimum_us) // 2

    @property
    def uncertainty_us(self) -> int | None:
        """Return half-range engineering uncertainty, rounded upward."""

        if self.minimum_us is None or self.maximum_us is None:
            return None
        return (self.maximum_us - self.minimum_us + 1) // 2
