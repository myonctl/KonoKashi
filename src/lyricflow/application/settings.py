"""Application settings values and defaults, separate from persistence."""

from dataclasses import dataclass

from lyricflow.domain.representations import RepresentationDisplaySettings
from lyricflow.domain.tracks import PlayerSelectionConfig


@dataclass(frozen=True, slots=True)
class DesktopInteractionSettings:
    """Opt-in desktop interaction mechanics independent of lyric content."""

    allow_lyric_selection: bool = False


def default_player_selection_config() -> PlayerSelectionConfig:
    """Return the documented default when no durable player setting exists."""

    return PlayerSelectionConfig()


def default_representation_display_settings() -> RepresentationDisplaySettings:
    """Keep original and romanized layers visible; translation is opt-in."""

    return RepresentationDisplaySettings()


def default_desktop_interaction_settings() -> DesktopInteractionSettings:
    """Keep lyrics passive until text selection is explicitly enabled."""

    return DesktopInteractionSettings()
