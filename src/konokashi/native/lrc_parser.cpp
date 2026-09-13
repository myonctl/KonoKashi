#include "lrc_parser.hpp"

#if !defined(KONOKASHI_PORTABLE_SHA256)
#include <openssl/evp.h>
#endif

#include <algorithm>
#include <array>
#include <bit>
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <limits>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <unordered_set>
#include <utility>
#include <vector>

namespace konokashi::lrc {
namespace {

struct TimestampMatch {
  std::int64_t value_ms;
  std::size_t end;
};

struct EnhancedPart {
  std::int64_t start_ms;
  std::string text;
};

struct TimedSourceLine {
  std::int64_t start_ms;
  std::size_t source_position;
  std::size_t copy;
  std::string text;
  std::vector<EnhancedPart> enhanced_parts;
};

struct DecodedCodepoint {
  char32_t value;
  std::size_t next;
};

[[nodiscard]] DecodedCodepoint decode_utf8(const std::string_view text,
                                           const std::size_t position) {
  const auto first = static_cast<unsigned char>(text.at(position));
  if (first < 0x80U) {
    return {first, position + 1U};
  }
  std::size_t width = 0;
  char32_t value = 0;
  if ((first & 0xE0U) == 0xC0U) {
    width = 2;
    value = first & 0x1FU;
  } else if ((first & 0xF0U) == 0xE0U) {
    width = 3;
    value = first & 0x0FU;
  } else if ((first & 0xF8U) == 0xF0U) {
    width = 4;
    value = first & 0x07U;
  } else {
    throw std::invalid_argument("invalid UTF-8");
  }
  if (position + width > text.size()) {
    throw std::invalid_argument("truncated UTF-8");
  }
  for (std::size_t index = 1; index < width; ++index) {
    const auto continuation =
        static_cast<unsigned char>(text[position + index]);
    if ((continuation & 0xC0U) != 0x80U) {
      throw std::invalid_argument("invalid UTF-8 continuation");
    }
    value = (value << 6U) | (continuation & 0x3FU);
  }
  if ((width == 2U && value < 0x80U) || (width == 3U && value < 0x800U) ||
      (width == 4U && value < 0x10000U) ||
      (value >= 0xD800U && value <= 0xDFFFU) || value > 0x10FFFFU) {
    throw std::invalid_argument("invalid UTF-8 code point");
  }
  return {value, position + width};
}

constexpr std::array<char32_t, 76> decimal_starts = {
    U'0',    0x0660,  0x06F0,  0x07C0,  0x0966,  0x09E6,  0x0A66,  0x0AE6,
    0x0B66,  0x0BE6,  0x0C66,  0x0CE6,  0x0D66,  0x0DE6,  0x0E50,  0x0ED0,
    0x0F20,  0x1040,  0x1090,  0x17E0,  0x1810,  0x1946,  0x19D0,  0x1A80,
    0x1A90,  0x1B50,  0x1BB0,  0x1C40,  0x1C50,  0xA620,  0xA8D0,  0xA900,
    0xA9D0,  0xA9F0,  0xAA50,  0xABF0,  0xFF10,  0x104A0, 0x10D30, 0x10D40,
    0x11066, 0x110F0, 0x11136, 0x111D0, 0x112F0, 0x11450, 0x114D0, 0x11650,
    0x116C0, 0x116D0, 0x116DA, 0x11730, 0x118E0, 0x11950, 0x11BF0, 0x11C50,
    0x11D50, 0x11DA0, 0x11F50, 0x16130, 0x16A60, 0x16AC0, 0x16B50, 0x16D70,
    0x1CCF0, 0x1D7CE, 0x1D7D8, 0x1D7E2, 0x1D7EC, 0x1D7F6, 0x1E140, 0x1E2F0,
    0x1E4F0, 0x1E5F1, 0x1E950, 0x1FBF0,
};

std::vector<char32_t> enabled_decimal_starts(decimal_starts.begin(),
                                             decimal_starts.end());

[[nodiscard]] std::optional<unsigned int> decimal_digit(const char32_t value) {
  // Every Unicode Nd block used by Python's base-10 integer conversion through
  // the newest supported runtime. The binding filters the table once at import
  // so older supported Python Unicode databases retain exact oracle behavior.
  for (const auto start : enabled_decimal_starts) {
    if (value >= start && value <= start + 9) {
      return static_cast<unsigned int>(value - start);
    }
  }
  return std::nullopt;
}

[[nodiscard]] bool unicode_space(const char32_t value) {
  return (value >= 0x0009 && value <= 0x000D) ||
         (value >= 0x001C && value <= 0x0020) || value == 0x0085 ||
         value == 0x00A0 || value == 0x1680 ||
         (value >= 0x2000 && value <= 0x200A) || value == 0x2028 ||
         value == 0x2029 || value == 0x202F || value == 0x205F ||
         value == 0x3000;
}

[[nodiscard]] std::string trim_unicode(const std::string_view value) {
  std::optional<std::size_t> first_non_space;
  std::size_t last_non_space_end = 0;
  for (std::size_t position = 0; position < value.size();) {
    const auto decoded = decode_utf8(value, position);
    if (!unicode_space(decoded.value)) {
      if (!first_non_space.has_value()) {
        first_non_space = position;
      }
      last_non_space_end = decoded.next;
    }
    position = decoded.next;
  }
  if (!first_non_space.has_value()) {
    return {};
  }
  return std::string(
      value.substr(*first_non_space, last_non_space_end - *first_non_space));
}

[[nodiscard]] bool contains_non_space(const std::string_view value) {
  for (std::size_t position = 0; position < value.size();) {
    const auto decoded = decode_utf8(value, position);
    if (!unicode_space(decoded.value)) {
      return true;
    }
    position = decoded.next;
  }
  return false;
}

[[nodiscard]] std::optional<TimestampMatch>
timestamp_at(const std::string_view value, const std::size_t position,
             const char opening, const char closing) {
  if (position >= value.size() || value[position] != opening) {
    return std::nullopt;
  }
  auto cursor = position + 1U;
  std::int64_t minutes = 0;
  std::size_t minute_digits = 0;
  while (cursor < value.size() && minute_digits < 3U) {
    const auto decoded = decode_utf8(value, cursor);
    const auto digit = decimal_digit(decoded.value);
    if (!digit.has_value()) {
      break;
    }
    minutes = minutes * 10 + static_cast<std::int64_t>(*digit);
    cursor = decoded.next;
    ++minute_digits;
  }
  if (minute_digits == 0U || cursor >= value.size() || value[cursor] != ':') {
    return std::nullopt;
  }
  ++cursor;
  std::int64_t seconds = 0;
  for (std::size_t index = 0; index < 2U; ++index) {
    if (cursor >= value.size()) {
      return std::nullopt;
    }
    const auto decoded = decode_utf8(value, cursor);
    const auto digit = decimal_digit(decoded.value);
    if (!digit.has_value() ||
        (index == 0U && (decoded.value < U'0' || decoded.value > U'5'))) {
      return std::nullopt;
    }
    seconds = seconds * 10 + static_cast<std::int64_t>(*digit);
    cursor = decoded.next;
  }
  if (seconds > 59 || cursor >= value.size() ||
      (value[cursor] != '.' && value[cursor] != ':')) {
    return std::nullopt;
  }
  ++cursor;
  std::int64_t fraction = 0;
  std::size_t fraction_digits = 0;
  while (cursor < value.size() && fraction_digits < 3U) {
    const auto decoded = decode_utf8(value, cursor);
    const auto digit = decimal_digit(decoded.value);
    if (!digit.has_value()) {
      break;
    }
    fraction = fraction * 10 + static_cast<std::int64_t>(*digit);
    cursor = decoded.next;
    ++fraction_digits;
  }
  if ((fraction_digits != 2U && fraction_digits != 3U) ||
      cursor >= value.size() || value[cursor] != closing) {
    return std::nullopt;
  }
  if (fraction_digits == 2U) {
    fraction *= 10;
  }
  return TimestampMatch{(minutes * 60 + seconds) * 1'000 + fraction,
                        cursor + 1U};
}

[[nodiscard]] bool timestamp_like(const std::string_view value) {
  if (value.empty() || value.front() != '[') {
    return false;
  }
  auto cursor = std::size_t{1};
  std::size_t digits = 0;
  while (cursor < value.size() && digits < 3U) {
    const auto decoded = decode_utf8(value, cursor);
    if (!decimal_digit(decoded.value).has_value()) {
      break;
    }
    cursor = decoded.next;
    ++digits;
  }
  if (digits == 0U || cursor >= value.size() || value[cursor] != ':') {
    return false;
  }
  return value.find(']', cursor + 1U) != std::string_view::npos;
}

[[nodiscard]] bool ascii_letter(const char value) {
  const auto byte = static_cast<unsigned char>(value);
  return (byte >= static_cast<unsigned char>('A') &&
          byte <= static_cast<unsigned char>('Z')) ||
         (byte >= static_cast<unsigned char>('a') &&
          byte <= static_cast<unsigned char>('z'));
}

[[nodiscard]] std::string ascii_lower(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](const char item) {
                   const auto byte = static_cast<unsigned char>(item);
                   if (byte >= static_cast<unsigned char>('A') &&
                       byte <= static_cast<unsigned char>('Z')) {
                     return static_cast<char>(byte + 32U);
                   }
                   return item;
                 });
  return value;
}

