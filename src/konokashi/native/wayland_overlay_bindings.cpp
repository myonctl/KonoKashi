#if defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wshadow"
#endif
#include <pybind11/pybind11.h>
#if defined(__GNUC__)
#pragma GCC diagnostic pop
#endif

#include <LayerShellQt/Window>

#include <QMargins>
#include <QSize>
#include <QString>
#include <QWindow>

#include <wayland-client.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <utility>

namespace py = pybind11;

namespace {

struct ProtocolCapabilities {
  std::uint32_t layer_shell_version = 0;
  std::uint32_t background_effect_version = 0;
};

void registry_global(void *data, wl_registry *, const std::uint32_t,
                     const char *interface, const std::uint32_t version) {
  auto &capabilities = *static_cast<ProtocolCapabilities *>(data);
  if (std::strcmp(interface, "zwlr_layer_shell_v1") == 0) {
    capabilities.layer_shell_version = version;
  } else if (std::strcmp(interface, "ext_background_effect_manager_v1") == 0) {
    capabilities.background_effect_version = version;
  }
}

void registry_global_remove(void *, wl_registry *, const std::uint32_t) {}

constexpr wl_registry_listener registry_listener{registry_global,
                                                 registry_global_remove};

[[nodiscard]] ProtocolCapabilities probe_protocols() {
  wl_display *display = wl_display_connect(nullptr);
  if (display == nullptr) {
    return {};
  }
  wl_registry *registry = wl_display_get_registry(display);
  if (registry == nullptr) {
    wl_display_disconnect(display);
    return {};
  }
  ProtocolCapabilities capabilities;
  if (wl_registry_add_listener(registry, &registry_listener, &capabilities) != 0 ||
      wl_display_roundtrip(display) < 0) {
    capabilities = {};
  }
  wl_registry_destroy(registry);
  wl_display_disconnect(display);
  return capabilities;
}

[[nodiscard]] std::int32_t checked_coordinate(const std::int64_t value) {
  if (value < std::numeric_limits<std::int32_t>::min() ||
      value > std::numeric_limits<std::int32_t>::max()) {
    throw std::out_of_range("layer-shell margin exceeds signed 32-bit range");
  }
  return static_cast<std::int32_t>(value);
}

[[nodiscard]] int checked_dimension(const std::int64_t value) {
  if (value <= 0 || value > std::numeric_limits<int>::max()) {
    throw std::out_of_range("layer-shell size must be a positive Qt dimension");
  }
  return static_cast<int>(value);
}

[[nodiscard]] bool configure_locked_overlay(
    const std::uintptr_t window_address, const std::int64_t left,
    const std::int64_t top, const std::int64_t width,
    const std::int64_t height) {
  if (window_address == 0U) {
    return false;
  }
  auto *window = reinterpret_cast<QWindow *>(window_address);
  auto *surface = LayerShellQt::Window::get(window);
  if (surface == nullptr) {
    return false;
  }
  surface->setLayer(LayerShellQt::Window::LayerOverlay);
  surface->setAnchors(
      LayerShellQt::Window::Anchors{LayerShellQt::Window::AnchorTop} |
      LayerShellQt::Window::AnchorLeft);
  surface->setExclusiveZone(-1);
  surface->setMargins(
      QMargins(checked_coordinate(left), checked_coordinate(top), 0, 0));
  surface->setDesiredSize(
      QSize(checked_dimension(width), checked_dimension(height)));
  surface->setKeyboardInteractivity(
      LayerShellQt::Window::KeyboardInteractivityNone);
  surface->setScope(QStringLiteral("konokashi-floating-lyrics"));
  surface->setCloseOnDismissed(false);
  surface->setActivateOnShow(false);
  surface->setScreen(window->screen());
  return true;
}

} // namespace

PYBIND11_MODULE(_wayland_overlay_native, module) {
  module.doc() = "Optional LayerShellQt bridge for locked floating lyrics";
  module.def("probe", []() {
    py::gil_scoped_release release;
    const auto capabilities = probe_protocols();
    return std::pair(capabilities.layer_shell_version,
                     capabilities.background_effect_version);
  });
  module.def("configure_locked_overlay", &configure_locked_overlay,
             py::arg("window_address"), py::arg("left"), py::arg("top"),
             py::arg("width"), py::arg("height"));
  module.attr("plugin_path") = KONOKASHI_LAYER_SHELL_PLUGIN_PATH;
}
