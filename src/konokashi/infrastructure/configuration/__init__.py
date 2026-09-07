"""XDG configuration adapters for the canonical settings service."""

from konokashi.infrastructure.configuration.bootstrap import open_settings
from konokashi.infrastructure.configuration.paths import default_config_path
from konokashi.infrastructure.configuration.toml_file import TomlSettingsFile

__all__ = ["TomlSettingsFile", "default_config_path", "open_settings"]
