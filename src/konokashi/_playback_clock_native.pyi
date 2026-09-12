class Policy:
    def __init__(self, *values: int) -> None: ...

class Observation:
    def __init__(
        self,
        session_id: str,
        position_us: int,
        state: int,
        rate_ppb: int,
        request_started_ns: int,
        response_received_ns: int,
        reason: int,
        source: int,
        trusted: bool,
    ) -> None: ...

class Update:
    kind: int
    reason: str
    residual_us: int | None
    scheduled_correction_us: int
    correction_class: int

class Diagnostics:
    quality: int
    sample_count: int
    sample_age_us: int
    last_round_trip_us: int
    last_residual_us: int | None
    residual_jitter_us: int
    phase_error_remaining_us: int
    observed_error_bound_us: int
    drift_ppm: float | None
    drift_correction_active: bool
    health: int
    last_rtt_us: int
    minimum_recent_rtt_us: int
    median_recent_rtt_us: int
    p95_recent_rtt_us: int
    maximum_recent_rtt_us: int
    accepted_sample_count: int
    rejected_sample_count: int
    discontinuity_count: int
    last_sample_source: int | None
    median_absolute_residual_us: int
    p95_absolute_residual_us: int
    maximum_absolute_residual_us: int
    last_correction_class: int | None

class Estimate:
    session_id: str
    position_us: int
    state: int
    reported_rate_ppb: int
    effective_rate_ppb: int
    base_rate_ppb: int
    slew_remaining_us: int
    slew_remaining_duration_us: int
    monotonic_ns: int
    diagnostics: Diagnostics
    latest_authoritative_position_us: int | None

class PlaybackClockCore:
    def __init__(self, policy: Policy) -> None: ...
    @property
    def available(self) -> bool: ...
    def observe(self, observation: Observation) -> Update: ...
    def reanchor_seek(
        self,
        session_id: str,
        position_us: int,
        state: int,
        rate_ppb: int,
        now_ns: int,
        source: int,
    ) -> Update: ...
    def transition_state(self, state: int, now_ns: int) -> Update | None: ...
    def transition_rate(self, rate_ppb: int, now_ns: int) -> Update | None: ...
    def mark_suspend_resume(self, now_ns: int) -> None: ...
    def mark_sampling_failure(self, reason: str) -> Update: ...
    def estimate(self, now_ns: int, duration_us: int | None) -> Estimate | None: ...
