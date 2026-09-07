"""Schema-driven Textual application for canonical KonoKashi settings."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from threading import Event
from typing import ClassVar, cast

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import (
    Button,
    Footer,
    Input,
    OptionList,
    Static,
)
from textual.widgets.option_list import Option

from konokashi.application.settings import (
    SETTINGS_SCHEMA,
    SettingCategory,
    SettingDefinition,
    SettingsDiagnostic,
    SettingsValidationError,
    SettingType,
    SettingValue,
    default_settings_snapshot,
    validate_settings_values,
)
from konokashi.application.settings_service import (
    CanonicalSettingsService,
    SettingsChange,
    SettingsFileError,
    SettingsSubscription,
)
from konokashi.presentation.tui.settings_bindings import NAVIGATION_BINDINGS
from konokashi.presentation.tui.settings_controls import SettingSwitch
from konokashi.presentation.tui.settings_messages import (
    DiskObserved,
    MutationFinished,
    ServiceReady,
    SnapshotObserved,
)
from konokashi.presentation.tui.settings_runtime import (
    ConfigSignature,
    config_signature,
    open_settings_service,
)
from konokashi.presentation.tui.settings_screens import (
    OrderedListEditorScreen,
    SettingsHelpScreen,
)
from konokashi.presentation.tui.settings_view import (
    APP_CSS,
    SettingsWorkspace,
    format_value,
    origin_text,
)

SettingsServiceFactory = Callable[[], CanonicalSettingsService]
DEFAULT_DISK_OBSERVE_INTERVAL = 0.15


class SettingsApp(App[int]):
    """Full-screen terminal settings frontend over one canonical service."""

    TITLE = "KonoKashi Settings"
    ENABLE_COMMAND_PALETTE = False
    HORIZONTAL_BREAKPOINTS = [  # noqa: RUF012
        (0, "-narrow"),
        (75, "-normal"),
        (115, "-wide"),
    ]
    BINDINGS: ClassVar[list[BindingType]] = list(NAVIGATION_BINDINGS)
    CSS = APP_CSS

    def __init__(
        self,
        *,
        service: CanonicalSettingsService | None = None,
        service_factory: SettingsServiceFactory | None = None,
        watch_interval: float = DEFAULT_DISK_OBSERVE_INTERVAL,
    ) -> None:
        super().__init__()
        self.register_theme(
            Theme(name="konokashi", primary="#39b9c7", accent="#39b9c7")
        )
        self.theme = "konokashi"
        self._service = service
        self._service_factory = service_factory
        self._watch_interval = watch_interval
        self._subscription: SettingsSubscription | None = None
        self._snapshot = service.current if service else default_settings_snapshot()
        self._category = SettingCategory.PLAYERS
        self._visible_definitions: tuple[SettingDefinition, ...] = ()
        self._selected_key = SETTINGS_SCHEMA[0].key
        self._signature: ConfigSignature | None = None
        self._watch_busy = False
        self._watch_started = False
        self._watch_stop = Event()
        self._mutation_busy = False
        self._startup_failed = False
        self._closed = False
        self._ui_ready = False
        self._file_diagnostic: str | None = None

    def compose(self) -> ComposeResult:
        yield SettingsWorkspace(id="shell")
        yield Static(
            "Resize to at least 50 x 20 cells to use Settings.",
            id="too-small",
            markup=False,
        )
        yield Footer()

    def on_mount(self) -> None:
        self._ui_ready = True
        self.install_screen(SettingsHelpScreen(), "help")
        categories = self.query_one("#categories", OptionList)
        settings = self.query_one("#settings-list", OptionList)
        search = self.query_one("#search", Input)
        self.watch(
            categories,
            "highlighted",
            self._category_index_changed,
            init=False,
        )
        self.watch(
            settings,
            "highlighted",
            self._setting_index_changed,
            init=False,
        )
        self.watch(search, "value", self._search_value_changed, init=False)
        self.watch(self.screen, "focused", self._focus_changed, init=False)
        categories.border_title = "Categories · 1-4"
        settings.border_title = "Settings"
        categories.highlighted = 0
        self._refresh_setting_list()
        settings.focus()
        if self._service is not None:
            self._accept_service(self._service)
        else:
            factory = self._service_factory
            if factory is None:
                self.post_message(ServiceReady(None, "No settings service factory."))
            else:
                self.run_worker(
                    partial(self._open_service, factory),
                    name="open canonical settings",
                    group="settings-open",
                    thread=True,
                    exclusive=True,
                    exit_on_error=False,
                )

    def on_unmount(self) -> None:
        self._closed = True
        self._ui_ready = False
        self._watch_stop.set()
        if self._subscription is not None:
            self._subscription.close()
            self._subscription = None

    def on_resize(self, event: events.Resize) -> None:
        self.screen.set_class(
            event.size.width < 50 or event.size.height < 20,
            "too-small",
        )

    def _search_value_changed(self, _value: str) -> None:
        self._refresh_setting_list()

    def _category_index_changed(self, index: int | None) -> None:
        if index is None:
            return
        categories = tuple(SettingCategory)
        if not 0 <= index < len(categories) or categories[index] is self._category:
            return
        self._category = categories[index]
        self._clear_search_or_refresh()

    @on(OptionList.OptionSelected, "#categories")
    def _category_selected(self, event: OptionList.OptionSelected) -> None:
        self._select_category(event.option.id, focus_settings=True)

    def _setting_index_changed(self, index: int | None) -> None:
        if index is None or not 0 <= index < len(self._visible_definitions):
            return
        key = self._visible_definitions[index].key
        if key == self._selected_key:
            return
        self._selected_key = key
        self._render_detail()

    @on(OptionList.OptionSelected, "#settings-list")
    def _setting_selected(self, _event: OptionList.OptionSelected) -> None:
        self._focus_editor()

    def on_setting_switch_edited(self, event: SettingSwitch.Edited) -> None:
        if event.value != self._snapshot.get(event.key):
            self._start_mutation(event.key, event.value)

    @on(Input.Submitted, "#search")
    def _search_submitted(self) -> None:
        if self._selected_key:
            self.query_one("#settings-list", OptionList).focus()

    @on(Input.Submitted, "#integer-value")
    def _integer_submitted(self, _event: Input.Submitted) -> None:
        self._apply_integer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "integer-minus":
            self._adjust_integer(-1)
        elif button_id == "integer-plus":
            self._adjust_integer(1)
        elif button_id == "integer-apply":
            self._apply_integer()
        elif button_id == "edit-list":
            self._open_list_editor()
        elif button_id == "reset-setting":
            self.action_reset_selected()
        elif button_id == "show-help":
            self.action_show_help()
        elif button_id == "clear-search":
            self.query_one("#search", Input).value = ""
            self.query_one("#search", Input).focus()

    def on_service_ready(self, message: ServiceReady) -> None:
        if message.service is None:
            self._startup_failed = True
            detail = message.error or "unknown error"
            self._set_status(
                f"Unable to open canonical settings: {detail}",
                error=True,
            )
            return
        self._accept_service(message.service)

    def on_snapshot_observed(self, message: SnapshotObserved) -> None:
        if message.change.current == self._snapshot:
            return
        self._snapshot = message.change.current
        self._refresh_changed_values(message.change.changed_keys)
        if not self._mutation_busy:
            changed = ", ".join(message.change.changed_keys)
            self._set_status(f"Canonical settings updated: {changed}", success=True)

    def on_mutation_finished(self, message: MutationFinished) -> None:
        self._mutation_busy = False
        self._set_editing_enabled(True)
        if message.error is not None or message.result is None:
            self._set_status(
                f"Could not update {message.key}: {message.error or 'unknown error'}",
                error=True,
            )
            self._render_detail()
            return
        if message.signature is not None:
            self._signature = message.signature
        self._file_diagnostic = None
        if message.result.snapshot != self._snapshot:
            changed_keys = message.result.changed_keys
            self._snapshot = message.result.snapshot
            self._refresh_changed_values(changed_keys)
        self._set_status(
            f"Saved {self._snapshot.resolved(message.key).definition.title}.",
            success=True,
        )

    def on_disk_observed(self, message: DiskObserved) -> None:
        self._watch_busy = False
        self._signature = message.signature
        if message.result is None:
            return
        if message.result.applied:
            self._file_diagnostic = None
            if message.result.changed_keys:
                self._set_status("External configuration change loaded.", success=True)
            else:
                self._set_status("External configuration is valid.", success=True)
        else:
            self._show_diagnostics(message.result.diagnostics, "External file rejected")

    def action_quit_settings(self) -> None:
        self.exit(1 if self._startup_failed else 0)

    def action_show_help(self) -> None:
        self.push_screen("help")

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_escape(self) -> None:
        search = self.query_one("#search", Input)
        if search.value:
            search.value = ""
            search.focus()
        else:
            self._render_detail()
            self.query_one("#settings-list", OptionList).focus()

    def action_reset_selected(self) -> None:
        if self._service is not None and self._selected_key:
            self._start_mutation(self._selected_key, None, reset=True)

    def action_next_setting(self) -> None:
        options = self._navigation_target()
        options.focus()
        options.action_cursor_down()

    def action_previous_setting(self) -> None:
        options = self._navigation_target()
        options.focus()
        options.action_cursor_up()

    def action_category(self, index: int) -> None:
        categories = tuple(SettingCategory)
        if not 0 <= index < len(categories):
            return
        self.query_one("#categories", OptionList).highlighted = index
        if self.query_one("#search", Input).value:
            self.query_one("#search", Input).value = ""
        self.query_one("#settings-list", OptionList).focus()

    def _navigation_target(self) -> OptionList:
        if self.focused is self.query_one("#categories", OptionList):
            return self.query_one("#categories", OptionList)
        return self.query_one("#settings-list", OptionList)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        return len(self.screen_stack) <= 1 or action == "quit_settings"

    def _focus_changed(self, focused: Widget | None) -> None:
        if not self._ui_ready:
            return
        identifier = focused.id if focused else None
        if identifier == "search":
            hint = "Enter results · Esc clear / return · Search all categories"
        elif identifier == "categories":
            hint = "↑↓ / j k categories · Enter settings · Tab next panel"
        elif identifier == "integer-value":
            hint = "Enter saves · Esc discards draft · Tab next control"
        elif identifier == "boolean-value":
            hint = "Enter / Space toggles · Esc settings · r reset"
        else:
            hint = "↑↓ / j k settings · Enter edit · 1-4 category · r reset"
        self.query_one("#context-hint", Static).update(hint, layout=False)

    def _clear_search_or_refresh(self) -> None:
        search = self.query_one("#search", Input)
        if search.value:
            search.value = ""
        else:
            self._refresh_setting_list()

    def _open_service(self, factory: SettingsServiceFactory) -> None:
        try:
            service = factory()
        except (OSError, RuntimeError, ValueError) as error:
            self.post_message(
                ServiceReady(None, f"{error.__class__.__name__}: {error}")
            )
        else:
            self.post_message(ServiceReady(service))

    def _accept_service(self, service: CanonicalSettingsService) -> None:
        if self._subscription is not None:
            self._subscription.close()
        self._service = service
        self._snapshot = service.current
        self._subscription = service.subscribe(self._service_changed)
        self._refresh_setting_list()
        if service.diagnostics:
            self._show_diagnostics(service.diagnostics, "Configuration rejected")
        else:
            self._set_status(f"Config: {service.path}")
        if self._watch_interval > 0:
            self._signature = config_signature(service.path)
            self._start_disk_watcher()

    def _service_changed(self, change: SettingsChange) -> None:
        if not self._closed:
            self.post_message(SnapshotObserved(change))

    def _schedule_disk_check(self) -> None:
        if self._watch_busy or self._service is None or self._closed:
            return
        self._watch_busy = True
        self.run_worker(
            self._observe_disk,
            name="observe canonical settings file",
            group="settings-watch",
            thread=True,
            exit_on_error=False,
        )

    def _start_disk_watcher(self) -> None:
        if self._watch_started or self._service is None or self._closed:
            return
        self._watch_started = True
        self._watch_stop.clear()
        self.run_worker(
            self._watch_disk,
            name="watch canonical settings file",
            group="settings-watch-loop",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _watch_disk(self) -> None:
        while not self._watch_stop.wait(self._watch_interval):
            service = self._service
            if service is None or self._closed:
                return
            if self._mutation_busy:
                continue
            signature = config_signature(service.path)
            if signature == self._signature:
                continue
            self._signature = signature
            result = service.reload()
            self.post_message(DiskObserved(signature, result))

    def _observe_disk(self) -> None:
        service = self._service
        if service is None:
            return
        signature = config_signature(service.path)
        result = (
            None
            if self._signature is None or signature == self._signature
            else service.reload()
        )
        self.post_message(DiskObserved(signature, result))

    def _start_mutation(
        self, key: str, value: SettingValue | None, *, reset: bool = False
    ) -> None:
        if self._service is None or self._mutation_busy:
            return
        self._mutation_busy = True
        self._set_editing_enabled(False)
        self._set_status(f"Saving {self._snapshot.resolved(key).definition.title}…")
        self.run_worker(
            partial(self._mutate, key, value, reset),
            name=f"update {key}",
            group="settings-mutation",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _mutate(self, key: str, value: SettingValue | None, reset: bool) -> None:
        service = self._service
        if service is None:
            self.post_message(MutationFinished(key, None, "service unavailable"))
            return
        try:
            if reset:
                result = service.reset(key)
            elif value is None:
                raise ValueError("a setting value is required")
            else:
                result = service.set(key, value)
        except (
            SettingsFileError,
            SettingsValidationError,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            self.post_message(MutationFinished(key, None, str(error)))
        else:
            signature = config_signature(service.path)
            self._signature = signature
            self.post_message(MutationFinished(key, result, signature=signature))

    def _select_category(
        self, option_id: str | None, *, focus_settings: bool = False
    ) -> None:
        if option_id is None:
            return
        for category in SettingCategory:
            if category.name.lower() == option_id:
                if category is self._category:
                    if self.query_one("#search", Input).value:
                        self.query_one("#search", Input).value = ""
                    if focus_settings:
                        self.query_one("#settings-list", OptionList).focus()
                    return
                self._category = category
                self._clear_search_or_refresh()
                if focus_settings:
                    self.query_one("#settings-list", OptionList).focus()
                return

    def _refresh_setting_list(self) -> None:
        if not self._ui_ready:
            return
        query = self.query_one("#search", Input).value.casefold().strip()
        definitions = tuple(
            definition
            for definition in SETTINGS_SCHEMA
            if (
                (not query and definition.category is self._category)
                or (
                    query
                    and query
                    in " ".join(
                        (definition.title, definition.description, definition.key)
                    ).casefold()
                )
            )
        )
        self._visible_definitions = definitions
        options = self.query_one("#settings-list", OptionList)
        options.border_title = (
            f"Search · {len(definitions)} results" if query else self._category.value
        )
        self.query_one("#clear-search", Button).disabled = not bool(query)
        options.clear_options()
        options.add_options(
            Option(
                self._setting_prompt(definition),
                id=f"setting-{index}",
            )
            for index, definition in enumerate(definitions)
        )
        if not definitions:
            self._selected_key = ""
            self._render_empty_detail(
                "No settings match. Try a shorter word or clear the search."
            )
            return
        selected_index = next(
            (
                index
                for index, definition in enumerate(definitions)
                if definition.key == self._selected_key
            ),
            0,
        )
        self._selected_key = definitions[selected_index].key
        options.highlighted = selected_index
        self._render_detail()

    def _refresh_changed_values(self, changed_keys: tuple[str, ...]) -> None:
        if not self._ui_ready or not changed_keys:
            return
        changed = frozenset(changed_keys)
        options = self.query_one("#settings-list", OptionList)
        for index, definition in enumerate(self._visible_definitions):
            if definition.key in changed:
                options.replace_option_prompt_at_index(
                    index,
                    self._setting_prompt(definition),
                )
        if self._selected_key in changed:
            self._render_detail()

    def _setting_prompt(self, definition: SettingDefinition) -> str:
        resolved = self._snapshot.resolved(definition.key)
        value = resolved.value
        summary = (
            f"{len(value)} items"
            if isinstance(value, tuple) and value
            else format_value(value)
        )
        return f"{definition.title}\n  {summary} · {origin_text(resolved.origin)}"

    def _render_detail(self) -> None:
        if not self._ui_ready or not self._selected_key:
            return
        self.query_one(SettingsWorkspace).show_setting(
            self._snapshot.resolved(self._selected_key)
        )

    def _render_empty_detail(self, text: str) -> None:
        self.query_one(SettingsWorkspace).show_empty(text)

    def _focus_editor(self) -> None:
        if not self._selected_key:
            return
        value_type = self._snapshot.resolved(self._selected_key).definition.value_type
        selector = {
            SettingType.BOOLEAN: "#boolean-value",
            SettingType.INTEGER: "#integer-value",
            SettingType.STRING_LIST: "#edit-list",
        }[value_type]
        self.query_one(selector).focus()

    def _apply_integer(self) -> None:
        if not self._selected_key:
            return
        definition = self._snapshot.resolved(self._selected_key).definition
        raw = self.query_one("#integer-value", Input).value.strip()
        try:
            value = int(raw)
        except ValueError:
            self._set_status("Enter a whole integer before applying.", error=True)
            return
        if definition.minimum is not None and value < definition.minimum:
            self._set_status(
                f"Minimum for {definition.key} is {definition.minimum}.", error=True
            )
            return
        if definition.maximum is not None and value > definition.maximum:
            self._set_status(
                f"Maximum for {definition.key} is {definition.maximum}.", error=True
            )
            return
        self._start_mutation(definition.key, value)

    def _adjust_integer(self, offset: int) -> None:
        editor = self.query_one("#integer-value", Input)
        try:
            value = int(editor.value)
        except ValueError:
            value = cast(int, self._snapshot.get(self._selected_key))
        definition = self._snapshot.resolved(self._selected_key).definition
        minimum = (
            definition.minimum if definition.minimum is not None else value + offset
        )
        maximum = (
            definition.maximum if definition.maximum is not None else value + offset
        )
        editor.value = str(min(maximum, max(minimum, value + offset)))

    def _open_list_editor(self) -> None:
        if not self._selected_key:
            return
        resolved = self._snapshot.resolved(self._selected_key)
        baseline = resolved.value
        if not isinstance(baseline, tuple):
            return
        screen = OrderedListEditorScreen(
            resolved.definition.title,
            baseline,
            path_values=resolved.definition.key == "library.roots",
            preferred=resolved.definition.key == "players.preferred",
            validate=partial(self._validate_list_draft, resolved.definition.key),
        )
        self.push_screen(
            screen,
            partial(self._list_editor_closed, resolved.definition.key, baseline),
        )

    def _validate_list_draft(self, key: str, values: tuple[str, ...]) -> str | None:
        candidate = {
            definition.key: self._snapshot.get(definition.key)
            for definition in SETTINGS_SCHEMA
        }
        candidate[key] = values
        try:
            validate_settings_values(candidate)
        except SettingsValidationError as error:
            self._set_status(str(error), error=True)
            return str(error)
        return None

    def _list_editor_closed(
        self,
        key: str,
        baseline: tuple[str, ...],
        result: tuple[str, ...] | None,
    ) -> None:
        if result is None or self._service is None:
            self._set_status("List edit cancelled.")
            return
        if self._service.current.get(key) != baseline:
            self._set_status(
                f"{key} changed externally while the draft was open; review and retry.",
                error=True,
            )
            self._snapshot = self._service.current
            self._refresh_setting_list()
            return
        # The dismissed dialog restores its launch button. Move to results
        # before saving disables editors, so Textual does not jump to Search.
        self.query_one("#settings-list", OptionList).focus()
        self._start_mutation(key, result)

    def _set_editing_enabled(self, enabled: bool) -> None:
        self.query_one(SettingsWorkspace).set_editing_enabled(enabled)

    def _show_diagnostics(
        self, diagnostics: tuple[SettingsDiagnostic, ...], context: str
    ) -> None:
        rendered = "; ".join(item.render() for item in diagnostics)
        self._file_diagnostic = (
            f"{context}. Last-known-good values remain active.\n{rendered}"
        )
        self._set_status("Repair the file to reload settings.", error=True)

    def _set_status(
        self, text: str, *, error: bool = False, success: bool = False
    ) -> None:
        if self._file_diagnostic:
            text = f"{self._file_diagnostic}\n{text}"
            error = True
        self.query_one(SettingsWorkspace).set_status(
            text,
            error=error,
            success=success,
        )


def run_settings_tui(
    *,
    database_path: Path | None = None,
    config_path: Path | None = None,
) -> int:
    """Run the interactive settings app with controlled top-level failures."""

    try:
        app = SettingsApp(
            service_factory=partial(open_settings_service, database_path, config_path)
        )
        loop = asyncio.new_event_loop()
        executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="konokashi-settings",
        )
        loop.set_default_executor(executor)
        try:
            result = app.run(loop=loop)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            loop.close()
        return 0 if result is None else result
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        import sys

        print(
            f"Unable to start KonoKashi settings: {error.__class__.__name__}: {error}",
            file=sys.stderr,
        )
        return 1
