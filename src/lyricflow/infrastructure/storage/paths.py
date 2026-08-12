"""Pure XDG database path policy with explicit initialization elsewhere."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

_APPLICATION_DIRECTORY = "lyricflow"
_DATABASE_FILENAME = "lyricflow.sqlite3"


def default_database_path(
    *,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the deterministic XDG data path without touching the filesystem."""

    values = os.environ if environment is None else environment
    configured = values.get("XDG_DATA_HOME")
    if configured:
        configured_path = Path(configured)
        if configured_path.is_absolute():
            data_home = configured_path
        else:
            data_home = (home or Path.home()) / ".local" / "share"
    else:
        data_home = (home or Path.home()) / ".local" / "share"
    return data_home / _APPLICATION_DIRECTORY / _DATABASE_FILENAME
