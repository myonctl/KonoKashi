"""Freedesktop-compliant paths for human-editable configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def default_config_path(
    *, environment: Mapping[str, str] | None = None, home: Path | None = None
) -> Path:
    """Resolve ``$XDG_CONFIG_HOME/lyriflux/config.toml`` without writing."""

    values = os.environ if environment is None else environment
    configured = values.get("XDG_CONFIG_HOME")
    base = (
        Path(configured)
        if configured and Path(configured).is_absolute()
        else (Path.home() if home is None else home) / ".config"
    )
    return base / "lyriflux" / "config.toml"