[[nodiscard]] std::optional<std::pair<std::string, std::string>>
metadata(const std::string_view value) {
  if (value.size() < 4U || value.front() != '[' || value.back() != ']') {
    return std::nullopt;
  }
  const auto colon = value.find(':');
  if (colon == std::string_view::npos || colon < 2U) {
    return std::nullopt;
  }
  const auto key = value.substr(1U, colon - 1U);
  if (!ascii_letter(key.front()) ||
      !std::all_of(key.begin() + 1, key.end(), [](const char item) {
        const auto byte = static_cast<unsigned char>(item);
        return ascii_letter(item) ||
               (byte >= static_cast<unsigned char>('0') &&
                byte <= static_cast<unsigned char>('9')) ||
               item == '_' || item == '-';
      })) {
    return std::nullopt;
  }
  return std::pair{
      ascii_lower(std::string(key)),
      trim_unicode(value.substr(colon + 1U, value.size() - colon - 2U)),
  };
}

[[nodiscard]] bool malformed_known_metadata(const std::string_view value) {
  if (value.size() < 4U || value.front() != '[' || value.back() != ']') {
    return false;
  }
  auto cursor = std::size_t{1};
  auto key_end = cursor;
  while (key_end < value.size()) {
    if (value[key_end] == ']') {
      break;
    }
    const auto decoded = decode_utf8(value, key_end);
    if (unicode_space(decoded.value)) {
      break;
    }
    key_end = decoded.next;
  }
  const auto key =
      ascii_lower(std::string(value.substr(cursor, key_end - cursor)));
  const std::unordered_set<std::string> known = {
      "ar", "ti", "al", "by", "re", "ve", "length", "offset"};
  if (!known.contains(key)) {
    return false;
  }
  cursor = key_end;
  if (value[cursor] == ']') {
    return cursor + 1U == value.size();
  }
  bool whitespace = false;
  while (cursor < value.size() - 1U) {
    const auto decoded = decode_utf8(value, cursor);
    if (!unicode_space(decoded.value)) {
      break;
    }
    whitespace = true;
    cursor = decoded.next;
  }
  return whitespace && value.find(']', cursor) == value.size() - 1U;
}

