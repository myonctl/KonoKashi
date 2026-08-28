"""Composed widgets and snapshot rendering for the settings TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, OptionList, Static, Switch
from textual.widgets.option_list import Option

from lyriflux.application.settings import (
    ReloadBehavior,
    ResolvedSetting,
    SettingCategory,
    SettingOrigin,
    SettingType,
    SettingValue,
)

APP_CSS = """
Screen { background: $surface; }
#shell { height: 1fr; padding: 0 1; }
#brand { height: 3; padding: 0 1; color: $text; background: $boost; }
#search { margin: 1 0; }
#workspace { height: 1fr; layout: horizontal; }
#navigation { width: 38; min-width: 28; height: 1fr; margin-right: 1; }
#categories { height: 8; border: round $primary; }
#settings-list { height: 1fr; border: round $primary; }
.section-heading { height: 1; color: $text-muted; margin-top: 1; }
#detail { width: 1fr; height: 1fr; border: round $secondary; padding: 1 2; }
#detail-title { text-style: bold; color: $accent; margin-bottom: 1; }
#detail-description { margin-bottom: 1; }
#detail-value { margin: 1 0; text-style: bold; }
#detail-metadata { color: $text-muted; margin-bottom: 1; }
#boolean-row, #integer-row, #detail-actions { height: 3; margin-top: 1; }
#integer-value { width: 12; margin: 0 1; }
#integer-minus, #integer-plus { width: 5; }
#integer-apply { margin-left: 1; }
#edit-list { width: auto; }
#reset-setting { margin-left: 1; }
#status { height: auto; min-height: 2; padding: 0 1; color: $text-muted; }
#status.error { color: $error; }
#status.success { color: $success; }
.hidden { display: none; }
#too-small { display: none; layer: overlay; width: 100%; height: 100%;
    content-align: center middle; background: $surface; color: $warning; }
Screen.too-small #too-small { display: block; }
Screen.too-small #shell, Screen.too-small Footer, Screen.too-small Header {
    visibility: hidden;
}
Screen.-narrow #workspace { layout: vertical; }
Screen.-narrow #navigation { width: 1fr; height: 15; margin-right: 0; }
Screen.-narrow #categories { height: 5; }
Screen.-narrow #detail { width: 1fr; height: 1fr; }
OrderedListEditorScreen, SettingsHelpScreen {
    align: center middle; background: $background 65%;
}
#list-dialog, #help-dialog {
    width: 78; max-width: 92%; height: auto; max-height: 90%;
    border: thick $primary; background: $surface; padding: 1 2;
}
#list-dialog-title, #help-title { text-style: bold; color: $accent; }
#list-dialog-hint { color: $text-muted; margin-bottom: 1; }
#draft-values { height: 12; border: round $secondary; }
.input-row, .button-row { height: 3; margin-top: 1; }
.button-row Button { margin-right: 1; }
#new-value { width: 1fr; }
#add-value { margin-left: 1; }
#help-content { margin: 1 0; }
"""


class SettingsWorkspace(Vertical):
    """Stable widget tree shared by the event/controller methods."""

    def compose(self) -> ComposeResult:
        yield Static(
            "LyriFlux  ·  Settings\nOne schema shared by TUI, desktop, CLI, and TOML",
            id="brand",
        )
        yield Input(
            placeholder="Search title, description, or key  (press /)",
            id="search",
        )
        with Horizontal(id="workspace"):
            with Vertical(id="navigation"):
                yield Label("Categories", classes="section-heading")
                yield OptionList(
                    *(
                        Option(category.value, id=category.name.lower())
                        for category in SettingCategory
                    ),
                    id="categories",
                    markup=False,
                    compact=True,
                )
                yield Label("Settings", classes="section-heading")
                yield OptionList(id="settings-list", markup=False, compact=True)
            with VerticalScroll(id="detail"):
                yield Label("", id="detail-title")
                yield Static("", id="detail-description", markup=False)
                yield Static("", id="detail-value", markup=False)
                yield Static("", id="detail-metadata", markup=False)
                with Horizontal(id="boolean-row", classes="hidden"):
                    yield Switch(id="boolean-value")
                    yield Label("Enabled", id="boolean-label")
                with Horizontal(id="integer-row", classes="hidden"):
                    yield Button("-", id="integer-minus")
                    yield Input(id="integer-value", type="integer")
                    yield Button("+", id="integer-plus")
                    yield Button("Apply", id="integer-apply", variant="primary")
                yield Button(
                    "Edit ordered values…",
                    id="edit-list",
                    classes="hidden",
                )
                with Horizontal(id="detail-actions"):
                    yield Button(
                        "Reset selected",
                        id="reset-setting",
                        variant="warning",
                    )
                    yield Button("Help", id="show-help")
        yield Static("Loading canonical settings…", id="status", markup=False)

    def show_setting(self, resolved: ResolvedSetting) -> None:
        definition = resolved.definition
        self.query_one("#detail-title", Label).update(definition.title)
        self.query_one("#detail-description", Static).update(definition.description)
        self.query_one("#detail-value", Static).update(
            f"Current: {format_value(resolved.value)}\n"
            f"Default: {format_value(definition.default)}"
        )
        self.query_one("#detail-metadata", Static).update(
            f"Key: {definition.key}\n"
            f"Origin: {resolved.origin.value}  ·  Scope: {definition.scope.value}\n"
            f"Reload: {reload_text(definition.reload)}"
        )
        self.query_one("#boolean-row", Horizontal).set_class(
            definition.value_type is not SettingType.BOOLEAN,
            "hidden",
        )
        self.query_one("#integer-row", Horizontal).set_class(
            definition.value_type is not SettingType.INTEGER,
            "hidden",
        )
        self.query_one("#edit-list", Button).set_class(
            definition.value_type is not SettingType.STRING_LIST,
            "hidden",
        )
        if definition.value_type is SettingType.BOOLEAN:
            self.query_one("#boolean-value", Switch).value = bool(resolved.value)
        elif definition.value_type is SettingType.INTEGER:
            self.query_one("#integer-value", Input).value = str(resolved.value)
        self.query_one("#reset-setting", Button).disabled = (
            resolved.value == definition.default
            and resolved.origin is SettingOrigin.DEFAULT
        )

    def show_empty(self, text: str) -> None:
        self.query_one("#detail-title", Label).update("No setting selected")
        self.query_one("#detail-description", Static).update(text)
        self.query_one("#detail-value", Static).update("")
        self.query_one("#detail-metadata", Static).update("")
        for selector in ("#boolean-row", "#integer-row", "#edit-list"):
            self.query_one(selector).add_class("hidden")
        self.query_one("#reset-setting", Button).disabled = True

    def set_editing_enabled(self, enabled: bool) -> None:
        for selector in (
            "#boolean-value",
            "#integer-value",
            "#integer-minus",
            "#integer-plus",
            "#integer-apply",
            "#edit-list",
            "#reset-setting",
        ):
            self.query_one(selector).disabled = not enabled

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


def reload_text(behavior: ReloadBehavior) -> str:
    if behavior is ReloadBehavior.LIVE:
        return "applies immediately"
    if behavior is ReloadBehavior.NEXT_OPERATION:
        return "applies to the next operation"
    return "requires restart"
