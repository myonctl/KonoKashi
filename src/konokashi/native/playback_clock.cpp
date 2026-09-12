#if defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wconversion"
#pragma GCC diagnostic ignored "-Wshadow"
#pragma GCC diagnostic ignored "-Wsign-conversion"
#endif
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#if defined(__GNUC__)
#pragma GCC diagnostic pop
#endif

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <deque>
#include <limits>
#include <optional>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace py = pybind11;

namespace {

constexpr std::int64_t kRateScale = 1'000'000'000;
constexpr std::int64_t kNsRateDenominator = 1'000 * kRateScale;

enum State : int { Playing = 0, Paused = 1, Stopped = 2, Unknown = 3 };
enum Reason : int {
    Initial = 0,
    Periodic = 1,
    Seek = 2,
    Status = 3,
    Rate = 4,
    Track = 5,
    SuspendResume = 6,
};
enum UpdateKind : int {
    Initialized = 0,
    Corrected = 1,
    Reset = 2,
    RejectedStale = 3,
    RejectedOutlier = 4,
    RejectedDuplicate = 5,
};
enum CorrectionClass : int {
    CorrectionInitial = 0,
    WithinNoise = 1,
    PhaseSlew = 2,
    Discontinuity = 3,
    Rejected = 4,
};
enum Quality : int {
    QualityUnavailable = 0,
    Warming = 1,
    Stable = 2,
    QualityDegraded = 3,
    Held = 4,
};
enum Health : int {
    Locked = 0,
    Converging = 1,
    HealthDegraded = 2,
    Stale = 3,
    HealthUnavailable = 4,
    HealthDiscontinuity = 5,
    HealthPaused = 6,
};

std::int64_t round_ratio(__int128 numerator, __int128 denominator) {
    if (denominator <= 0) {
        throw std::invalid_argument("rounding denominator must be positive");
    }
    if (numerator >= 0) {
        return static_cast<std::int64_t>((numerator + denominator / 2) / denominator);
    }
    return -static_cast<std::int64_t>((-numerator + denominator / 2) / denominator);
}

std::int64_t rounded_median(std::vector<std::int64_t> values) {
    if (values.empty()) {
        return 0;
    }
    std::sort(values.begin(), values.end());
    const auto middle = values.size() / 2;
    if (values.size() % 2 == 1) {
        return values[middle];
    }
    const __int128 sum = static_cast<__int128>(values[middle - 1]) + values[middle];
    const auto truncated = static_cast<std::int64_t>(sum / 2);
    if (sum % 2 == 0 || truncated % 2 == 0) {
        return truncated;
    }
    return truncated + (sum > 0 ? 1 : -1);
}

std::int64_t percentile(std::vector<std::int64_t> values, int percentage) {
    if (values.empty()) {
        return 0;
    }
    std::sort(values.begin(), values.end());
    const auto rank = std::max<std::size_t>(
        1, (values.size() * static_cast<std::size_t>(percentage) + 99) / 100);
    return values[std::min(values.size(), rank) - 1];
}

unsigned __int128 integer_sqrt(unsigned __int128 value) {
    unsigned __int128 result = 0;
    unsigned __int128 bit = static_cast<unsigned __int128>(1) << 126;
    while (bit > value) {
        bit >>= 2;
    }
    while (bit != 0) {
        if (value >= result + bit) {
            value -= result + bit;
            result = (result >> 1) + bit;
        } else {
            result >>= 1;
        }
        bit >>= 2;
    }
    return result;
}

struct Policy {
    std::int64_t discontinuity_us;
    std::int64_t tiny_phase_floor_us;
    std::int64_t maximum_phase_step_us;
    std::int64_t phase_correction_horizon_us;
    std::int64_t drift_min_samples;
    std::int64_t drift_min_span_us;
    std::int64_t drift_pair_min_span_us;
    std::int64_t drift_max_abs_ppm;
    std::int64_t drift_max_residual_p95_us;
    std::int64_t warming_drift_allowance_ppm;
    std::int64_t rtt_quality_min_samples;
    std::int64_t slow_rtt_absolute_us;
    std::int64_t slow_rtt_median_multiplier;
    std::int64_t slow_rtt_minimum_multiplier;
    std::int64_t degraded_after_us;
    std::int64_t stale_after_us;
    std::int64_t history_size;
};

struct Observation {
    std::string session_id;
    std::int64_t position_us;
    int state;
    std::int64_t rate_ppb;
    std::int64_t request_started_ns;
    std::int64_t response_received_ns;
    int reason;
    int source;
    bool trusted;