struct ParsedInteger {
  bool valid = false;
  bool overflow = false;
  bool negative = false;
  std::uint64_t magnitude = 0;
};

[[nodiscard]] ParsedInteger parse_python_integer(const std::string_view raw) {
  const auto value = trim_unicode(raw);
  if (value.empty()) {
    return {};
  }
  std::size_t cursor = 0;
  bool negative = false;
  if (value[cursor] == '+' || value[cursor] == '-') {
    negative = value[cursor] == '-';
    ++cursor;
  }
  bool saw_digit = false;
  bool prior_underscore = false;
  bool overflow = false;
  std::uint64_t magnitude = 0;
  while (cursor < value.size()) {
    if (value[cursor] == '_') {
      if (!saw_digit || prior_underscore) {
        return {};
      }
      prior_underscore = true;
      ++cursor;
      continue;
    }
    const auto decoded = decode_utf8(value, cursor);
    const auto digit = decimal_digit(decoded.value);
    if (!digit.has_value()) {
      return {};
    }
    if (magnitude >
        (std::numeric_limits<std::uint64_t>::max() - *digit) / 10U) {
      overflow = true;
    } else if (!overflow) {
      magnitude = magnitude * 10U + *digit;
    }
    saw_digit = true;
    prior_underscore = false;
    cursor = decoded.next;
  }
  if (!saw_digit || prior_underscore) {
    return {};
  }
  return {true, overflow, negative, magnitude};
}

