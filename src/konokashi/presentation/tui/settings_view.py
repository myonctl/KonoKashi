"""Composed widgets and snapshot rendering for the settings TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from konokashi.application.settings import (
    ReloadBehavior,
    ResolvedSetting,
    SettingCategory,
    SettingOrigin,
    SettingType,
    SettingValue,
)
from konokashi.presentation.tui.settings_controls import (
    FixedSurfaceInput,
    FixedSurfaceOptionList,
    SettingSwitch,
)

APP_CSS = """
Screen { background: $surface; align-horizontal: center; }
#shell { width: 100%; max-width: 132; height: 1fr; padding: 0 1; }
#brand { height: 1; padding: 0 1; text-style: bold; color: $primary; }
#search-row { height: 3; margin: 0 0 1 0; }
#search { width: 1fr; height: 3; }
#clear-search { width: 9; min-width: 9; margin-left: 1; }
#workspace { height: 1fr; layout: horizontal; }
#navigation { width: 34; min-width: 26; height: 1fr; margin-right: 1; }
#categories { height: 6; border: round $border; margin-bottom: 1; }
#settings-list { height: 1fr; border: round $border; }
OptionList:focus { border: heavy $primary; }
OptionList > .option-list--option-highlighted { text-style: bold; }
#detail { width: 1fr; height: 1fr; border: round $border; padding: 0 1; }
#detail:focus-within { border: heavy $primary; }
#detail-title { width: 1fr; height: auto; min-height: 1; text-style: bold;
    margin-bottom: 1; }
#detail-description { width: 1fr; margin-bottom: 1; }
#detail-value { width: 1fr; margin: 1 0; }
#detail-metadata { width: 1fr; color: $text-muted; margin-bottom: 1; }
#editor-hint { height: auto; min-height: 1; color: $text-muted; }
#editor-slot { width: 1fr; height: 3;
    layers: boolean integer string list; }
#boolean-row { layer: boolean; }
#integer-row { layer: integer; }
#string-row { layer: string; }
#edit-list { layer: list; }
#boolean-row, #integer-row, #string-row { width: 1fr; height: 3; }
#detail-actions { height: 3; margin-top: 1; }
#integer-value { width: 8; margin: 0 1; }
#integer-minus, #integer-plus { width: 5; min-width: 5; }
#integer-apply { margin-left: 1; }
#integer-apply { min-width: 9; width: 9; }
#string-value { width: 1fr; }
#string-apply { min-width: 9; width: 9; margin-left: 1; }
#boolean-label { padding: 1 1; }
#edit-list { width: auto; min-width: 20; }
#reset-setting { min-width: 18; margin-right: 1; }
#show-help { min-width: 8; width: 8; }
#context-hint { height: 1; padding: 0 1; color: $text-muted; }
#status { height: auto; min-height: 1; max-height: 4; padding: 0 1;
    color: $text-muted; }
#status.error { color: $error; }
#status.success { color: $success; }
.hidden { visibility: hidden; }
#too-small { display: none; layer: overlay; width: 100%; height: 100%;
    content-align: center middle; background: $surface; color: $warning; }
Screen.too-small #too-small { display: block; }
#modal-too-small { display: none; }
Screen.too-small #modal-too-small { display: block; width: 100%; height: 100%;
    content-align: center middle; }
Screen.too-small #list-dialog, Screen.too-small #help-dialog { display: none; }
Screen.too-small #shell, Screen.too-small Footer {
    visibility: hidden;
}
Screen.-narrow #workspace { layout: vertical; }
Screen.-narrow #navigation { width: 1fr; height: 8; min-height: 8;
    margin-right: 0; layout: horizontal; }