    [[nodiscard]] std::int64_t midpoint_ns() const {
        return request_started_ns + (response_received_ns - request_started_ns) / 2;
    }

    [[nodiscard]] std::int64_t half_round_trip_us() const {
        return (response_received_ns - request_started_ns + 1'999) / 2'000;
    }

    [[nodiscard]] std::int64_t round_trip_us() const {
        return (response_received_ns - request_started_ns + 999) / 1'000;
    }
};

struct Update {
    int kind;
    std::string reason;
    std::optional<std::int64_t> residual_us;
    std::int64_t scheduled_correction_us = 0;
    int correction_class = CorrectionInitial;
};

struct DriftFit {
    std::int64_t drift_ppm;
    std::int64_t effective_rate_ppb;
    std::int64_t residual_p95_us;
    std::int64_t residual_rms_us;
    bool trusted;
};

struct Diagnostics {
    int quality;
    std::int64_t sample_count;
    std::int64_t sample_age_us;
    std::int64_t last_round_trip_us;
    std::optional<std::int64_t> last_residual_us;
    std::int64_t residual_jitter_us;
    std::int64_t phase_error_remaining_us;
    std::int64_t observed_error_bound_us;
    std::optional<double> drift_ppm;
    bool drift_correction_active;
    int health;
    std::int64_t last_rtt_us;
    std::int64_t minimum_recent_rtt_us;
    std::int64_t median_recent_rtt_us;
    std::int64_t p95_recent_rtt_us;
    std::int64_t maximum_recent_rtt_us;
    std::int64_t accepted_sample_count;
    std::int64_t rejected_sample_count;
    std::int64_t discontinuity_count;
    std::optional<int> last_sample_source;
    std::int64_t median_absolute_residual_us;
    std::int64_t p95_absolute_residual_us;
    std::int64_t maximum_absolute_residual_us;
    std::optional<int> last_correction_class;
};

struct Estimate {
    std::string session_id;
    std::int64_t position_us;
    int state;
    std::int64_t reported_rate_ppb;
    std::int64_t effective_rate_ppb;
    std::int64_t base_rate_ppb;
    std::int64_t slew_remaining_us;
    std::int64_t slew_remaining_duration_us;
    std::int64_t monotonic_ns;
    Diagnostics diagnostics;
    std::optional<std::int64_t> latest_authoritative_position_us;
};

class PlaybackClockCore {
  public:
    explicit PlaybackClockCore(Policy policy) : policy_(policy) {}

    [[nodiscard]] bool available() const { return session_id_.has_value(); }

