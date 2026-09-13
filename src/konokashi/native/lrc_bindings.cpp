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

#include "lrc_parser.hpp"

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace py = pybind11;
using konokashi::lrc::LineRecord;
using konokashi::lrc::ParseResult;
using konokashi::lrc::SegmentRecord;

namespace {

struct BoundParseResult {
  ParseResult value;
  py::object source;
};

[[nodiscard]] BoundParseResult oversized_result(py::object source) {
  ParseResult result;
  result.diagnostics.emplace_back(
      "lyrics text exceeds the bounded parser size");
  return {std::move(result), std::move(source)};
}

[[nodiscard]] BoundParseResult
parse_encoded(const std::string_view encoded, const py::object &source,
              const std::optional<std::int64_t> duration_ms) {
  ParseResult result;
  {
    py::gil_scoped_release release;
    result = konokashi::lrc::parse(encoded, duration_ms);
  }
  return {std::move(result), source};
}

} // namespace

PYBIND11_MODULE(_lrc_native, module) {
  module.doc() = "Bounded native LRC/plain-text parser";

  const auto category = py::module_::import("unicodedata").attr("category");
  std::vector<char32_t> decimal_starts;
  for (const auto start : konokashi::lrc::decimal_digit_block_starts()) {
    auto character = py::reinterpret_steal<py::object>(
        PyUnicode_FromOrdinal(static_cast<int>(start)));
    if (category(character).cast<std::string>() == "Nd") {
      decimal_starts.push_back(start);
    }
  }
  konokashi::lrc::configure_decimal_digit_blocks(std::move(decimal_starts));

  py::class_<SegmentRecord>(module, "SegmentRecord")
      .def_readonly("segment_id", &SegmentRecord::segment_id)
      .def_readonly("text", &SegmentRecord::text)
      .def_readonly("start_ms", &SegmentRecord::start_ms)
      .def_readonly("end_ms", &SegmentRecord::end_ms);

  py::class_<LineRecord>(module, "LineRecord")
      .def_readonly("line_id", &LineRecord::line_id)
      .def_readonly("text", &LineRecord::text)
      .def_readonly("start_ms", &LineRecord::start_ms)
      .def_readonly("segments", &LineRecord::segments)
      .def_readonly("source_position", &LineRecord::source_position)
      .def_readonly("text_from_source", &LineRecord::text_from_source);

  py::class_<BoundParseResult>(module, "ParseResult")
      .def_property_readonly(
          "status",
          [](const BoundParseResult &result) { return result.value.status; })
      .def_property_readonly(
          "lines",
          [](const BoundParseResult &result) { return result.value.lines; })
      .def_property_readonly("plain_lines",
                             [](const BoundParseResult &result) {
                               return result.value.plain_lines;
                             })
      .def_property_readonly(
          "metadata",
          [](const BoundParseResult &result) { return result.value.metadata; })
      .def_property_readonly("diagnostics",
                             [](const BoundParseResult &result) {
                               return result.value.diagnostics;
                             })
      .def_property_readonly(
          "normalized_text",
          [](const BoundParseResult &result) -> py::str {
            if (result.value.normalized_text_from_source) {
              if (PyUnicode_Check(result.source.ptr()) != 0) {
                return py::reinterpret_borrow<py::str>(result.source);
              }
              char *encoded = nullptr;
              Py_ssize_t encoded_size = 0;
              if (PyBytes_AsStringAndSize(result.source.ptr(), &encoded,
                                          &encoded_size) != 0) {
                throw py::error_already_set();
              }
              auto decoded = py::reinterpret_steal<py::object>(
                  PyUnicode_DecodeUTF8(encoded, encoded_size, "strict"));
              if (!decoded) {
                throw py::error_already_set();
              }
              return py::reinterpret_borrow<py::str>(decoded);
            }
            return py::str(result.value.normalized_text);
          })
      .def_property_readonly("normalized_text_from_source",
                             [](const BoundParseResult &result) {
                               return result.value.normalized_text_from_source;
                             })
      .def_property_readonly("raw_text_checksum",
                             [](const BoundParseResult &result) {
                               return result.value.raw_text_checksum;
                             })
      .def_property_readonly("timing_level",
                             [](const BoundParseResult &result) {
                               return result.value.timing_level;
                             });

  module.def(
      "parse",
      [](const py::str &text, std::optional<std::int64_t> duration_ms) {
        if (py::len(text) > konokashi::lrc::max_lyrics_text_chars) {
          return oversized_result(text);
        }
        Py_ssize_t encoded_size = 0;
        const auto *encoded =
            PyUnicode_AsUTF8AndSize(text.ptr(), &encoded_size);
        if (encoded == nullptr) {
          throw py::error_already_set();
        }
        return parse_encoded(
            std::string_view(encoded, static_cast<std::size_t>(encoded_size)),
            text, duration_ms);
      },
      py::arg("text"), py::arg("duration_ms") = py::none());

  module.def(
      "parse",
      [](const py::bytes &text, std::optional<std::int64_t> duration_ms) {
        char *encoded = nullptr;
        Py_ssize_t encoded_size = 0;
        if (PyBytes_AsStringAndSize(text.ptr(), &encoded, &encoded_size) != 0) {
          throw py::error_already_set();
        }
        const auto view =
            std::string_view(encoded, static_cast<std::size_t>(encoded_size));
        if (konokashi::lrc::utf8_character_count(
                view, konokashi::lrc::max_lyrics_text_chars) >
            konokashi::lrc::max_lyrics_text_chars) {
          return oversized_result(text);
        }
        return parse_encoded(view, text, duration_ms);
      },
      py::arg("text"), py::arg("duration_ms") = py::none());

  module.def(
      "parse",
      [](const py::bytes &text, std::optional<std::int64_t> duration_ms,
         const py::str &source) {
        const auto character_count = static_cast<std::size_t>(py::len(source));
        if (character_count > konokashi::lrc::max_lyrics_text_chars) {
          return oversized_result(source);
        }
        char *encoded = nullptr;
        Py_ssize_t encoded_size = 0;
        if (PyBytes_AsStringAndSize(text.ptr(), &encoded, &encoded_size) != 0) {
          throw py::error_already_set();
        }
        const auto byte_count = static_cast<std::size_t>(encoded_size);
        if (byte_count < character_count || byte_count > character_count * 4U) {
          throw std::invalid_argument(
              "UTF-8 byte size is inconsistent with the character count");
        }
        // The Python wrapper is the sole caller of this arity and obtains the
        // bytes from the supplied immutable str. Holding that str lets the
        // wrapper release the transient encoding before materializing records.
        // The two-argument bytes overload remains fully validating for direct
        // diagnostic use.
        return parse_encoded(std::string_view(encoded, byte_count), source,
                             duration_ms);
      },
      py::arg("text"), py::arg("duration_ms"), py::arg("source"));
}
