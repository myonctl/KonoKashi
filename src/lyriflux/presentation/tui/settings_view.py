"""Composed widgets and snapshot rendering for the settings TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.geometry import Size
from textual.reactive import Reactive
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
#search { width: 1fr; height: 3; margin: 1 0; }
#workspace { height: 1fr; layout: horizontal; }
#navigation { width: 38; min-width: 28; height: 1fr; margin-right: 1; }
#categories { height: 8; border: round $primary; }
#settings-list { height: 1fr; border: round $primary; }
.section-heading { height: 1; color: $text-muted; margin-top: 1; }
#detail { width: 1fr; height: 1fr; border: round $secondary; padding: 1 2; }
#detail-title { width: 1fr; height: 1; text-style: bold; color: $accent;
    margin-bottom: 1; }
#detail-description { width: 1fr; margin-bottom: 1; }
#detail-value { width: 1fr; margin: 1 0; text-style: bold; }
#detail-metadata { width: 1fr; color: $text-muted; margin-bottom: 1; }
#editor-slot { width: 1fr; height: 4; margin-top: 1;
    layers: boolean integer list; }
#boolean-row { layer: boolean; }
#integer-row { layer: integer; }
#edit-list { layer: list; }
#boolean-row, #integer-row { width: 1fr; height: 3; }
#detail-actions { height: 3; margin-top: 1; }
#integer-value { width: 12; margin: 0 1; }
#integer-minus, #integer-plus { width: 5; }
#integer-apply { margin-left: 1; }
#edit-list { width: auto; }
#reset-setting { margin-left: 1; }
#status { height: auto; min-height: 2; padding: 0 1; color: $text-muted; }
#status.error { color: $error; }
#status.success { color: $success; }
.hidden { visibility: hidden; }
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
OrderedListEditorScreen, SettingsHelpScreen { align: center middle; }
OrderedListEditorScreen { background: $background 65%; }
SettingsHelpScreen { background: $surface; }
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


class FixedSurfaceInput(Input):
    """Input whose fixed CSS box does not request layout for text edits."""

    virtual_size = Reactive(Size(0, 0), layout=False)


class FixedSurfaceOptionList(OptionList):
    """Option list whose fixed CSS box owns scrolling without parent reflow."""

    virtual_size = Reactive(Size(0, 0), layout=False)


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
        yield Static(
            "LyriFlux  ·  Settings\nOne schema shared by TUI, desktop, CLI, and TOML",
            id="brand",
        )
        yield FixedSurfaceInput(
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
                yield FixedSurfaceOptionList(
                    id="settings-list", markup=False, compact=True
                )
            with VerticalScroll(id="detail"):
                yield Label("", id="detail-title")
                yield Static("", id="detail-description", markup=False)
                yield Static("", id="detail-value", markup=False)
                yield Static("", id="detail-metadata", markup=False)
                with Container(id="editor-slot"):
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
        self._update_text("#detail-title", definition.title)
        self._update_text("#detail-description", definition.description)
        self._update_text(
            "#detail-value",
            f"Current: {format_value(resolved.value)}\n"
            f"Default: {format_value(definition.default)}",
        )
        self._update_text(
            "#detail-metadata",
            f"Key: {definition.key}\n"
            f"Origin: {resolved.origin.value}  ·  Scope: {definition.scope.value}\n"
            f"Reload: {reload_text(definition.reload)}",
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
            boolean = self.query_one("#boolean-value", Switch)
            boolean_value = bool(resolved.value)
            if boolean.value != boolean_value:
                boolean.value = boolean_value
        elif definition.value_type is SettingType.INTEGER:
            integer = self.query_one("#integer-value", Input)
            integer_value = str(resolved.value)
            if integer.value != integer_value:
                integer.value = integer_value
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
        self.query_one("#boolean-row", Horizontal).add_class("hidden")
        self.query_one("#integer-row", Horizontal).add_class("hidden")
        self.query_one("#edit-list", Button).add_class("hidden")
        self._reset_disabled_when_enabled = True
        for selector in (
            "#boolean-value",
            "#integer-value",
            "#integer-minus",
            "#integer-plus",
            "#integer-apply",
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

    def set_editing_enabled(self, enabled: bool) -> None:
        self._editing_enabled = enabled
        for selector, group in (
            ("#boolean-value", "#boolean-row"),
            ("#integer-value", "#integer-row"),
            ("#integer-minus", "#integer-row"),
            ("#integer-plus", "#integer-row"),
            ("#integer-apply", "#integer-row"),
            ("#edit-list", "#edit-list"),
        ):
            hidden = self.query_one(group).has_class("hidden")
            self.query_one(selector).disabled = not enabled or hidden
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


def reload_text(behavior: ReloadBehavior) -> str:
    if behavior is ReloadBehavior.LIVE:
        return "applies immediately"
    if behavior is ReloadBehavior.NEXT_OPERATION:
        return "applies to the next operation"
    return "requires restart"