    Update observe(const Observation& observation) {
        if (available() && observation.response_received_ns < latest_response_ns_) {
            return reject(RejectedStale,
                          "observation response predates the newest accepted response");
        }
        if (available() && observation.response_received_ns == latest_response_ns_) {
            return reject(
                RejectedDuplicate,
                "observation duplicates the newest accepted response timestamp");
        }

        const bool new_source_series =
            available() && (observation.session_id != *session_id_ ||
                            observation.reason == Track);
        if (new_source_series) {
            rtts_.clear();
            accepted_count_ = 0;
            rejected_count_ = 0;
            discontinuity_count_ = 0;
        }
        append_bounded(rtts_, observation.round_trip_us());
        if (!observation.trusted) {
            return reject(RejectedOutlier,
                          "observation source marked the sample untrusted");
        }
        if (available() && is_slow_rtt(observation.round_trip_us())) {
            return reject(RejectedOutlier,
                          "request latency is an outlier relative to recent samples");
        }

        const bool session_changed =
            !session_id_.has_value() || observation.session_id != *session_id_;
        const bool confirms_pending_event =
            available() && !session_changed && discontinuity_pending_ &&
            (observation.reason == Seek || observation.reason == SuspendResume);
        const bool explicit_discontinuity =
            !confirms_pending_event &&
            (observation.reason == Seek || observation.reason == Track ||
             observation.reason == SuspendResume);
        const bool state_changed = available() && observation.state != state_;
        const bool rate_changed =
            available() && observation.rate_ppb != reported_rate_ppb_;
        const bool lifecycle_reanchor =
            observation.reason == Status || observation.reason == Rate ||
            confirms_pending_event;
        if (!available() || session_changed || explicit_discontinuity || state_changed ||
            rate_changed || lifecycle_reanchor) {
            const std::string reason = reset_reason(
                observation, session_changed, state_changed, rate_changed);
            const std::optional<std::int64_t> residual =
                !available()
                    ? std::nullopt
                    : std::optional<std::int64_t>(
                          observation.position_us - position_at(observation.midpoint_ns()));
            const bool first = !available();
            reset(observation, explicit_discontinuity);
            const int correction = first ? CorrectionInitial
                                         : (explicit_discontinuity ? Discontinuity
                                                                   : WithinNoise);
            last_correction_class_ = correction;
            return {first ? Initialized : Reset, reason, residual, 0, correction};
        }

        const auto midpoint_ns = observation.midpoint_ns();
        const auto predicted = position_at(midpoint_ns);
        const auto residual = observation.position_us - predicted;
        if (std::abs(residual) >= policy_.discontinuity_us) {
            reset(observation, true);
            last_correction_class_ = Discontinuity;
            return {Reset,
                    "unannounced position discontinuity treated as a seek",
                    residual,
                    0,
                    Discontinuity};
        }

        accept_metadata(observation, residual);
        if (observation.state == Playing) {
            append_bounded(samples_,
                           std::pair(observation.midpoint_ns(), observation.position_us));
            fit_ = fit_drift();
        }
        const auto fitted_rate = fit_.has_value() && fit_->trusted
                                     ? fit_->effective_rate_ppb
                                     : observation.rate_ppb;
        const auto noise_threshold =
            std::max(policy_.tiny_phase_floor_us,
                     observation.half_round_trip_us() + residual_jitter());
        anchor_position_us_ = predicted;
        anchor_ns_ = midpoint_ns;
        base_rate_ppb_ = fitted_rate;
        reported_rate_ppb_ = observation.rate_ppb;
        state_ = observation.state;
        phase_residual_us_ = residual;
        if (std::abs(residual) <= noise_threshold) {
            phase_correction_us_ = 0;
            converging_ = !fit_.has_value() || !fit_->trusted;
            last_correction_class_ = WithinNoise;
            return {Corrected,
                    "phase residual is within measured noise; anchor retained",
                    residual,
                    0,
                    WithinNoise};
        }

        auto correction = std::clamp(residual, -policy_.maximum_phase_step_us,
                                     policy_.maximum_phase_step_us);
        const auto minimum_monotonic_correction = -round_ratio(
            static_cast<__int128>(base_rate_ppb_) *
                policy_.phase_correction_horizon_us,
            2 * static_cast<__int128>(kRateScale));
        correction = std::max(minimum_monotonic_correction, correction);
        phase_correction_us_ = correction;
        converging_ = true;
        last_correction_class_ = PhaseSlew;
        return {Corrected,
                "genuine phase residual scheduled as a bounded monotonic slew",
                residual,
                correction,
                PhaseSlew};
    }

    Update reanchor_seek(const std::string& session_id, std::int64_t position_us,
                         int state, std::int64_t rate_ppb, std::int64_t now_ns,
                         int source) {
        const Observation observation{session_id, position_us, state, rate_ppb, now_ns,
                                      now_ns, Seek, source, true};
        const std::optional<std::int64_t> residual =
            !available() ? std::nullopt
                         : std::optional<std::int64_t>(position_us - position_at(now_ns));
        const bool first = !available();
        reset(observation, true);
        last_correction_class_ = Discontinuity;
        return {first ? Initialized : Reset,
                "seek signal immediately replaced interpolation state",
                residual,
                0,
                Discontinuity};
    }

    std::optional<Update> transition_state(int state, std::int64_t now_ns) {
        if (!available() || state == state_) {
            return std::nullopt;
        }
        anchor_position_us_ = position_at(now_ns);
        anchor_ns_ = now_ns;
        state_ = state;
        clear_discipline_window();
        converging_ = true;
        last_correction_class_ = WithinNoise;
        return Update{Reset,
                      "playback state changed at local signal receipt; re-anchor requested",
                      std::nullopt,
                      0,
                      WithinNoise};
    }