Screen.-narrow #categories { width: 15; height: 7; margin-right: 1; }
Screen.-narrow #settings-list { width: 1fr; height: 7; }
Screen.-narrow #detail { width: 1fr; height: 1fr; min-height: 18; }
OrderedListEditorScreen, SettingsHelpScreen { align: center middle; }
OrderedListEditorScreen, SettingsHelpScreen { background: $surface; }
#list-dialog, #help-dialog {
    width: 78; max-width: 100%; height: auto; max-height: 100%;
    border: round $primary; background: $surface; padding: 0 1;
}
#list-dialog { height: 28; }
#help-dialog { height: 32; }
#list-dialog-title, #help-title { text-style: bold; color: $text; }
#list-dialog-hint { height: 2; color: $text-muted; }
#draft-values { height: 1fr; min-height: 3; border: round $border; }
#draft-empty { height: auto; color: $text-muted; }
#draft-selected { height: 1; }
#draft-feedback { height: 1; color: $warning; }
.input-row, .button-row { height: 3; }
.button-row Button { margin-right: 1; min-width: 10; width: 1fr; }
#new-value { width: 1fr; }
#add-value { margin-left: 1; min-width: 8; width: 8; }
#help-content { margin: 1 0; }
#help-scroll { height: 1fr; }
"""


class SettingsWorkspace(Vertical):
    """Stable widget tree shared by the event/controller methods."""

    _rendered_text: dict[str, str]
    _editing_enabled: bool
    _reset_disabled_when_enabled: bool

    def on_mount(self) -> None:
        self._rendered_text = {}
        self._editing_enabled = True
        self._reset_disabled_when_enabled = True

    def compose(self) -> ComposeResult:
        yield Static("KonoKashi  /  Settings", id="brand")
        with Horizontal(id="search-row"):
            yield FixedSurfaceInput(placeholder="Search settings  /", id="search")
            yield Button("Clear", id="clear-search", disabled=True)
        with VerticalScroll(id="workspace"):
            with Vertical(id="navigation"):
                yield OptionList(
                    *(
                        Option(f"{index}  {category.value}", id=category.name.lower())
                        for index, category in enumerate(SettingCategory, 1)
                    ),
                    id="categories",
                    markup=False,
                    compact=True,
                )
                yield FixedSurfaceOptionList(
                    id="settings-list", markup=False, compact=True
                )
            with VerticalScroll(id="detail"):
                yield Label("", id="detail-title")
                yield Static("", id="detail-description", markup=False)
                yield Static("", id="editor-hint", markup=False)
                with Container(id="editor-slot"):
                    with Horizontal(id="boolean-row", classes="hidden"):
                        yield SettingSwitch(id="boolean-value")
                        yield Label("Enabled", id="boolean-label")
                    with Horizontal(id="integer-row", classes="hidden"):
                        yield Button("-", id="integer-minus")
                        yield Input(id="integer-value", type="integer")
                        yield Button("+", id="integer-plus")
                        yield Button("Apply", id="integer-apply", variant="primary")
                    with Horizontal(id="string-row", classes="hidden"):
                        yield Input(id="string-value")
                        yield Button("Apply", id="string-apply", variant="primary")
                    yield Button(
                        "Edit list…",
                        id="edit-list",
                        classes="hidden",
                    )
                with Horizontal(id="detail-actions"):
                    yield Button(
                        "Reset to default",
                        id="reset-setting",
                    )
                    yield Button("Help", id="show-help")
                yield Static("", id="detail-value", markup=False)
                yield Static("", id="detail-metadata", markup=False)
        yield Static("", id="context-hint", markup=False)
        yield Static("Loading settings…", id="status", markup=False)

    def show_setting(self, resolved: ResolvedSetting) -> None:
        definition = resolved.definition
        self._update_text("#detail-title", definition.title)
        self._update_text("#detail-description", definition.description)
        self._update_text(
            "#detail-value",
            f"Current: {format_value(resolved.value)}\n"
            f"{origin_text(resolved.origin)} · "
            f"Default: {format_value(definition.default)}",
        )
        self._update_text(
            "#detail-metadata",
            f"Key: {definition.key}\n"
            f"Scope: {definition.scope.value}\n"
            f"Reload: {reload_text(definition.reload)}",
        )
        for selector, value_type in (
            ("#boolean-row", SettingType.BOOLEAN),
            ("#integer-row", SettingType.INTEGER),
            ("#string-row", SettingType.STRING),
            ("#edit-list", SettingType.STRING_LIST),
        ):
            self._show_editor(selector, definition.value_type is value_type)
        if definition.value_type is SettingType.BOOLEAN:
            boolean = self.query_one("#boolean-value", SettingSwitch)
            boolean_value = bool(resolved.value)
            boolean.project(definition.key, boolean_value)
            self._update_text("#boolean-label", "On" if boolean_value else "Off")
            hint = "Enter or Space toggles · Saves immediately"
        elif definition.value_type is SettingType.INTEGER:
            integer = self.query_one("#integer-value", Input)
            integer_value = str(resolved.value)
            if integer.value != integer_value:
                integer.value = integer_value
            hint = (
                f"{definition.minimum}-{definition.maximum} · "
                "Enter or Apply saves · Esc cancels"
            )
        elif definition.value_type is SettingType.STRING:
            string = self.query_one("#string-value", Input)
            string_value = str(resolved.value)
            if string.value != string_value:
                string.value = string_value
            if definition.choices:
                hint = "Choices: " + ", ".join(definition.choices)
            else:
                hint = "Enter or Apply validates and saves · Esc keeps the draft"
        else:
            hint = "Add or remove items in a draft · Apply saves"
            self.query_one("#edit-list", Button).label = (
                "Edit folders…"
                if definition.key == "library.roots"
                else "Edit players…"
            )
        self._update_text("#editor-hint", hint)
        self._reset_disabled_when_enabled = (
            resolved.value == definition.default
            and resolved.origin is SettingOrigin.DEFAULT
        )
        self.set_editing_enabled(self._editing_enabled)

    def show_empty(self, text: str) -> None:
        self._update_text("#detail-title", "No setting selected")
        self._update_text("#detail-description", text)
        self._update_text("#detail-value", " \n ")
        self._update_text("#detail-metadata", " \n \n ")
        self._update_text("#editor-hint", "Esc or Clear returns to your category")
        self.query_one("#boolean-value", SettingSwitch).setting_key = ""
        for selector in (
            "#boolean-row",
            "#integer-row",
            "#string-row",
            "#edit-list",
        ):
            self._show_editor(selector, False)
        self._reset_disabled_when_enabled = True
        for selector in (
            "#boolean-value",
            "#integer-value",
            "#integer-minus",
            "#integer-plus",
            "#integer-apply",
            "#string-value",
            "#string-apply",
            "#edit-list",
        ):
            self.query_one(selector).disabled = True
        self.query_one("#reset-setting", Button).disabled = True

    def _update_text(self, selector: str, content: str) -> None:
        if self._rendered_text.get(selector) == content:
            return
        widget = self.query_one(selector, Static)
        width = widget.content_region.width
        if not widget.is_mounted or width <= 0:
            widget.update(content)
        else:
            previous_height = widget.get_content_height(
                widget.container_size,
                widget.screen.size,
                width,
            )
            widget.update(content, layout=False)
            current_height = widget.get_content_height(
                widget.container_size,
                widget.screen.size,
                width,
            )
            if current_height != previous_height:
                widget.refresh(layout=True)
        self._rendered_text[selector] = content

    def _show_editor(self, selector: str, visible: bool) -> None:
        editor = self.query_one(selector, Widget)
        # Keep the semantic marker without rematching every descendant's CSS.
        # Visibility is inherited and Textual excludes invisible focus targets.
        editor.set_class(not visible, "hidden", update=False)
        editor.visible = visible

    def set_editing_enabled(self, enabled: bool) -> None:
        self._editing_enabled = enabled
        for selector in (
            "#boolean-value",
            "#integer-value",
            "#integer-minus",
            "#integer-plus",
            "#integer-apply",
            "#string-value",
            "#string-apply",
            "#edit-list",
        ):
            self.query_one(selector).disabled = not enabled
        self.query_one("#reset-setting").disabled = (
            not enabled or self._reset_disabled_when_enabled
        )

    def set_status(
        self,
        text: str,
        *,
        error: bool = False,
        success: bool = False,
    ) -> None:
        status = self.query_one("#status", Static)
        status.update(text)
        status.set_class(error, "error")
        status.set_class(success and not error, "success")


def format_value(value: SettingValue) -> str:
    if isinstance(value, tuple):
        return "(empty)" if not value else " → ".join(value)
    if isinstance(value, bool):
        return "On" if value else "Off"
    return str(value)


def origin_text(origin: SettingOrigin) -> str:
    if origin is SettingOrigin.DEFAULT:
        return "Using default"
    if origin is SettingOrigin.LEGACY_MIGRATION:
        return "Migrated preference"
    return "Customized"


def reload_text(behavior: ReloadBehavior) -> str:
    if behavior is ReloadBehavior.LIVE:
        return "applies immediately"
    if behavior is ReloadBehavior.NEXT_OPERATION:
        return "applies to the next operation"
    return "requires restart"
