"""Accepted Stage 2 settings defaults, separate from persisted values."""

from lyricflow.domain.tracks import PlayerSelectionConfig


def default_player_selection_config() -> PlayerSelectionConfig:
    """Return the documented default when no durable player setting exists."""

    return PlayerSelectionConfig()
