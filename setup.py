"""Build the bounded native core modules."""

from pathlib import Path

from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup

SOURCE_ROOT = Path(__file__).resolve().parent

setup(
    ext_modules=[
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
    ],
    cmdclass={"build_ext": build_ext},
)