[[nodiscard]] std::optional<std::int64_t>
bounded_offset(const ParsedInteger &parsed) {
  if (!parsed.valid || parsed.overflow ||
      parsed.magnitude > static_cast<std::uint64_t>(max_lyric_timestamp_ms)) {
    return std::nullopt;
  }
  const auto magnitude = static_cast<std::int64_t>(parsed.magnitude);
  return parsed.negative ? -magnitude : magnitude;
}

[[nodiscard]] std::optional<std::int64_t>
checked_add(const std::int64_t left, const std::int64_t right) {
  if ((right > 0 && left > max_lyric_timestamp_ms - right) ||
      (right < 0 && left < std::numeric_limits<std::int64_t>::min() - right)) {
    return std::nullopt;
  }
  return left + right;
}

void add_diagnostic(ParseResult &result, std::string diagnostic) {
  if (result.diagnostic_keys.insert(diagnostic).second) {
    result.diagnostics.push_back(std::move(diagnostic));
  }
}

[[nodiscard]] std::string normalize(std::string text) {
  if (text.starts_with("\xEF\xBB\xBF")) {
    text.erase(0, 3);
  }
  if (text.find('\r') == std::string::npos) {
    return text;
  }
  std::size_t write = 0;
  for (std::size_t read = 0; read < text.size(); ++read) {
    if (text[read] == '\r') {
      if (read + 1U < text.size() && text[read + 1U] == '\n') {
        ++read;
      }
      text[write++] = '\n';
    } else {
      text[write++] = text[read];
    }
  }
  text.resize(write);
  return text;
}

[[nodiscard]] std::vector<std::string_view>
split_lines(const std::string_view value) {
  std::vector<std::string_view> result;
  std::size_t start = 0;
  while (true) {
    const auto end = value.find('\n', start);
    if (end == std::string_view::npos) {
      result.push_back(value.substr(start));
      return result;
    }
    result.push_back(value.substr(start, end - start));
    start = end + 1U;
  }
}

[[nodiscard]] std::pair<std::string, std::vector<EnhancedPart>>
enhanced_parts(const std::string_view value) {
  struct Match {
    std::size_t start;
    TimestampMatch timestamp;
  };
  std::vector<Match> matches;
  for (std::size_t position = 0; position < value.size();) {
    if (value[position] == '<') {
      if (const auto match = timestamp_at(value, position, '<', '>');
          match.has_value()) {
        matches.push_back({position, *match});
        position = match->end;
        continue;
      }
    }
    position = decode_utf8(value, position).next;
  }
  if (matches.empty()) {
    return {std::string(value), {}};
  }
  std::vector<EnhancedPart> parts;
  parts.reserve(matches.size());
  for (std::size_t index = 0; index < matches.size(); ++index) {
    const auto text_start = matches[index].timestamp.end;
    const auto text_end =
        index + 1U < matches.size() ? matches[index + 1U].start : value.size();
    parts.push_back({
        matches[index].timestamp.value_ms,
        std::string(value.substr(text_start, text_end - text_start)),
    });
  }
  if (matches.front().start > 0U) {
    parts.front().text = std::string(value.substr(0, matches.front().start)) +
                         parts.front().text;
  }
  std::string text;
  for (const auto &part : parts) {
    text += part.text;
  }
  return {std::move(text), std::move(parts)};
}

