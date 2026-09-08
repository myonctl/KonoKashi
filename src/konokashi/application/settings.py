"""Canonical frontend-neutral settings values, schema, and validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TypeAlias, cast

from konokashi.application.appearance import (
    DEFAULT_APPEARANCE_VALUES,
    AppearancePreset,
    AppearanceProfile,
    TextAlignment,
    normalize_color,
    resolve_appearance,
)
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
    STRING = "string"
    STRING_LIST = "string-list"


class SettingStringFormat(Enum):
    """Optional semantic validation/editor hint for string settings."""

    PLAIN = "plain"
    COLOR = "color"
    FONT_FAMILY = "font-family"


class SettingScope(Enum):
    """Semantic owner of a setting, independent of its storage adapter."""

    GLOBAL = "global"
    DESKTOP = "desktop"


class SettingCategory(Enum):
    """Stable semantic grouping shared by settings frontends."""

    PLAYERS = "Players / MPRIS"
    LYRICS = "Lyrics"
    DESKTOP = "Desktop"
    LIBRARY = "Library"
    APPEARANCE = "Presets & Defaults"
    TYPOGRAPHY = "Typography"
    COLORS = "Colors"
    LAYOUT = "Spacing"
    VISIBILITY = "Visibility"
    MOTION = "Motion"
    PROGRESS = "Progress"


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


SettingValue: TypeAlias = bool | int | str | tuple[str, ...]


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
    choices: tuple[str, ...] = ()
    string_format: SettingStringFormat = SettingStringFormat.PLAIN

    @property
    def section(self) -> str:
        """Shared navigation context derived from the canonical semantic key."""

        parts = self.key.split(".")
        if self.key == "appearance.typography.lyric_scale_percent":
            return "Quick adjustments"
        if parts[:2] == ["appearance", "typography"]:
            return parts[2].replace("_", " ").title() if len(parts) > 3 else "Emphasis"
        if parts[0] == "appearance" and len(parts) > 2:
            return parts[1].replace("_", " ").title()
        return self.category.value


def _appearance_definition(
    key: str,
    title: str,
    category: SettingCategory,
    description: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    choices: tuple[str, ...] = (),
    string_format: SettingStringFormat = SettingStringFormat.PLAIN,
) -> SettingDefinition:
    default = DEFAULT_APPEARANCE_VALUES[key]
    value_type = (
        SettingType.BOOLEAN
        if type(default) is bool
        else SettingType.INTEGER
        if type(default) is int
        else SettingType.STRING
    )
    return SettingDefinition(
        key,
        value_type,
        default,
        SettingScope.GLOBAL,
        ReloadBehavior.LIVE,
        title,
        SettingCategory.PROGRESS
        if key
        in {
            "appearance.colors.progress",
            "appearance.visibility.progress",
            "appearance.visibility.timestamps",
            "appearance.spacing.progress",
        }
        else category,
        description,
        minimum,
        maximum,
        choices,
        string_format,
    )


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
        "Prefer these players, from top to bottom, among equally active players. "
        "Use stable names such as strawberry; ignored players are always excluded.",
    ),
    SettingDefinition(
        "players.ignored",
        SettingType.STRING_LIST,
        (),
        SettingScope.GLOBAL,
        ReloadBehavior.LIVE,
        "Ignored players",
        SettingCategory.PLAYERS,
        "Never select these players, even if also preferred. Order does not matter. "
        "Use stable names such as plasma-browser-integration.",
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
    _appearance_definition(
        "appearance.preset",
        "Appearance preset",
        SettingCategory.APPEARANCE,
        "Choose a declarative starting profile; explicit custom values override it.",
        choices=tuple(item.value for item in AppearancePreset),
    ),
    *(
        _appearance_definition(
            f"appearance.typography.{layer}.family",
            f"{title} font family",
            SettingCategory.TYPOGRAPHY,
            "Installed font family; leave empty to use the system fallback chain.",
            string_format=SettingStringFormat.FONT_FAMILY,
        )
        for layer, title in (
            ("original", "Original lyric"),
            ("romanization", "Romanization"),
            ("translation", "Translation"),
            ("metadata", "Metadata"),
            ("status", "Status and progress"),
        )
    ),
    *(
        _appearance_definition(
            f"appearance.typography.{layer}.size",
            f"{title} font size",
            SettingCategory.TYPOGRAPHY,
            "Base point size before bounded active/inactive emphasis.",
            minimum=6,
            maximum=96,
        )
        for layer, title in (
            ("original", "Original lyric"),
            ("romanization", "Romanization"),
            ("translation", "Translation"),
            ("metadata", "Metadata"),
            ("status", "Status and progress"),
        )
    ),
    *(
        _appearance_definition(
            f"appearance.typography.{layer}.weight",
            f"{title} font weight",
            SettingCategory.TYPOGRAPHY,
            "Choose how light or bold this text appears in the selected font.",
            minimum=100,
            maximum=900,
        )
        for layer, title in (
            ("original", "Original lyric"),
            ("romanization", "Romanization"),
            ("translation", "Translation"),
            ("metadata", "Metadata"),
            ("status", "Status and progress"),
        )
    ),
    *(
        _appearance_definition(
            f"appearance.typography.{layer}.italic",
            f"Italic {title.lower()}",
            SettingCategory.TYPOGRAPHY,
            f"Render {title.lower()} in italic when the selected font supports it.",
        )
        for layer, title in (
            ("original", "Original lyrics"),
            ("romanization", "Romanization"),
            ("translation", "Translation"),
        )
    ),
    _appearance_definition(
        "appearance.typography.active_size_percent",
        "Active-line size emphasis",
        SettingCategory.TYPOGRAPHY,
        "Scale the current line relative to each representation's base size.",
        minimum=80,
        maximum=160,
    ),
    _appearance_definition(
        "appearance.typography.inactive_size_percent",
        "Inactive-line size",
        SettingCategory.TYPOGRAPHY,
        "Scale context lines without changing size on playback clock ticks.",
        minimum=60,
        maximum=120,
    ),
    *(
        _appearance_definition(
            key,
            title,
            SettingCategory.COLORS,
            description,
            string_format=SettingStringFormat.COLOR,
        )
        for key, title, description in (
            (
                "appearance.colors.active_lyric",
                "Active lyric color",
                "Color emphasis applied to the current lyric line.",
            ),
            (
                "appearance.colors.inactive_lyric",
                "Inactive lyric color",
                "Color emphasis applied to surrounding lyric lines.",
            ),
            (
                "appearance.colors.original_lyric",
                "Original lyric color",
                "Base color of the original lyric representation.",
            ),
            (
                "appearance.colors.romanization",
                "Romanization color",
                "Color of romanized or transliterated lyrics.",
            ),
            (
                "appearance.colors.translation",
                "Translation color",
                "Color of translated lyrics.",
            ),
            (
                "appearance.colors.metadata_primary",
                "Primary metadata color",
                "Color of the track title and primary metadata.",
            ),
            (
                "appearance.colors.metadata_secondary",
                "Secondary metadata color",
                "Color of artist, album, and secondary metadata.",
            ),
            (
                "appearance.colors.background",
                "Background color",
                "Window background color; alpha combines with background opacity "
                "for transparency.",
            ),
            (
                "appearance.colors.foreground",
                "Foreground color",
                "General foreground color for product-controlled surfaces.",
            ),
            (
                "appearance.colors.accent",
                "Accent color",
                "KonoKashi accent for controls and product-controlled focus.",
            ),
            (
                "appearance.colors.progress",
                "Progress color",
                "Playback progress indicator color.",
            ),
            (
                "appearance.colors.status",
                "Status color",
                "Playback and auxiliary status text color.",
            ),
            (
                "appearance.colors.muted",
                "Muted color",
                "Muted and disabled presentation text color.",
            ),
            (
                "appearance.colors.selection",
                "Selection color",
                "Product-controlled lyric text selection color.",
            ),
        )
    ),
    *(
        _appearance_definition(
            key,
            title,
            SettingCategory.COLORS,
            description,
            minimum=0,
            maximum=100,
        )
        for key, title, description in (
            (
                "appearance.opacity.content",
                "Content opacity",
                "Opacity percentage applied to the ordinary content surface.",
            ),
            (
                "appearance.opacity.background",
                "Background opacity",
                "Window background transparency: 100 is opaque, 0 is transparent. "
                "Text stays independent.",
            ),
            (
                "appearance.opacity.inactive_line",
                "Inactive-line opacity",
                "Opacity percentage applied to context lyrics.",
            ),
            (
                "appearance.opacity.metadata",
                "Metadata opacity",
                "Opacity percentage applied to track metadata.",
            ),
            (
                "appearance.opacity.secondary_representation",
                "Secondary representation opacity",
                "Opacity percentage applied to romanization and translation.",
            ),
        )
    ),
    *(
        _appearance_definition(
            key,
            title,
            SettingCategory.LAYOUT,
            description,
            minimum=minimum,
            maximum=maximum,
        )
        for key, title, description, minimum, maximum in (
            (
                "appearance.spacing.outer_margin",
                "Outer margin",
                "Space around the main content in logical pixels.",
                0,
                120,
            ),
            (
                "appearance.spacing.lyric_padding",
                "Lyric block padding",
                "Space inside the lyric presentation region.",
                0,
                80,
            ),
            (
                "appearance.spacing.line",
                "Lyric line spacing",
                "Space between aligned lyric groups.",
                0,
                48,
            ),
            (
                "appearance.spacing.representation",
                "Representation spacing",
                "Space between original, romanized, and translated text.",
                0,
                32,
            ),
            (
                "appearance.spacing.metadata",
                "Metadata spacing",
                "Space between metadata elements.",
                0,
                48,
            ),
            (
                "appearance.spacing.progress",
                "Progress spacing",
                "Space around progress and playback status.",
                0,
                48,
            ),
            (
                "appearance.spacing.context",
                "Context spacing",
                "Space separating current lyrics from context lyrics.",
                0,
                80,
            ),
            (
                "appearance.spacing.maximum_lyric_width",
                "Maximum lyric width",
                "Readable maximum width of the lyric block in logical pixels.",
                240,
                2400,
            ),
            (
                "appearance.context.previous",
                "Previous context lines",
                "Number of lyric lines shown before the current line.",
                0,
                8,
            ),
            (
                "appearance.context.following",
                "Following context lines",
                "Number of lyric lines shown after the current line.",
                0,
                8,
            ),
        )
    ),
    *(
        _appearance_definition(
            key,
            title,
            SettingCategory.LAYOUT,
            description,
            choices=tuple(item.value for item in TextAlignment),
        )
        for key, title, description in (
            (
                "appearance.alignment.lyrics",
                "Lyric alignment",
                "Horizontal alignment of all lyric representations.",
            ),
            (
                "appearance.alignment.metadata",
                "Metadata alignment",
                "Horizontal alignment of track metadata independently of lyrics.",
            ),
        )
    ),
    *(
        _appearance_definition(key, title, SettingCategory.VISIBILITY, description)
        for key, title, description in (
            (
                "appearance.visibility.title",
                "Show title",
                "Show the current track title.",
            ),
            (
                "appearance.visibility.artist",
                "Show artist",
                "Show the current track artist.",
            ),
            (
                "appearance.visibility.album",
                "Show album",
                "Show album metadata when the frontend supplies it.",
            ),
            (
                "appearance.visibility.source",
                "Show lyrics source",
                "Show provider, confidence, and synchronization source details.",
            ),
            (
                "appearance.visibility.playback_status",
                "Show playback status",
                "Show playing, paused, or stopped status.",
            ),
            (
                "appearance.visibility.progress",
                "Show progress bar",
                "Show graphical playback progress.",
            ),
            (
                "appearance.visibility.timestamps",
                "Show timestamps",
                "Show playback position and duration text.",
            ),
            (
                "appearance.visibility.inactive_context",
                "Show inactive lyrics",
                "Show previous and following context lyrics.",
            ),
            (
                "appearance.visibility.auxiliary_status",
                "Show auxiliary status",
                "Show non-lyric empty, loading, and untimed state labels.",
            ),
            (
                "appearance.visibility.chrome",
                "Show application chrome",
                "Show ordinary Settings, Review, Details, and library actions.",
            ),
        )
    ),
    *(
        _appearance_definition(
            key,
            title,
            SettingCategory.MOTION,
            description,
            minimum=minimum,
            maximum=maximum,
        )
        for key, title, description, minimum, maximum in (
            (
                "appearance.motion.transition_ms",
                "Animation speed",
                "Choose how quickly lyrics settle into focus. Custom accepts an "
                "exact duration.",
                0,
                1000,
            ),
            (
                "appearance.motion.emphasis_transition_ms",
                "Emphasis transition duration",
                "Bounded active-line emphasis transition duration in milliseconds.",
                0,
                1000,
            ),
        )
    ),
    _appearance_definition(
        "appearance.motion.smooth_scrolling",
        "Lyric transitions",
        SettingCategory.MOTION,
        "Choose Instant or Smooth movement when the active lyric changes.",
    ),
    _appearance_definition(
        "appearance.motion.reduced",
        "Reduce motion",
        SettingCategory.MOTION,
        "Disable nonessential movement regardless of other motion preferences.",
    ),
)

SETTINGS_SCHEMA += (
    _appearance_definition(
        "appearance.typography.lyric_scale_percent",
        "Lyrics scale",
        SettingCategory.APPEARANCE,
        "Scale all lyric layers together. 100% is normal; "
        "menus and metadata stay unchanged.",
        minimum=50,
        maximum=200,
    ),
    _appearance_definition(
        "appearance.progress.thickness",
        "Progress thickness",
        SettingCategory.PROGRESS,
        "Height of the playback progress bar in logical pixels.",
        minimum=1,
        maximum=24,
    ),
    _appearance_definition(
        "appearance.progress.track_color",
        "Progress track color",
        SettingCategory.PROGRESS,
        "Unfilled progress track color, including optional alpha.",
        string_format=SettingStringFormat.COLOR,
    ),
    _appearance_definition(
        "appearance.progress.opacity",
        "Progress opacity",
        SettingCategory.PROGRESS,
        "Opacity of both the progress fill and track; timestamps stay independent.",
        minimum=0,
        maximum=100,
    ),
    _appearance_definition(
        "appearance.progress.corner_radius",
        "Progress corner radius",
        SettingCategory.PROGRESS,
        "Use zero for square ends; rounding is limited to half the bar thickness.",
        minimum=0,
        maximum=12,
    ),
)

SETTINGS_SCHEMA = tuple(
    definition
    for category in SettingCategory
    for definition in SETTINGS_SCHEMA
    if definition.category is category
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
    def appearance(self) -> AppearanceProfile:
        explicit = frozenset(
            item.definition.key
            for item in self.values
            if item.origin is not SettingOrigin.DEFAULT
        )
        return resolve_appearance(self.plain_values(), explicit_keys=explicit)

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
        _ = snapshot.appearance
        display = snapshot.representation_display
        if not (
            display.show_original or display.show_romanized or display.show_translated
        ):
            raise ValueError(
                "At least one lyric representation must remain visible so the "
                "application is recoverable."
            )
    except ValueError as error:
        raise SettingsValidationError(
            (SettingsDiagnostic(str(error), path=path),)
        ) from error
    return snapshot


def _canonical_value(definition: SettingDefinition, value: object) -> SettingValue:
    if definition.value_type is SettingType.STRING_LIST:
        return tuple(cast(list[str] | tuple[str, ...], value))
    if (
        definition.value_type is SettingType.STRING
        and definition.string_format is SettingStringFormat.COLOR
    ):
        return normalize_color(cast(str, value))
    return cast(bool | int | str, value)


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
    if expected is SettingType.STRING and type(value) is not str:
        return (
            f"Expected string, got {type(value).__name__} {_render_bad_value(value)}."
        )
    if expected is SettingType.STRING_LIST and (
        not isinstance(value, (list, tuple))
        or any(type(item) is not str for item in value)
    ):
        return f"Expected an array of strings, got {_render_bad_value(value)}."
    if expected is SettingType.STRING_LIST and isinstance(value, (list, tuple)):
        strings = cast(list[str] | tuple[str, ...], value)
        if any(not item.strip() for item in strings):
            return "Collection entries must not be blank."
        if any("\x00" in item or len(item) > 256 for item in strings):
            return (
                "Collection entries must be at most 256 characters and contain no NUL."
            )
        keys = tuple(item.strip().casefold() for item in strings)
        if len(set(keys)) != len(keys):
            return "Collection entries must not contain duplicates."
    if type(value) is int:
        if definition.minimum is not None and value < definition.minimum:
            return f"Expected an integer of at least {definition.minimum}, got {value}."
        if definition.maximum is not None and value > definition.maximum:
            return f"Expected an integer of at most {definition.maximum}, got {value}."
    if type(value) is str:
        if "\x00" in value:
            return "Strings cannot contain NUL characters."
        if len(value) > 256:
            return "Expected a string no longer than 256 characters."
        if definition.choices and value not in definition.choices:
            choices = ", ".join(repr(item) for item in definition.choices)
            return f"Expected one of {choices}, got {value!r}."
        if definition.string_format is SettingStringFormat.COLOR:
            try:
                normalize_color(value)
            except ValueError as error:
                return str(error)
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
