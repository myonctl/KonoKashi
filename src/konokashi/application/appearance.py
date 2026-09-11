"""Frontend-neutral semantic appearance profiles and declarative presets."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import cast


class AppearancePreset(Enum):
    DEFAULT = "default"
    COMPACT = "compact"
    LYRIC_ONLY = "lyric-only"
    CURRENT_LINE = "current-line"
    LARGE_DISPLAY = "large-display"


class TextAlignment(Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


@dataclass(frozen=True, slots=True)
class TextStyle:
    """Portable font intent; an empty family delegates to the frontend/system."""

    family: str
    size: int
    weight: int
    italic: bool = False


@dataclass(frozen=True, slots=True)
class AppearanceColors:
    active_lyric: str
    inactive_lyric: str
    original_lyric: str
    romanization: str
    translation: str
    metadata_primary: str
    metadata_secondary: str
    background: str
    foreground: str
    accent: str
    progress: str
    status: str
    muted: str
    selection: str


@dataclass(frozen=True, slots=True)
class AppearanceOpacity:
    content: int
    background: int
    inactive_line: int
    metadata: int
    secondary_representation: int


@dataclass(frozen=True, slots=True)
class AppearanceSpacing:
    outer_margin: int
    lyric_padding: int
    line: int
    representation: int
    metadata: int
    progress: int
    context: int
    maximum_lyric_width: int


@dataclass(frozen=True, slots=True)
class AppearanceVisibility:
    title: bool
    artist: bool
    album: bool
    source: bool
    playback_status: bool
    progress: bool
    timestamps: bool
    original: bool
    romanization: bool
    translation: bool
    inactive_context: bool
    auxiliary_status: bool
    chrome: bool


@dataclass(frozen=True, slots=True)
class LyricContext:
    previous: int
    following: int


@dataclass(frozen=True, slots=True)
class MotionPreferences:
    smooth_scrolling: bool
    transition_ms: int
    emphasis_transition_ms: int
    reduced_motion: bool

    @property
    def effective_transition_ms(self) -> int:
        return (
            0
            if self.reduced_motion or not self.smooth_scrolling
            else self.transition_ms
        )

    @property
    def effective_emphasis_transition_ms(self) -> int:
        return (
            0
            if self.reduced_motion or not self.smooth_scrolling
            else self.emphasis_transition_ms
        )


@dataclass(frozen=True, slots=True)
class ProgressAppearance:
    """Presentation-neutral geometry and track treatment for playback progress."""

    thickness: int
    track_color: str
    opacity: int
    corner_radius: int


@dataclass(frozen=True, slots=True)
class AppearanceProfile:
    """Resolved immutable semantics shared by every current/future frontend."""

    preset: AppearancePreset
    original: TextStyle
    romanization: TextStyle
    translation: TextStyle
    metadata: TextStyle
    status: TextStyle
    active_size_percent: int
    inactive_size_percent: int
    colors: AppearanceColors
    opacity: AppearanceOpacity
    spacing: AppearanceSpacing
    lyric_alignment: TextAlignment
    metadata_alignment: TextAlignment
    visibility: AppearanceVisibility
    context: LyricContext
    motion: MotionPreferences
    progress: ProgressAppearance
    lyric_scale_percent: int


_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")


def normalize_color(value: str) -> str:
    """Validate conventional RGB/RGBA notation and return deterministic uppercase."""

    if not _COLOR_PATTERN.fullmatch(value):
        raise ValueError("Expected #RRGGBB or #RRGGBBAA.")
    return value.upper()


DEFAULT_APPEARANCE_VALUES: Mapping[str, bool | int | str] = MappingProxyType(
    {
        "appearance.preset": "default",
        "appearance.typography.lyric_scale_percent": 100,
        "appearance.progress.thickness": 4,
        "appearance.progress.track_color": "#FFFFFF20",
        "appearance.progress.opacity": 100,
        "appearance.progress.corner_radius": 2,
        "appearance.typography.original.family": "",
        "appearance.typography.original.size": 22,
        "appearance.typography.original.weight": 600,
        "appearance.typography.original.italic": False,
        "appearance.typography.romanization.family": "",
        "appearance.typography.romanization.size": 16,
        "appearance.typography.romanization.weight": 400,
        "appearance.typography.romanization.italic": False,
        "appearance.typography.translation.family": "",
        "appearance.typography.translation.size": 15,
        "appearance.typography.translation.weight": 400,
        "appearance.typography.translation.italic": False,
        "appearance.typography.metadata.family": "",
        "appearance.typography.metadata.size": 11,
        "appearance.typography.metadata.weight": 600,
        "appearance.typography.status.family": "",
        "appearance.typography.status.size": 10,
        "appearance.typography.status.weight": 400,
        "appearance.typography.active_size_percent": 115,
        "appearance.typography.inactive_size_percent": 90,
        "appearance.colors.active_lyric": "#39B9C7",
        "appearance.colors.inactive_lyric": "#8B949E",
        "appearance.colors.original_lyric": "#F1F3F4",
        "appearance.colors.romanization": "#C9D1D9",
        "appearance.colors.translation": "#AEB6BF",
        "appearance.colors.metadata_primary": "#F1F3F4",
        "appearance.colors.metadata_secondary": "#8B949E",
        "appearance.colors.background": "#202124",
        "appearance.colors.foreground": "#F1F3F4",
        "appearance.colors.accent": "#39B9C7",
        "appearance.colors.progress": "#39B9C7",
        "appearance.colors.status": "#C9D1D9",
        "appearance.colors.muted": "#8B949E",
        "appearance.colors.selection": "#39B9C7",
        "appearance.opacity.content": 100,
        "appearance.opacity.background": 100,
        "appearance.opacity.inactive_line": 60,
        "appearance.opacity.metadata": 100,
        "appearance.opacity.secondary_representation": 85,
        "appearance.spacing.outer_margin": 20,
        "appearance.spacing.lyric_padding": 8,
        "appearance.spacing.line": 8,
        "appearance.spacing.representation": 2,
        "appearance.spacing.metadata": 4,
        "appearance.spacing.progress": 12,
        "appearance.spacing.context": 12,
        "appearance.spacing.maximum_lyric_width": 1040,
        "appearance.alignment.lyrics": "center",
        "appearance.alignment.metadata": "left",
        "appearance.visibility.title": True,
        "appearance.visibility.artist": True,
        "appearance.visibility.album": True,
        "appearance.visibility.source": True,
        "appearance.visibility.playback_status": True,
        "appearance.visibility.progress": True,
        "appearance.visibility.timestamps": True,
        "appearance.visibility.inactive_context": True,
        "appearance.visibility.auxiliary_status": True,
        "appearance.visibility.chrome": True,
        "appearance.context.previous": 2,
        "appearance.context.following": 2,
        "appearance.motion.smooth_scrolling": True,
        "appearance.motion.transition_ms": 180,
        "appearance.motion.emphasis_transition_ms": 120,
        "appearance.motion.reduced": False,
    }
)


APPEARANCE_PRESETS: Mapping[AppearancePreset, Mapping[str, bool | int | str]] = (
    MappingProxyType(
        {
            AppearancePreset.DEFAULT: MappingProxyType({}),
            AppearancePreset.COMPACT: MappingProxyType(
                {
                    "appearance.typography.original.size": 16,
                    "appearance.typography.romanization.size": 12,
                    "appearance.typography.translation.size": 11,
                    "appearance.spacing.outer_margin": 8,
                    "appearance.spacing.lyric_padding": 2,
                    "appearance.spacing.line": 3,
                    "appearance.spacing.context": 5,
                    "appearance.spacing.maximum_lyric_width": 720,
                    "appearance.visibility.album": False,
                    "appearance.context.previous": 3,
                    "appearance.context.following": 3,
                }
            ),
            AppearancePreset.LYRIC_ONLY: MappingProxyType(
                {
                    "appearance.typography.original.size": 28,
                    "appearance.spacing.outer_margin": 10,
                    "appearance.alignment.lyrics": "center",
                    "appearance.visibility.title": False,
                    "appearance.visibility.artist": False,
                    "appearance.visibility.album": False,
                    "appearance.visibility.source": False,
                    "appearance.visibility.playback_status": False,
                    "appearance.visibility.progress": False,
                    "appearance.visibility.timestamps": False,
                    "appearance.visibility.auxiliary_status": False,
                    "appearance.visibility.chrome": False,
                }
            ),
            AppearancePreset.CURRENT_LINE: MappingProxyType(
                {
                    "appearance.typography.original.size": 34,
                    "appearance.typography.romanization.size": 22,
                    "appearance.typography.translation.size": 20,
                    "appearance.visibility.inactive_context": False,
                    "appearance.context.previous": 0,
                    "appearance.context.following": 0,
                    "appearance.alignment.lyrics": "center",
                }
            ),
            AppearancePreset.LARGE_DISPLAY: MappingProxyType(
                {
                    "appearance.typography.original.size": 44,
                    "appearance.typography.original.weight": 700,
                    "appearance.typography.romanization.size": 28,
                    "appearance.typography.translation.size": 24,
                    "appearance.spacing.outer_margin": 28,
                    "appearance.spacing.line": 14,
                    "appearance.spacing.maximum_lyric_width": 1600,
                    "appearance.context.previous": 1,
                    "appearance.context.following": 1,
                    "appearance.alignment.lyrics": "center",
                    "appearance.alignment.metadata": "center",
                }
            ),
        }
    )
)


def resolve_appearance(
    values: Mapping[str, object], *, explicit_keys: frozenset[str] = frozenset()
) -> AppearanceProfile:
    """Resolve defaults, one declarative preset, then explicit user overrides."""

    preset = AppearancePreset(cast(str, values.get("appearance.preset", "default")))
    resolved = dict(DEFAULT_APPEARANCE_VALUES)
    resolved.update(APPEARANCE_PRESETS[preset])
    for key in explicit_keys:
        if key in DEFAULT_APPEARANCE_VALUES and key in values:
            resolved[key] = cast(bool | int | str, values[key])
    resolved["appearance.preset"] = preset.value

    def text_style(name: str, *, italic: bool = True) -> TextStyle:
        prefix = f"appearance.typography.{name}"
        return TextStyle(
            cast(str, resolved[f"{prefix}.family"]),
            cast(int, resolved[f"{prefix}.size"]),
            cast(int, resolved[f"{prefix}.weight"]),
            cast(bool, resolved.get(f"{prefix}.italic", False)) if italic else False,
        )

    return AppearanceProfile(
        preset,
        text_style("original"),
        text_style("romanization"),
        text_style("translation"),
        text_style("metadata", italic=False),
        text_style("status", italic=False),
        cast(int, resolved["appearance.typography.active_size_percent"]),
        cast(int, resolved["appearance.typography.inactive_size_percent"]),
        AppearanceColors(
            *(
                cast(str, resolved[key])
                for key in (
                    "appearance.colors.active_lyric",
                    "appearance.colors.inactive_lyric",
                    "appearance.colors.original_lyric",
                    "appearance.colors.romanization",
                    "appearance.colors.translation",
                    "appearance.colors.metadata_primary",
                    "appearance.colors.metadata_secondary",
                    "appearance.colors.background",
                    "appearance.colors.foreground",
                    "appearance.colors.accent",
                    "appearance.colors.progress",
                    "appearance.colors.status",
                    "appearance.colors.muted",
                    "appearance.colors.selection",
                )
            )
        ),
        AppearanceOpacity(
            *(
                cast(int, resolved[key])
                for key in (
                    "appearance.opacity.content",
                    "appearance.opacity.background",
                    "appearance.opacity.inactive_line",
                    "appearance.opacity.metadata",
                    "appearance.opacity.secondary_representation",
                )
            )
        ),
        AppearanceSpacing(
            *(
                cast(int, resolved[key])
                for key in (
                    "appearance.spacing.outer_margin",
                    "appearance.spacing.lyric_padding",
                    "appearance.spacing.line",
                    "appearance.spacing.representation",
                    "appearance.spacing.metadata",
                    "appearance.spacing.progress",
                    "appearance.spacing.context",
                    "appearance.spacing.maximum_lyric_width",
                )
            )
        ),
        TextAlignment(cast(str, resolved["appearance.alignment.lyrics"])),
        TextAlignment(cast(str, resolved["appearance.alignment.metadata"])),
        AppearanceVisibility(
            cast(bool, resolved["appearance.visibility.title"]),
            cast(bool, resolved["appearance.visibility.artist"]),
            cast(bool, resolved["appearance.visibility.album"]),
            cast(bool, resolved["appearance.visibility.source"]),
            cast(bool, resolved["appearance.visibility.playback_status"]),
            cast(bool, resolved["appearance.visibility.progress"]),
            cast(bool, resolved["appearance.visibility.timestamps"]),
            cast(bool, values["lyrics.display.original"]),
            cast(bool, values["lyrics.display.romanized"]),
            cast(bool, values["lyrics.display.translated"]),
            cast(bool, resolved["appearance.visibility.inactive_context"]),
            cast(bool, resolved["appearance.visibility.auxiliary_status"]),
            cast(bool, resolved["appearance.visibility.chrome"]),
        ),
        LyricContext(
            cast(int, resolved["appearance.context.previous"]),
            cast(int, resolved["appearance.context.following"]),
        ),
        MotionPreferences(
            cast(bool, resolved["appearance.motion.smooth_scrolling"]),
            cast(int, resolved["appearance.motion.transition_ms"]),
            cast(int, resolved["appearance.motion.emphasis_transition_ms"]),
            cast(bool, resolved["appearance.motion.reduced"]),
        ),
        ProgressAppearance(
            cast(int, resolved["appearance.progress.thickness"]),
            cast(str, resolved["appearance.progress.track_color"]),
            cast(int, resolved["appearance.progress.opacity"]),
            cast(int, resolved["appearance.progress.corner_radius"]),
        ),
        cast(int, resolved["appearance.typography.lyric_scale_percent"]),
    )


def default_appearance_profile() -> AppearanceProfile:
    """Return the canonical default without importing the settings schema."""

    values: dict[str, object] = dict(DEFAULT_APPEARANCE_VALUES)
    values.update(
        {
            "lyrics.display.original": True,
            "lyrics.display.romanized": True,
            "lyrics.display.translated": True,
        }
    )
    return resolve_appearance(values)
