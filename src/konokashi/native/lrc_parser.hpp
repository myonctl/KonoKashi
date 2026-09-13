#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_set>
#include <utility>
#include <vector>

namespace konokashi::lrc {

inline constexpr std::size_t max_lyrics_text_chars = 2'000'000;
inline constexpr std::int64_t max_lyric_timestamp_ms =
    9'223'372'036'854'775'807LL;

struct SegmentRecord {
  std::string segment_id;
  std::string text;
  std::int64_t start_ms;
  std::optional<std::int64_t> end_ms;
};

struct LineRecord {
  std::string line_id;
  std::string text;
  std::optional<std::int64_t> start_ms;
  std::vector<SegmentRecord> segments;
  std::size_t source_position;
  bool text_from_source = false;
};

struct ParseResult {
  std::string status = "invalid";
  std::vector<LineRecord> lines;
  std::vector<std::pair<std::string, std::size_t>> plain_lines;
  std::vector<std::pair<std::string, std::string>> metadata;
  std::vector<std::string> diagnostics;
  std::unordered_set<std::string> diagnostic_keys;
  std::string normalized_text;
  bool normalized_text_from_source = false;
  std::optional<std::string> raw_text_checksum;
  std::string timing_level = "unsynchronized";
};

[[nodiscard]] const std::vector<char32_t> &decimal_digit_block_starts();
void configure_decimal_digit_blocks(std::vector<char32_t> starts);
[[nodiscard]] std::size_t utf8_character_count(std::string_view text,
                                               std::size_t stop_after);

[[nodiscard]] ParseResult
parse(std::string_view text,
      std::optional<std::int64_t> duration_ms = std::nullopt);

} // namespace konokashi::lrc