#if defined(KONOKASHI_PORTABLE_SHA256)
class Sha256 {
public:
  void update(const std::string_view input) {
    const auto *bytes = reinterpret_cast<const std::uint8_t *>(input.data());
    byte_count_ += input.size();
    std::size_t position = 0;
    if (buffer_size_ != 0U) {
      const auto copied = std::min(buffer_.size() - buffer_size_, input.size());
      std::memcpy(buffer_.data() + buffer_size_, bytes, copied);
      buffer_size_ += copied;
      position += copied;
      if (buffer_size_ == buffer_.size()) {
        transform(buffer_.data());
        buffer_size_ = 0;
      }
    }
    while (position + buffer_.size() <= input.size()) {
      transform(bytes + position);
      position += buffer_.size();
    }
    if (position < input.size()) {
      buffer_size_ = input.size() - position;
      std::memcpy(buffer_.data(), bytes + position, buffer_size_);
    }
  }

  [[nodiscard]] std::string finish() {
    const auto bit_count = byte_count_ * 8U;
    buffer_[buffer_size_++] = 0x80U;
    if (buffer_size_ > 56U) {
      while (buffer_size_ < 64U) {
        buffer_[buffer_size_++] = 0;
      }
      transform(buffer_.data());
      buffer_size_ = 0;
    }
    while (buffer_size_ < 56U) {
      buffer_[buffer_size_++] = 0;
    }
    for (std::size_t index = 0; index < 8U; ++index) {
      buffer_[63U - index] =
          static_cast<std::uint8_t>(bit_count >> (index * 8U));
    }
    transform(buffer_.data());
    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (const auto word : state_) {
      output << std::setw(8) << word;
    }
    return output.str();
  }

private:
  static constexpr std::array<std::uint32_t, 64> constants_ = {
      0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U, 0x3956c25bU,
      0x59f111f1U, 0x923f82a4U, 0xab1c5ed5U, 0xd807aa98U, 0x12835b01U,
      0x243185beU, 0x550c7dc3U, 0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U,
      0xc19bf174U, 0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU,
      0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU, 0x983e5152U,
      0xa831c66dU, 0xb00327c8U, 0xbf597fc7U, 0xc6e00bf3U, 0xd5a79147U,
      0x06ca6351U, 0x14292967U, 0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU,
      0x53380d13U, 0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U,
      0xa2bfe8a1U, 0xa81a664bU, 0xc24b8b70U, 0xc76c51a3U, 0xd192e819U,
      0xd6990624U, 0xf40e3585U, 0x106aa070U, 0x19a4c116U, 0x1e376c08U,
      0x2748774cU, 0x34b0bcb5U, 0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU,
      0x682e6ff3U, 0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
      0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U,
  };

  void transform(const std::uint8_t *block) {
    std::array<std::uint32_t, 64> words{};
    for (std::size_t index = 0; index < 16U; ++index) {
      const auto offset = index * 4U;
      words[index] = (static_cast<std::uint32_t>(block[offset]) << 24U) |
                     (static_cast<std::uint32_t>(block[offset + 1U]) << 16U) |
                     (static_cast<std::uint32_t>(block[offset + 2U]) << 8U) |
                     static_cast<std::uint32_t>(block[offset + 3U]);
    }
    for (std::size_t index = 16U; index < words.size(); ++index) {
      const auto s0 = std::rotr(words[index - 15U], 7) ^
                      std::rotr(words[index - 15U], 18) ^
                      (words[index - 15U] >> 3U);
      const auto s1 = std::rotr(words[index - 2U], 17) ^
                      std::rotr(words[index - 2U], 19) ^
                      (words[index - 2U] >> 10U);
      words[index] = words[index - 16U] + s0 + words[index - 7U] + s1;
    }
    auto a = state_[0];
    auto b = state_[1];
    auto c = state_[2];
    auto d = state_[3];
    auto e = state_[4];
    auto f = state_[5];
    auto g = state_[6];
    auto h = state_[7];
    for (std::size_t index = 0; index < words.size(); ++index) {
      const auto sum1 = std::rotr(e, 6) ^ std::rotr(e, 11) ^ std::rotr(e, 25);
      const auto choice = (e & f) ^ ((~e) & g);
      const auto temporary1 =
          h + sum1 + choice + constants_[index] + words[index];
      const auto sum0 = std::rotr(a, 2) ^ std::rotr(a, 13) ^ std::rotr(a, 22);
      const auto majority = (a & b) ^ (a & c) ^ (b & c);
      const auto temporary2 = sum0 + majority;
      h = g;
      g = f;
      f = e;
      e = d + temporary1;
      d = c;
      c = b;
      b = a;
      a = temporary1 + temporary2;
    }
    state_[0] += a;
    state_[1] += b;
    state_[2] += c;
    state_[3] += d;
    state_[4] += e;
    state_[5] += f;
    state_[6] += g;
    state_[7] += h;
  }

