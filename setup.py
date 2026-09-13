"""Build bounded native core modules and an optional Wayland surface bridge."""

import os
from pathlib import Path

from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup

SOURCE_ROOT = Path(__file__).resolve().parent


def _layer_shell_extension() -> Pybind11Extension | None:
    """Build the optional bridge only when a complete native SDK is present."""

    requested = os.environ.get("KONOKASHI_LAYER_SHELL", "auto").strip().lower()
    if requested in {"0", "false", "no", "off"}:
        return None
    configured_prefix = os.environ.get("KONOKASHI_LAYER_SHELL_PREFIX")
    prefixes = tuple(
        Path(value)
        for value in (configured_prefix, "/app", "/usr")
        if value is not None
    )
    for prefix in prefixes:
        header = prefix / "include" / "LayerShellQt" / "Window"
        library = prefix / "lib" / "libLayerShellQtInterface.so"
        qt_header_candidates = (
            prefix / "include" / "qt6",
            Path("/usr/include/qt6"),
            Path("/usr/include"),
        )
        qt_headers = next(
            (
                path
                for path in qt_header_candidates
                if (path / "QtCore" / "QObject").is_file()
            ),
            None,
        )
        if not (header.is_file() and library.is_file() and qt_headers is not None):
            continue
        plugin_candidates = (
            prefix / "lib" / "qt6" / "plugins",
            prefix / "lib" / "plugins",
        )
        plugin_path = next(
            (path for path in plugin_candidates if path.is_dir()),
            plugin_candidates[0],
        )
        return Pybind11Extension(
            "konokashi._wayland_overlay_native",
            ["src/konokashi/native/wayland_overlay_bindings.cpp"],
            include_dirs=[
                str(prefix / "include"),
                str(qt_headers),
                str(qt_headers / "QtCore"),
                str(qt_headers / "QtGui"),
            ],
            library_dirs=[str(prefix / "lib")],
            libraries=[
                "LayerShellQtInterface",
                "wayland-client",
                "Qt6Gui",
                "Qt6Core",
            ],
            define_macros=[
                (
                    "KONOKASHI_LAYER_SHELL_PLUGIN_PATH",
                    f'"{plugin_path}"',
                )
            ],
            cxx_std=20,
            extra_compile_args=[
                f"-ffile-prefix-map={SOURCE_ROOT}=.",
                f"-fdebug-prefix-map={SOURCE_ROOT}=.",
                "-frandom-seed=konokashi-wayland-overlay",
                "-Wconversion",
                "-Werror",
                "-Wextra",
                "-Wshadow",
                "-Wsign-conversion",
            ],
            extra_link_args=["-Wl,--build-id=none", "-s"],
        )
    if requested in {"1", "true", "yes", "on", "required"}:
        raise RuntimeError(
            "KONOKASHI_LAYER_SHELL requires LayerShellQt and Qt 6 development files"
        )
    return None


extensions = [
    Pybind11Extension(
        "konokashi._playback_clock_native",
        ["src/konokashi/native/playback_clock.cpp"],
        cxx_std=20,
        extra_compile_args=[
            f"-ffile-prefix-map={SOURCE_ROOT}=.",
            f"-fdebug-prefix-map={SOURCE_ROOT}=.",
            "-frandom-seed=konokashi-playback-clock",
            "-Wconversion",
            "-Werror",
            "-Wextra",
            "-Wshadow",
            "-Wsign-conversion",
        ],
        extra_link_args=["-Wl,--build-id=none", "-s"],
    ),
    Pybind11Extension(
        "konokashi._lrc_native",
        [
            "src/konokashi/native/lrc_bindings.cpp",
            "src/konokashi/native/lrc_parser.cpp",
        ],
        libraries=["crypto"],
        cxx_std=20,
        extra_compile_args=[
            f"-ffile-prefix-map={SOURCE_ROOT}=.",
            f"-fdebug-prefix-map={SOURCE_ROOT}=.",
            "-frandom-seed=konokashi-lrc-parser",
            "-Wconversion",
            "-Werror",
            "-Wextra",
            "-Wshadow",
            "-Wsign-conversion",
        ],
        extra_link_args=["-Wl,--build-id=none", "-s"],
    ),
]
layer_shell_extension = _layer_shell_extension()
if layer_shell_extension is not None:
    extensions.append(layer_shell_extension)

setup(
    ext_modules=extensions,
    cmdclass={"build_ext": build_ext},
)
