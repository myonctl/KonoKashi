"""Canonical frontend-neutral settings values, schema, and validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TypeAlias, cast

from konokashi.domain.library import LibrarySettings
from konokashi.domain.representations import RepresentationDisplaySettings
from konokashi.domain.tracks import PlayerSelectionConfig


@dataclass(frozen=True, slots=True)
class DesktopInteractionSettings:
    """Opt-in desktop interaction mechanics independent of lyric content."""

    allow_lyric_selection: bool = False


class SettingType(Enum):
    """Primitive configuration types supported by the Stage 11 schema."""

    BOOLEAN = "boolean"
    INTEGER = "integer"
    STRING_LIST = "string-list"


class SettingScope(Enum):
    """Semantic owner of a setting, independent of its storage adapter."""

    GLOBAL = "global"
    DESKTOP = "desktop"


class SettingCategory(Enum):
    """Stable semantic grouping shared by settings frontends."""

    PLAYERS = "Players"
    LYRICS = "Lyrics"
    DESKTOP = "Desktop"
    LIBRARY = "Library"


class ReloadBehavior(Enum):
    """When a validated setting can affect a running consumer."""

    LIVE = "live"
    NEXT_OPERATION = "next-operation"
    RESTART = "restart"


class SettingOrigin(Enum):
    """Why an effective setting has its current value."""

    DEFAULT = "built-in-default"
    CONFIG_FILE = "config-file"
    LEGACY_MIGRATION = "legacy-sqlite-migration"


SettingValue: TypeAlias = bool | int | tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SettingDefinition:
    """Stable schema metadata reusable by CLI and future settings frontends."""

    key: str
    value_type: SettingType
    default: SettingValue
    scope: SettingScope
    reload: ReloadBehavior
    title: str
    category: SettingCategory
    description: str
    minimum: int | None = None
    maximum: int | None = None


SETTINGS_SCHEMA_VERSION = 1

SETTINGS_SCHEMA: tuple[SettingDefinition, ...] = (
    SettingDefinition(
        "players.preferred",
        SettingType.STRING_LIST,
        (),
        SettingScope.GLOBAL,
        ReloadBehavior.LIVE,
        "Preferred players",
        SettingCategory.PLAYERS,
        "Ordered MPRIS player selectors preferred after playback state.",
    ),
    SettingDefinition(
        "players.ignored",
        SettingType.STRING_LIST,
        (),
        SettingScope.GLOBAL,
        ReloadBehavior.LIVE,
        "Ignored players",
        SettingCategory.PLAYERS,
        "MPRIS player selectors excluded before selection scoring.",
    ),
    SettingDefinition(
        "lyrics.display.original",
        SettingType.BOOLEAN,
        True,
        SettingScope.GLOBAL,
        ReloadBehavior.LIVE,
        "Show original lyrics",
        SettingCategory.LYRICS,
        "Show the canonical original lyric representation.",
    ),
    SettingDefinition(
        "lyrics.display.romanized",
        SettingType.BOOLEAN,
        True,
        SettingScope.GLOBAL,
        ReloadBehavior.LIVE,
        "Show romanization or transliteration",
        SettingCategory.LYRICS,
        "Show available romanized or transliterated lyric representations.",
    ),
    SettingDefinition(
        "lyrics.display.translated",
        SettingType.BOOLEAN,
        False,
        SettingScope.GLOBAL,
        ReloadBehavior.LIVE,
        "Show translation",
        SettingCategory.LYRICS,
        "Show an available aligned translated lyric representation.",
    ),
    SettingDefinition(
        "desktop.lyrics.selectable",
        SettingType.BOOLEAN,
        False,
        SettingScope.DESKTOP,
        ReloadBehavior.LIVE,
        "Allow lyric text selection",
        SettingCategory.DESKTOP,
        "Allow mouse and keyboard selection of lyric text in the desktop.",
    ),
    SettingDefinition(
        "library.roots",
        SettingType.STRING_LIST,
        (),
        SettingScope.GLOBAL,
        ReloadBehavior.NEXT_OPERATION,
        "Music library folders",
        SettingCategory.LIBRARY,
        "Absolute non-overlapping directories included in a library scan.",
    ),
    SettingDefinition(
        "library.automatic_downloads",
        SettingType.BOOLEAN,
        False,
        SettingScope.GLOBAL,
        ReloadBehavior.NEXT_OPERATION,
        "Download high-confidence lyrics",
        SettingCategory.LIBRARY,
        "Allow policy-approved High/Approved lyric downloads during a scan.",
    ),
    SettingDefinition(
        "library.metadata_workers",
        SettingType.INTEGER,
        4,
        SettingScope.GLOBAL,
        ReloadBehavior.NEXT_OPERATION,
        "Metadata workers",
        SettingCategory.LIBRARY,
        "Bounded metadata workers used by the next library scan.",
        minimum=1,
        maximum=8,
    ),
)

SETTINGS_BY_KEY = MappingProxyType(
    {definition.key: definition for definition in SETTINGS_SCHEMA}
)


@dataclass(frozen=True, slots=True)
class ResolvedSetting:
    """One effective value plus schema metadata and precedence evidence."""

    definition: SettingDefinition
    value: SettingValue
    origin: SettingOrigin


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    """One immutable, internally consistent canonical settings snapshot."""

    schema_version: int
    values: tuple[ResolvedSetting, ...]

    def resolved(self, key: str) -> ResolvedSetting:
        """Return one known setting or raise an actionable schema error."""

        for item in self.values:
            if item.definition.key == key:
                return item
        raise SettingsValidationError((unknown_setting_diagnostic(key),))

    def get(self, key: str) -> SettingValue:
        return self.resolved(key).value

    @property
    def player_selection(self) -> PlayerSelectionConfig:
        return PlayerSelectionConfig(
            cast(tuple[str, ...], self.get("players.preferred")),
            cast(tuple[str, ...], self.get("players.ignored")),
        )

    @property
    def representation_display(self) -> RepresentationDisplaySettings:
        return RepresentationDisplaySettings(
            cast(bool, self.get("lyrics.display.original")),
            cast(bool, self.get("lyrics.display.romanized")),
            cast(bool, self.get("lyrics.display.translated")),
        )

    @property
    def desktop_interaction(self) -> DesktopInteractionSettings:
        return DesktopInteractionSettings(
            cast(bool, self.get("desktop.lyrics.selectable"))
        )

    @property
    def library(self) -> LibrarySettings:
        return LibrarySettings(
            cast(tuple[str, ...], self.get("library.roots")),
            cast(bool, self.get("library.automatic_downloads")),
            cast(int, self.get("library.metadata_workers")),
        )

    def plain_values(self) -> dict[str, SettingValue]:
        return {item.definition.key: item.value for item in self.values}


@dataclass(frozen=True, slots=True)
class SettingsDiagnostic:
    """One actionable syntax, schema, or reload problem."""

    message: str
    key: str | None = None
    path: Path | None = None

    def render(self) -> str:
        location = str(self.path) if self.path is not None else "configuration"
        if self.key is not None:
            location = f"{location}: {self.key}"
        return f"{location}: {self.message}"


class SettingsValidationError(ValueError):
    """Reject a complete candidate without partially applying it."""

    def __init__(self, diagnostics: tuple[SettingsDiagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__("; ".join(item.render() for item in diagnostics))


def unknown_setting_diagnostic(
    key: str, *, path: Path | None = None
) -> SettingsDiagnostic:
    """Suggest the closest stable key when an unknown value is encountered."""

    matches = get_close_matches(key, SETTINGS_BY_KEY, n=1, cutoff=0.6)
    suggestion = f" Did you mean `{matches[0]}`?" if matches else ""
    return SettingsDiagnostic(
        f"Unknown setting `{key}`.{suggestion}", key=key, path=path
    )


def default_settings_snapshot(
    *, origin: SettingOrigin = SettingOrigin.DEFAULT
) -> SettingsSnapshot:
    """Return the schema defaults as a typed immutable snapshot."""

    return SettingsSnapshot(
        SETTINGS_SCHEMA_VERSION,
        tuple(
            ResolvedSetting(definition, definition.default, origin)
            for definition in SETTINGS_SCHEMA
        ),
    )


def validate_settings_values(
    values: Mapping[str, object],
    *,
    explicit_keys: frozenset[str] = frozenset(),
    explicit_origin: SettingOrigin = SettingOrigin.CONFIG_FILE,
    path: Path | None = None,
) -> SettingsSnapshot:
    """Validate one complete flattened candidate and construct typed values."""

    diagnostics: list[SettingsDiagnostic] = []
    for key in values:
        if key not in SETTINGS_BY_KEY:
            diagnostics.append(unknown_setting_diagnostic(key, path=path))
    resolved: list[ResolvedSetting] = []
    for definition in SETTINGS_SCHEMA:
        value = values.get(definition.key, definition.default)
        error = _validate_value(definition, value)
        if error is not None:
            diagnostics.append(SettingsDiagnostic(error, definition.key, path))
            continue
        resolved.append(
            ResolvedSetting(
                definition,
                _canonical_value(definition, value),
                explicit_origin
                if definition.key in explicit_keys
                else SettingOrigin.DEFAULT,
            )
        )
    if diagnostics:
        raise SettingsValidationError(tuple(diagnostics))
    snapshot = SettingsSnapshot(SETTINGS_SCHEMA_VERSION, tuple(resolved))
    try:
        _ = snapshot.player_selection
        _ = snapshot.library
    except ValueError as error:
        raise SettingsValidationError(
            (SettingsDiagnostic(str(error), path=path),)
        ) from error
    return snapshot


def _canonical_value(definition: SettingDefinition, value: object) -> SettingValue:
    if definition.value_type is SettingType.STRING_LIST:
        return tuple(cast(list[str] | tuple[str, ...], value))
    return cast(bool | int, value)


def _validate_value(definition: SettingDefinition, value: object) -> str | None:
    expected = definition.value_type
    if expected is SettingType.BOOLEAN and type(value) is not bool:
        return (
            f"Expected boolean, got {type(value).__name__} {_render_bad_value(value)}."
        )
    if expected is SettingType.INTEGER and type(value) is not int:
        return (
            f"Expected integer, got {type(value).__name__} {_render_bad_value(value)}."
        )
    if expected is SettingType.STRING_LIST and (
        not isinstance(value, (list, tuple))
        or any(type(item) is not str for item in value)
    ):
        return f"Expected an array of strings, got {_render_bad_value(value)}."
    if type(value) is int:
        if definition.minimum is not None and value < definition.minimum:
            return f"Expected an integer of at least {definition.minimum}, got {value}."
        if definition.maximum is not None and value > definition.maximum:
            return f"Expected an integer of at most {definition.maximum}, got {value}."
    return None


def _render_bad_value(value: object) -> str:
    rendered = repr(value)
    return rendered if len(rendered) <= 120 else f"{rendered[:117]}..."


def default_player_selection_config() -> PlayerSelectionConfig:
    """Return the documented default when no durable player setting exists."""

    return PlayerSelectionConfig()


def default_representation_display_settings() -> RepresentationDisplaySettings:
    """Keep original and romanized layers visible; translation is opt-in."""

    return RepresentationDisplaySettings()


def default_desktop_interaction_settings() -> DesktopInteractionSettings:
    """Keep lyrics passive until text selection is explicitly enabled."""

    return DesktopInteractionSettings()


def default_library_settings() -> LibrarySettings:
    """Keep scanning and downloads dormant until roots are explicitly configured."""

    return LibrarySettings()