  std::array<std::uint32_t, 8> state_ = {
      0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
      0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U,
  };
  std::array<std::uint8_t, 64> buffer_{};
  std::size_t buffer_size_ = 0;
  std::uint64_t byte_count_ = 0;
};
#endif

[[nodiscard]] std::string sha256(const std::string_view value) {
#if defined(KONOKASHI_PORTABLE_SHA256)
  Sha256 digest;
  digest.update(value);
  return digest.finish();
#else
  std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
  unsigned int digest_size = 0;
  if (EVP_Digest(value.data(), value.size(), digest.data(), &digest_size,
                 EVP_sha256(), nullptr) != 1 ||
      digest_size != 32U) {
    throw std::runtime_error("SHA-256 digest calculation failed");
  }
  constexpr std::string_view hexadecimal = "0123456789abcdef";
  std::string result(64U, '0');
  for (std::size_t index = 0; index < digest_size; ++index) {
    const auto byte = digest[index];
    result[index * 2U] = hexadecimal[byte >> 4U];
    result[index * 2U + 1U] = hexadecimal[byte & 0x0FU];
  }
  return result;
#endif
}

[[nodiscard]] std::string line_id(const std::string &checksum,
                                  const std::size_t source_position,
                                  const std::size_t copy,
                                  const std::optional<std::int64_t> start_ms) {
  const auto start =
      start_ms.has_value() ? std::to_string(*start_ms) : std::string{"None"};
  return "line-" + sha256(checksum + ":" + std::to_string(source_position) +
                          ":" + std::to_string(copy) + ":" + start)
                       .substr(0, 20);
}

[[nodiscard]] std::string segment_id(const std::string &source_line_id,
                                     const std::size_t position,
                                     const std::int64_t start_ms) {
  return "segment-" + sha256(source_line_id + ":" + std::to_string(position) +
                             ":" + std::to_string(start_ms))
                          .substr(0, 20);
}

[[nodiscard]] std::string line_diagnostic(const std::size_t source_position,
                                          const std::string_view message) {
  return "line " + std::to_string(source_position + 1U) + ": " +
         std::string(message);
}

} // namespace

const std::vector<char32_t> &decimal_digit_block_starts() {
  static const std::vector<char32_t> starts(decimal_starts.begin(),
                                            decimal_starts.end());
  return starts;
}

void configure_decimal_digit_blocks(std::vector<char32_t> starts) {
  enabled_decimal_starts = std::move(starts);
}

std::size_t utf8_character_count(const std::string_view text,
                                 const std::size_t stop_after) {
  std::size_t count = 0;
  for (std::size_t position = 0; position < text.size();) {
    const auto first = static_cast<unsigned char>(text[position]);
    std::size_t width = 1;
    if (first < 0x80U) {
      width = 1;
    } else if (first >= 0xC2U && first <= 0xDFU) {
      width = 2;
    } else if (first >= 0xE0U && first <= 0xEFU) {
      width = 3;
    } else if (first >= 0xF0U && first <= 0xF4U) {
      width = 4;
    } else {
      throw std::invalid_argument("invalid UTF-8");
    }
    if (position + width > text.size()) {
      throw std::invalid_argument("truncated UTF-8");
    }
    for (std::size_t index = 1; index < width; ++index) {
      const auto continuation =
          static_cast<unsigned char>(text[position + index]);
      if ((continuation & 0xC0U) != 0x80U) {
        throw std::invalid_argument("invalid UTF-8 continuation");
      }
    }
    if (width >= 3U) {
      const auto second = static_cast<unsigned char>(text[position + 1U]);
      if ((first == 0xE0U && second < 0xA0U) ||
          (first == 0xEDU && second >= 0xA0U) ||
          (first == 0xF0U && second < 0x90U) ||
          (first == 0xF4U && second >= 0x90U)) {
        throw std::invalid_argument("invalid UTF-8 code point");
      }
    }
    position += width;
    ++count;
    if (count > stop_after) {
      return count;
    }
  }
  return count;
}

