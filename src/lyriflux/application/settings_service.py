"""Transactional canonical settings service shared by every frontend."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock
from typing import Protocol

from lyriflux.application.settings import (
    SETTINGS_BY_KEY,
    DesktopInteractionSettings,
    SettingsDiagnostic,
    SettingsSnapshot,
    SettingsValidationError,
    SettingValue,
    default_settings_snapshot,
    unknown_setting_diagnostic,
    validate_settings_values,
)
from lyriflux.domain.library import LibrarySettings
from lyriflux.domain.representations import RepresentationDisplaySettings
from lyriflux.domain.tracks import PlayerSelectionConfig


class SettingsFileError(RuntimeError):
    """A bounded, path-aware configuration file failure."""

    def __init__(self, diagnostic: SettingsDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(diagnostic.render())


class SettingsConfigPort(Protocol):
    """Safe declarative document boundary; no frontend parses files itself."""

    @property
    def path(self) -> Path: ...

    def read(self) -> Mapping[str, object]: ...

    def transaction(self) -> AbstractContextManager[None]: ...

    def update_many(self, values: Mapping[str, SettingValue]) -> bool: ...

    def reset(self, key: str) -> bool: ...

    def render(self, values: Mapping[str, SettingValue]) -> str: ...


@dataclass(frozen=True, slots=True)
class SettingsChange:
    """One atomic semantic transition delivered to subscribers."""

    previous: SettingsSnapshot
    current: SettingsSnapshot
    changed_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SettingsReloadResult:
    """Outcome of one reload attempt with last-known-good evidence."""

    applied: bool
    snapshot: SettingsSnapshot
    changed_keys: tuple[str, ...] = ()
    diagnostics: tuple[SettingsDiagnostic, ...] = ()


class SettingsSubscription:
    """Idempotent handle preventing callback accumulation."""

    def __init__(self, close: Callable[[], None]) -> None:
        self._close = close
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._close()


class CanonicalSettingsService:
    """Resolve defaults < config file and retain atomic last-known-good state."""

    def __init__(
        self,
        config: SettingsConfigPort,
        *,
        fallback: SettingsSnapshot | None = None,
    ) -> None:
        self._config = config
        self._lock = RLock()
        self._base = fallback or default_settings_snapshot()
        self._current = self._base
        self._last_diagnostics: tuple[SettingsDiagnostic, ...] = ()
        self._subscriptions: dict[int, Callable[[SettingsChange], None]] = {}
        self._next_subscription = 1

    @property
    def path(self) -> Path:
        return self._config.path

    @property
    def current(self) -> SettingsSnapshot:
        with self._lock:
            return self._current

    @property
    def diagnostics(self) -> tuple[SettingsDiagnostic, ...]:
        with self._lock:
            return self._last_diagnostics

    def initialize(self) -> SettingsReloadResult:
        return self.reload()

    def validate(self) -> SettingsSnapshot:
        """Validate current disk content without changing runtime state."""

        raw = self._read_flattened()
        return self._validate_flattened(raw)

    def reload(self) -> SettingsReloadResult:
        """Atomically accept a full valid candidate or retain last-known-good."""

        try:
            raw = self._read_flattened()
            candidate = self._validate_flattened(raw)
        except SettingsFileError as error:
            diagnostics = (error.diagnostic,)
            with self._lock:
                self._last_diagnostics = diagnostics
                return SettingsReloadResult(
                    False, self._current, diagnostics=diagnostics
                )
        except SettingsValidationError as error:
            with self._lock:
                self._last_diagnostics = error.diagnostics
                return SettingsReloadResult(
                    False, self._current, diagnostics=error.diagnostics
                )
        callbacks: tuple[Callable[[SettingsChange], None], ...] = ()
        change: SettingsChange | None = None
        with self._lock:
            previous = self._current
            changed = tuple(
                definition.key
                for definition in SETTINGS_BY_KEY.values()
                if previous.get(definition.key) != candidate.get(definition.key)
                or previous.resolved(definition.key).origin
                != candidate.resolved(definition.key).origin
            )
            self._current = candidate
            self._last_diagnostics = ()
            if changed:
                change = SettingsChange(previous, candidate, changed)
                callbacks = tuple(self._subscriptions.values())
        if change is not None:
            for callback in callbacks:
                callback(change)
        return SettingsReloadResult(True, candidate, changed)

    def get(self, key: str) -> SettingValue:
        return self.current.get(key)

    def set(self, key: str, value: SettingValue) -> SettingsReloadResult:
        """Validate the full candidate before one comment-preserving write."""

        if key not in SETTINGS_BY_KEY:
            raise SettingsValidationError(
                (unknown_setting_diagnostic(key, path=self.path),)
            )
        with self._config.transaction():
            raw = self._read_flattened()
            raw[key] = value
            self._validate_flattened(raw)
            self._config.update_many({key: value})
            result = self.reload()
        if not result.applied:
            raise SettingsValidationError(result.diagnostics)
        return result

    def set_many(self, values: Mapping[str, SettingValue]) -> SettingsReloadResult:
        with self._config.transaction():
            raw = self._read_flattened()
            raw.update(values)
            self._validate_flattened(raw)
            self._config.update_many(values)
            result = self.reload()
        if not result.applied:
            raise SettingsValidationError(result.diagnostics)
        return result

    def reset(self, key: str) -> SettingsReloadResult:
        if key not in SETTINGS_BY_KEY:
            raise SettingsValidationError(
                (unknown_setting_diagnostic(key, path=self.path),)
            )
        with self._config.transaction():
            self._read_flattened()
            self._config.reset(key)
            result = self.reload()
        if not result.applied:
            raise SettingsValidationError(result.diagnostics)
        return result

    def subscribe(
        self, callback: Callable[[SettingsChange], None]
    ) -> SettingsSubscription:
        with self._lock:
            subscription_id = self._next_subscription
            self._next_subscription += 1
            self._subscriptions[subscription_id] = callback

        def remove() -> None:
            with self._lock:
                self._subscriptions.pop(subscription_id, None)

        return SettingsSubscription(remove)

    def dump_defaults(self) -> str:
        return self._config.render(default_settings_snapshot().plain_values())

    def get_player_selection(self) -> PlayerSelectionConfig:
        return self.current.player_selection

    def put_player_selection(self, config: PlayerSelectionConfig) -> None:
        self.set_many(
            {
                "players.preferred": config.preferred_players,
                "players.ignored": config.ignored_players,
            }
        )

    def get_representation_display(self) -> RepresentationDisplaySettings:
        return self.current.representation_display

    def put_representation_display(
        self, settings: RepresentationDisplaySettings
    ) -> None:
        self.set_many(
            {
                "lyrics.display.original": settings.show_original,
                "lyrics.display.romanized": settings.show_romanized,
                "lyrics.display.translated": settings.show_translated,
            }
        )

    def get_desktop_interaction(self) -> DesktopInteractionSettings:
        return self.current.desktop_interaction

    def put_desktop_interaction(self, settings: DesktopInteractionSettings) -> None:
        self.set("desktop.lyrics.selectable", settings.allow_lyric_selection)

    def get_library(self) -> LibrarySettings:
        return self.current.library

    def put_library(self, settings: LibrarySettings) -> None:
        self.set_many(
            {
                "library.roots": settings.roots,
                "library.automatic_downloads": settings.automatic_downloads,
                "library.metadata_workers": settings.worker_count,
            }
        )

    def _read_flattened(self) -> dict[str, object]:
        nested = self._config.read()
        version = nested.get("schema_version", 1)
        if type(version) is not int or version != 1:
            raise SettingsValidationError(
                (
                    SettingsDiagnostic(
                        "Expected supported integer schema_version = 1.",
                        key="schema_version",
                        path=self.path,
                    ),
                )
            )
        raw: dict[str, object] = {}
        self._flatten(nested, (), raw)
        raw.pop("schema_version", None)
        return raw

    def _validate_flattened(self, raw: Mapping[str, object]) -> SettingsSnapshot:
        explicit_keys = frozenset(raw)
        combined: dict[str, object] = {}
        combined.update(self._base.plain_values())
        combined.update(raw)
        candidate = validate_settings_values(
            combined,
            explicit_keys=explicit_keys,
            path=self.path,
        )
        return SettingsSnapshot(
            candidate.schema_version,
            tuple(
                item
                if item.definition.key in explicit_keys
                else replace(
                    item,
                    origin=self._base.resolved(item.definition.key).origin,
                )
                for item in candidate.values
            ),
        )

    def _flatten(
        self,
        value: Mapping[str, object],
        prefix: tuple[str, ...],
        target: dict[str, object],
    ) -> None:
        for name, item in value.items():
            path = (*prefix, name)
            key = ".".join(path)
            if not prefix and name == "schema_version":
                target[key] = item
            elif isinstance(item, Mapping):
                if not item and not any(
                    candidate.startswith(f"{key}.") for candidate in SETTINGS_BY_KEY
                ):
                    target[key] = item
                else:
                    self._flatten(item, path, target)
            else:
                target[key] = item