    std::optional<Update> transition_rate(std::int64_t rate_ppb,
                                          std::int64_t now_ns) {
        if (!available() || rate_ppb == reported_rate_ppb_) {
            return std::nullopt;
        }
        anchor_position_us_ = position_at(now_ns);
        anchor_ns_ = now_ns;
        reported_rate_ppb_ = rate_ppb;
        base_rate_ppb_ = rate_ppb;
        clear_discipline_window();
        converging_ = true;
        last_correction_class_ = WithinNoise;
        return Update{Reset,
                      "playback rate changed without a phase discontinuity",
                      std::nullopt,
                      0,
                      WithinNoise};
    }

    void mark_suspend_resume(std::int64_t now_ns) {
        if (!available()) {
            return;
        }
        anchor_position_us_ = position_at(now_ns);
        anchor_ns_ = now_ns;
        clear_discipline_window();
        converging_ = true;
        discontinuity_pending_ = true;
        ++discontinuity_count_;
        last_correction_class_ = Discontinuity;
    }

    Update mark_sampling_failure(const std::string& reason) {
        return reject(RejectedOutlier, reason);
    }

    std::optional<Estimate> estimate(std::int64_t current_ns,
                                     std::optional<std::int64_t> duration_us) {
        if (!available()) {
            return std::nullopt;
        }
        auto position = position_at(current_ns);
        const auto effective_rate_ppb = rate_at(current_ns);
        const auto [slew_remaining_us, slew_remaining_duration_us] =
            slew_remaining_at(current_ns);
        if (duration_us.has_value()) {
            position = std::min(position, std::max<std::int64_t>(0, *duration_us));
        }
        position = std::max<std::int64_t>(0, position);
        const auto age_us = std::max<std::int64_t>(0, (current_ns - last_sample_ns_) / 1'000);
        const auto jitter_us = residual_jitter();
        const auto health = health_at(age_us);
        const auto quality = quality_at(health);
        const auto drift_growth_us = fit_.has_value() && fit_->trusted
                                         ? 0
                                         : age_us * policy_.warming_drift_allowance_ppm /
                                               1'000'000;
        const auto observed_bound = last_half_rtt_us_ + jitter_us +
                                    std::abs(phase_remaining_at(current_ns)) +
                                    drift_growth_us;
        std::vector<std::int64_t> rtts(rtts_.begin(), rtts_.end());
        std::vector<std::int64_t> absolute_residuals;
        absolute_residuals.reserve(residuals_.size());
        for (const auto value : residuals_) {
            absolute_residuals.push_back(std::abs(value));
        }
        const auto rtt_min = rtts.empty() ? 0 : *std::min_element(rtts.begin(), rtts.end());
        const auto rtt_max = rtts.empty() ? 0 : *std::max_element(rtts.begin(), rtts.end());
        const auto residual_max = absolute_residuals.empty()
                                      ? 0
                                      : *std::max_element(absolute_residuals.begin(),
                                                          absolute_residuals.end());
        Diagnostics diagnostics{
            quality,
            static_cast<std::int64_t>(samples_.size()),
            age_us,
            last_half_rtt_us_,
            last_residual_us_,
            jitter_us,
            phase_remaining_at(current_ns),
            observed_bound,
            fit_.has_value() ? std::optional<double>(fit_->drift_ppm) : std::nullopt,
            fit_.has_value() && fit_->trusted &&
                fit_->effective_rate_ppb != reported_rate_ppb_,
            health,
            last_rtt_us_,
            rtt_min,
            rounded_median(rtts),
            percentile(rtts, 95),
            rtt_max,
            accepted_count_,
            rejected_count_,
            discontinuity_count_,
            last_source_,
            rounded_median(absolute_residuals),
            percentile(absolute_residuals, 95),
            residual_max,
            last_correction_class_,
        };
        return Estimate{*session_id_,
                        position,
                        state_,
                        reported_rate_ppb_,
                        effective_rate_ppb,
                        base_rate_ppb_,
                        slew_remaining_us,
                        slew_remaining_duration_us,
                        current_ns,
                        diagnostics,
                        last_observed_position_us_};
    }

  private:
    template <typename T>
    void append_bounded(std::deque<T>& target, T value) {
        target.push_back(std::move(value));
        while (target.size() > static_cast<std::size_t>(policy_.history_size)) {
            target.pop_front();
        }
    }

    [[nodiscard]] std::string reset_reason(const Observation& observation,
                                           bool session_changed, bool state_changed,
                                           bool rate_changed) const {
        if (!available()) {
            return "first position observation";
        }
        if (session_changed) {
            return "track/session identity changed";
        }
        if (state_changed) {
            return "playback state changed";
        }
        if (rate_changed) {
            return "playback rate changed without a phase discontinuity";
        }
        static const std::vector<std::string> reasons{
            "initial snapshot",          "periodic correction", "seek",
            "playback status change",    "playback rate change", "track change",
            "system resume",
        };
        return reasons.at(static_cast<std::size_t>(observation.reason));
    }

    void reset(const Observation& observation, bool discontinuity) {
        session_id_ = observation.session_id;
        state_ = observation.state;
        reported_rate_ppb_ = observation.rate_ppb;
        base_rate_ppb_ = observation.rate_ppb;
        anchor_position_us_ = observation.position_us;
        anchor_ns_ = observation.midpoint_ns();
        latest_response_ns_ = observation.response_received_ns;
        last_sample_ns_ = observation.midpoint_ns();
        last_half_rtt_us_ = observation.half_round_trip_us();
        last_rtt_us_ = observation.round_trip_us();
        last_residual_us_.reset();
        last_observed_position_us_ = observation.position_us;
        last_source_ = observation.source;
        clear_discipline_window();
        ++accepted_count_;
        degraded_by_rejection_ = false;
        converging_ = true;
        discontinuity_pending_ = discontinuity;
        if (discontinuity) {
            ++discontinuity_count_;
        }
        if (observation.state == Playing) {
            append_bounded(samples_,
                           std::pair(observation.midpoint_ns(), observation.position_us));
        }
    }

    void accept_metadata(const Observation& observation, std::int64_t residual_us) {
        latest_response_ns_ = observation.response_received_ns;
        last_sample_ns_ = observation.midpoint_ns();
        last_half_rtt_us_ = observation.half_round_trip_us();
        last_rtt_us_ = observation.round_trip_us();
        last_residual_us_ = residual_us;
        last_observed_position_us_ = observation.position_us;
        last_source_ = observation.source;
        append_bounded(residuals_, residual_us);
        ++accepted_count_;
        degraded_by_rejection_ = false;
        discontinuity_pending_ = false;
    }

    Update reject(int kind, const std::string& reason) {
        ++rejected_count_;
        degraded_by_rejection_ = true;
        last_correction_class_ = Rejected;
        return {kind, reason, std::nullopt, 0, Rejected};
    }

    [[nodiscard]] bool is_slow_rtt(std::int64_t rtt_us) const {
        if (rtts_.size() < static_cast<std::size_t>(policy_.rtt_quality_min_samples)) {
            return false;
        }
        std::vector<std::int64_t> prior(rtts_.begin(), rtts_.end() - 1);
        if (prior.empty()) {
            return false;
        }
        const auto threshold = std::max(
            {policy_.slow_rtt_absolute_us,
             rounded_median(prior) * policy_.slow_rtt_median_multiplier,
             *std::min_element(prior.begin(), prior.end()) *
                 policy_.slow_rtt_minimum_multiplier});
        return rtt_us > threshold;
    }

    void clear_discipline_window() {
        phase_residual_us_ = 0;
        phase_correction_us_ = 0;
        samples_.clear();
        residuals_.clear();
        fit_.reset();
    }

    [[nodiscard]] std::int64_t position_at(std::int64_t monotonic_ns) const {
        if (state_ != Playing) {
            return anchor_position_us_;
        }
        const auto elapsed_ns = std::max<std::int64_t>(0, monotonic_ns - anchor_ns_);
        const auto base_progress_us = round_ratio(
            static_cast<__int128>(elapsed_ns) * base_rate_ppb_, kNsRateDenominator);
        const auto horizon_ns = policy_.phase_correction_horizon_us * 1'000;
        const auto correction_progress_us = round_ratio(
            static_cast<__int128>(phase_correction_us_) *
                std::min(elapsed_ns, horizon_ns),
            horizon_ns);
        return anchor_position_us_ + base_progress_us + correction_progress_us;
    }

    [[nodiscard]] std::int64_t rate_at(std::int64_t monotonic_ns) const {
        if (state_ != Playing) {
            return base_rate_ppb_;
        }
        const auto elapsed_ns = std::max<std::int64_t>(0, monotonic_ns - anchor_ns_);
        const auto horizon_ns = policy_.phase_correction_horizon_us * 1'000;
        if (elapsed_ns >= horizon_ns) {
            return base_rate_ppb_;
        }
        const auto correction_rate_ppb = round_ratio(
            static_cast<__int128>(phase_correction_us_) * kRateScale,
            policy_.phase_correction_horizon_us);
        return base_rate_ppb_ + correction_rate_ppb;
    }

    [[nodiscard]] std::int64_t phase_remaining_at(std::int64_t monotonic_ns) const {
        if (state_ != Playing) {
            return phase_residual_us_;
        }
        const auto elapsed_ns = std::max<std::int64_t>(0, monotonic_ns - anchor_ns_);
        const auto horizon_ns = policy_.phase_correction_horizon_us * 1'000;
        const auto completed_us = round_ratio(
            static_cast<__int128>(phase_correction_us_) *
                std::min(elapsed_ns, horizon_ns),
            horizon_ns);
        return phase_residual_us_ - completed_us;
    }

    [[nodiscard]] std::pair<std::int64_t, std::int64_t>
    slew_remaining_at(std::int64_t monotonic_ns) const {
        if (state_ != Playing) {
            return {0, 0};
        }
        const auto elapsed_us = std::max<std::int64_t>(0, monotonic_ns - anchor_ns_) /
                                1'000;
        const auto remaining_duration_us = std::max<std::int64_t>(
            0, policy_.phase_correction_horizon_us - elapsed_us);
        const auto remaining_us = round_ratio(
            static_cast<__int128>(phase_correction_us_) * remaining_duration_us,
            policy_.phase_correction_horizon_us);
        return {remaining_us, remaining_duration_us};
    }

    [[nodiscard]] std::int64_t residual_jitter() const {
        if (residuals_.empty()) {
            return 0;
        }
        std::vector<std::int64_t> values(residuals_.begin(), residuals_.end());
        const auto center = rounded_median(values);
        std::vector<std::int64_t> deviations;
        deviations.reserve(values.size());
        for (const auto value : values) {
            deviations.push_back(std::abs(value - center));
        }
        return percentile(deviations, 95);
    }

    [[nodiscard]] std::optional<DriftFit> fit_drift() const {
        if (samples_.size() < static_cast<std::size_t>(policy_.drift_min_samples)) {
            return std::nullopt;
        }
        const auto first_ns = samples_.front().first;
        const auto span_us = (samples_.back().first - first_ns) / 1'000;
        if (span_us < policy_.drift_min_span_us) {
            return std::nullopt;
        }
        std::vector<std::int64_t> slopes;
        for (std::size_t left = 0; left < samples_.size(); ++left) {
            for (std::size_t right = left + 1; right < samples_.size(); ++right) {
                const auto delta_ns = samples_[right].first - samples_[left].first;
                if (delta_ns < policy_.drift_pair_min_span_us * 1'000) {
                    continue;
                }
                const auto delta_position =
                    samples_[right].second - samples_[left].second;
                if (delta_position <= 0) {
                    continue;
                }
                slopes.push_back(round_ratio(
                    static_cast<__int128>(delta_position) * kNsRateDenominator,
                    delta_ns));
            }
        }
        if (slopes.empty()) {
            return std::nullopt;
        }
        const auto slope_ppb = rounded_median(slopes);
        std::vector<std::int64_t> intercepts;
        for (const auto& [timestamp_ns, position] : samples_) {
            intercepts.push_back(position - round_ratio(
                static_cast<__int128>(timestamp_ns - first_ns) * slope_ppb,
                kNsRateDenominator));
        }
        const auto intercept = rounded_median(intercepts);
        std::vector<std::int64_t> residuals;
        unsigned __int128 sum_squares = 0;
        for (const auto& [timestamp_ns, position] : samples_) {
            const auto residual = position -
                                  (intercept + round_ratio(
                                                   static_cast<__int128>(timestamp_ns -
                                                                         first_ns) *
                                                       slope_ppb,
                                                   kNsRateDenominator));
            residuals.push_back(std::abs(residual));
            const __int128 signed_value = residual;
            sum_squares += static_cast<unsigned __int128>(signed_value * signed_value);
        }
        const auto residual_p95 = percentile(residuals, 95);
        const auto residual_rms = static_cast<std::int64_t>(
            integer_sqrt(sum_squares / residuals.size()));
        const auto drift_ppm = round_ratio(
            static_cast<__int128>(slope_ppb - reported_rate_ppb_) * 1'000'000,
            reported_rate_ppb_);
        const bool trusted = std::abs(drift_ppm) <= policy_.drift_max_abs_ppm &&
                             residual_p95 <= policy_.drift_max_residual_p95_us;
        return DriftFit{drift_ppm, slope_ppb, residual_p95, residual_rms, trusted};
    }

    int health_at(std::int64_t age_us) {
        if (state_ == Paused) {
            return HealthPaused;
        }
        if (state_ == Stopped || state_ == Unknown) {
            return HealthUnavailable;
        }
        if (age_us >= policy_.stale_after_us) {
            return Stale;
        }
        if (discontinuity_pending_) {
            return HealthDiscontinuity;
        }
        if (age_us >= policy_.degraded_after_us || degraded_by_rejection_) {
            return HealthDegraded;
        }
        if (fit_.has_value() && fit_->trusted) {
            converging_ = false;
            return Locked;
        }
        if (fit_.has_value() && !fit_->trusted) {
            return HealthDegraded;
        }
        return Converging;
    }

    [[nodiscard]] int quality_at(int health) const {
        if (health == Locked) {
            return Stable;
        }
        if (health == HealthDegraded || health == Stale) {
            return QualityDegraded;
        }
        if (health == HealthDiscontinuity) {
            return Warming;
        }
        if (health == HealthPaused) {
            return Held;
        }
        if (health == HealthUnavailable) {
            return QualityUnavailable;
        }
        if (fit_.has_value() && !fit_->trusted) {
            return QualityDegraded;
        }
        return Warming;
    }

    Policy policy_;
    std::optional<std::string> session_id_;
    int state_ = Unknown;
    std::int64_t reported_rate_ppb_ = kRateScale;
    std::int64_t base_rate_ppb_ = kRateScale;
    std::int64_t anchor_position_us_ = 0;
    std::int64_t anchor_ns_ = 0;
    std::int64_t latest_response_ns_ = -1;
    std::int64_t last_sample_ns_ = 0;
    std::int64_t last_half_rtt_us_ = 0;
    std::int64_t last_rtt_us_ = 0;
    std::optional<std::int64_t> last_residual_us_;
    std::optional<std::int64_t> last_observed_position_us_;
    std::optional<int> last_source_;
    std::optional<int> last_correction_class_;
    std::int64_t phase_residual_us_ = 0;
    std::int64_t phase_correction_us_ = 0;
    std::deque<std::pair<std::int64_t, std::int64_t>> samples_;
    std::deque<std::int64_t> residuals_;
    std::deque<std::int64_t> rtts_;
    std::optional<DriftFit> fit_;
    std::int64_t accepted_count_ = 0;
    std::int64_t rejected_count_ = 0;
    std::int64_t discontinuity_count_ = 0;
    bool converging_ = true;
    bool degraded_by_rejection_ = false;
    bool discontinuity_pending_ = false;
};

}  // namespace

PYBIND11_MODULE(_playback_clock_native, module) {
    module.doc() = "Native KonoKashi PlaybackClock experiment";

    py::class_<Policy>(module, "Policy")
        .def(py::init<std::int64_t, std::int64_t, std::int64_t, std::int64_t,
                      std::int64_t, std::int64_t, std::int64_t, std::int64_t,
                      std::int64_t, std::int64_t, std::int64_t, std::int64_t,
                      std::int64_t, std::int64_t, std::int64_t, std::int64_t,
                      std::int64_t>());
    py::class_<Observation>(module, "Observation")
        .def(py::init<std::string, std::int64_t, int, std::int64_t, std::int64_t,
                      std::int64_t, int, int, bool>());
    py::class_<Update>(module, "Update")
        .def_readonly("kind", &Update::kind)
        .def_readonly("reason", &Update::reason)
        .def_readonly("residual_us", &Update::residual_us)
        .def_readonly("scheduled_correction_us", &Update::scheduled_correction_us)
        .def_readonly("correction_class", &Update::correction_class);
    py::class_<Diagnostics>(module, "Diagnostics")
        .def_readonly("quality", &Diagnostics::quality)
        .def_readonly("sample_count", &Diagnostics::sample_count)
        .def_readonly("sample_age_us", &Diagnostics::sample_age_us)
        .def_readonly("last_round_trip_us", &Diagnostics::last_round_trip_us)
        .def_readonly("last_residual_us", &Diagnostics::last_residual_us)
        .def_readonly("residual_jitter_us", &Diagnostics::residual_jitter_us)
        .def_readonly("phase_error_remaining_us", &Diagnostics::phase_error_remaining_us)
        .def_readonly("observed_error_bound_us", &Diagnostics::observed_error_bound_us)
        .def_readonly("drift_ppm", &Diagnostics::drift_ppm)
        .def_readonly("drift_correction_active", &Diagnostics::drift_correction_active)
        .def_readonly("health", &Diagnostics::health)
        .def_readonly("last_rtt_us", &Diagnostics::last_rtt_us)
        .def_readonly("minimum_recent_rtt_us", &Diagnostics::minimum_recent_rtt_us)
        .def_readonly("median_recent_rtt_us", &Diagnostics::median_recent_rtt_us)
        .def_readonly("p95_recent_rtt_us", &Diagnostics::p95_recent_rtt_us)
        .def_readonly("maximum_recent_rtt_us", &Diagnostics::maximum_recent_rtt_us)
        .def_readonly("accepted_sample_count", &Diagnostics::accepted_sample_count)
        .def_readonly("rejected_sample_count", &Diagnostics::rejected_sample_count)
        .def_readonly("discontinuity_count", &Diagnostics::discontinuity_count)
        .def_readonly("last_sample_source", &Diagnostics::last_sample_source)
        .def_readonly("median_absolute_residual_us",
                      &Diagnostics::median_absolute_residual_us)
        .def_readonly("p95_absolute_residual_us", &Diagnostics::p95_absolute_residual_us)
        .def_readonly("maximum_absolute_residual_us",
                      &Diagnostics::maximum_absolute_residual_us)
        .def_readonly("last_correction_class", &Diagnostics::last_correction_class);
    py::class_<Estimate>(module, "Estimate")
        .def_readonly("session_id", &Estimate::session_id)
        .def_readonly("position_us", &Estimate::position_us)
        .def_readonly("state", &Estimate::state)
        .def_readonly("reported_rate_ppb", &Estimate::reported_rate_ppb)
        .def_readonly("effective_rate_ppb", &Estimate::effective_rate_ppb)
        .def_readonly("base_rate_ppb", &Estimate::base_rate_ppb)
        .def_readonly("slew_remaining_us", &Estimate::slew_remaining_us)
        .def_readonly("slew_remaining_duration_us", &Estimate::slew_remaining_duration_us)
        .def_readonly("monotonic_ns", &Estimate::monotonic_ns)
        .def_readonly("diagnostics", &Estimate::diagnostics)
        .def_readonly("latest_authoritative_position_us",
                      &Estimate::latest_authoritative_position_us);
    py::class_<PlaybackClockCore>(module, "PlaybackClockCore")
        .def(py::init<Policy>())
        .def_property_readonly("available", &PlaybackClockCore::available)
        .def("observe", &PlaybackClockCore::observe)
        .def("reanchor_seek", &PlaybackClockCore::reanchor_seek)
        .def("transition_state", &PlaybackClockCore::transition_state)
        .def("transition_rate", &PlaybackClockCore::transition_rate)
        .def("mark_suspend_resume", &PlaybackClockCore::mark_suspend_resume)
        .def("mark_sampling_failure", &PlaybackClockCore::mark_sampling_failure)
        .def("estimate", &PlaybackClockCore::estimate);
}