ParseResult parse(const std::string_view text,
                  const std::optional<std::int64_t> duration_ms) {
  ParseResult result;
  result.normalized_text_from_source =
      !text.starts_with("\xEF\xBB\xBF") && text.find('\r') == std::string::npos;
  if (!result.normalized_text_from_source) {
    result.normalized_text = normalize(std::string(text));
  }
  const auto normalized = result.normalized_text_from_source
                              ? text
                              : std::string_view(result.normalized_text);
  result.raw_text_checksum = sha256(normalized);
  const auto checksum = *result.raw_text_checksum;
  std::vector<TimedSourceLine> timed;
  std::vector<std::pair<std::size_t, std::string_view>> plain_source;
  std::int64_t offset_ms = 0;
  bool invalid_timing = false;
  const std::unordered_set<std::string> supported = {"ar", "ti", "al",    "by",
                                                     "re", "ve", "length"};

  const auto source_lines = split_lines(normalized);
  for (std::size_t source_position = 0; source_position < source_lines.size();
       ++source_position) {
    const auto source_line = source_lines[source_position];
    if (source_line.empty() || source_line.front() != '[') {
      plain_source.emplace_back(source_position, source_line);
      continue;
    }
    auto cursor = std::size_t{0};
    std::vector<std::int64_t> timestamps;
    while (const auto match = timestamp_at(source_line, cursor, '[', ']')) {
      timestamps.push_back(match->value_ms);
      cursor = match->end;
    }
    if (!timestamps.empty()) {
      auto [line_text, parts] = enhanced_parts(source_line.substr(cursor));
      for (std::size_t copy = 0; copy < timestamps.size(); ++copy) {
        const auto delta = timestamps[copy] - timestamps.front();
        auto shifted_parts = parts;
        for (auto &part : shifted_parts) {
          part.start_ms += delta;
        }
        timed.push_back({
            timestamps[copy],
            source_position,
            copy,
            line_text,
            std::move(shifted_parts),
        });
      }
      continue;
    }
    if (timestamp_like(source_line)) {
      add_diagnostic(
          result, line_diagnostic(source_position,
                                  "malformed timestamp was not reinterpreted"));
      invalid_timing = true;
      continue;
    }
    if (const auto parsed_metadata = metadata(source_line);
        parsed_metadata.has_value()) {
      const auto &[key, value] = *parsed_metadata;
      if (key == "offset") {
        const auto integer = parse_python_integer(value);
        if (!integer.valid) {
          add_diagnostic(result, line_diagnostic(source_position,
                                                 "malformed offset metadata"));
          invalid_timing = true;
        } else if (const auto bounded = bounded_offset(integer);
                   !bounded.has_value()) {
          add_diagnostic(
              result,
              line_diagnostic(
                  source_position,
                  "offset metadata is outside the supported integer range"));
          invalid_timing = true;
        } else {
          offset_ms = *bounded;
          result.metadata.push_back(*parsed_metadata);
        }
      } else if (supported.contains(key)) {
        result.metadata.push_back(*parsed_metadata);
      } else {
        add_diagnostic(result, line_diagnostic(source_position,
                                               "unsupported metadata tag '" +
                                                   key + "' ignored"));
      }
      continue;
    }
    if (malformed_known_metadata(source_line)) {
      add_diagnostic(result,
                     line_diagnostic(source_position,
                                     "malformed lyrics metadata ignored"));
      continue;
    }
    plain_source.emplace_back(source_position, source_line);
  }

  if (!timed.empty()) {
    if (invalid_timing) {
      return result;
    }
    std::vector<TimedSourceLine> adjusted;
    adjusted.reserve(timed.size());
    for (const auto &source : timed) {
      const auto start = checked_add(source.start_ms, offset_ms);
      if (!start.has_value() || *start > max_lyric_timestamp_ms) {
        add_diagnostic(result,
                       line_diagnostic(
                           source.source_position,
                           "timestamp is outside the supported integer range"));
        invalid_timing = true;
        continue;
      }
      if (*start < 0) {
        add_diagnostic(result,
                       line_diagnostic(source.source_position,
                                       "offset produced a negative timestamp"));
        invalid_timing = true;
        continue;
      }
      if (duration_ms.has_value() &&
          (*duration_ms <= max_lyric_timestamp_ms - 2'000) &&
          *start > *duration_ms + 2'000) {
        add_diagnostic(result,
                       line_diagnostic(source.source_position,
                                       "timestamp exceeds track duration"));
      }
      const auto starts_ordered = std::is_sorted(
          source.enhanced_parts.begin(), source.enhanced_parts.end(),
          [](const auto &first, const auto &second) {
            return first.start_ms < second.start_ms;
          });
      if (!starts_ordered) {
        add_diagnostic(result,
                       line_diagnostic(source.source_position,
                                       "enhanced timestamps are out of order"));
        invalid_timing = true;
      }
      std::vector<EnhancedPart> adjusted_parts;
      adjusted_parts.reserve(source.enhanced_parts.size());
      for (const auto &part : source.enhanced_parts) {
        const auto segment_start = checked_add(part.start_ms, offset_ms);
        if (!segment_start.has_value() || *segment_start < 0 ||
            *segment_start > max_lyric_timestamp_ms) {
          add_diagnostic(
              result,
              line_diagnostic(
                  source.source_position,
                  "enhanced timestamp is outside the supported integer range"));
          invalid_timing = true;
          continue;
        }
        adjusted_parts.push_back({*segment_start, part.text});
      }
      adjusted.push_back({
          *start,
          source.source_position,
          source.copy,
          source.text,
          std::move(adjusted_parts),
      });
    }
    if (invalid_timing) {
      return result;
    }
    if (!std::is_sorted(adjusted.begin(), adjusted.end(),
                        [](const auto &first, const auto &second) {
                          return first.start_ms < second.start_ms;
                        })) {
      add_diagnostic(result,
                     "out-of-order timestamps were ordered chronologically");
    }
    std::unordered_set<std::int64_t> starts;
    const auto duplicate = std::any_of(
        adjusted.begin(), adjusted.end(), [&starts](const auto &item) {
          return !starts.insert(item.start_ms).second;
        });
    if (duplicate) {
      add_diagnostic(result,
                     "duplicate timestamps were preserved as distinct lines");
    }
    std::stable_sort(
        adjusted.begin(), adjusted.end(),
        [](const auto &first, const auto &second) {
          return std::tie(first.start_ms, first.source_position, first.copy) <
                 std::tie(second.start_ms, second.source_position, second.copy);
        });
    bool has_segments = false;
    for (const auto &item : adjusted) {
      const auto source_line_id =
          line_id(checksum, item.source_position, item.copy, item.start_ms);
      LineRecord line{
          source_line_id,       item.text, item.start_ms, {},
          item.source_position, false,
      };
      line.segments.reserve(item.enhanced_parts.size());
      for (std::size_t position = 0; position < item.enhanced_parts.size();
           ++position) {
        const auto &part = item.enhanced_parts[position];
        line.segments.push_back({
            segment_id(source_line_id, position, part.start_ms),
            part.text,
            part.start_ms,
            position + 1U < item.enhanced_parts.size()
                ? std::optional<std::int64_t>{item.enhanced_parts[position + 1U]
                                                  .start_ms}
                : std::nullopt,
        });
      }
      has_segments = has_segments || !line.segments.empty();
      result.lines.push_back(std::move(line));
    }
    result.status = "synced";
    result.timing_level = has_segments ? "word" : "line";
    return result;
  }

  if (invalid_timing) {
    return result;
  }
  const auto has_text = std::any_of(
      plain_source.begin(), plain_source.end(),
      [](const auto &item) { return contains_non_space(item.second); });
  if (!has_text) {
    add_diagnostic(result, "lyrics contain no text");
    return result;
  }
  result.plain_lines.reserve(plain_source.size());
  for (const auto &[source_position, line_text] : plain_source) {
    result.plain_lines.emplace_back(
        line_id(checksum, source_position, 0, std::nullopt), source_position);
  }
  result.status = "plain";
  return result;
}

} // namespace konokashi::lrc
