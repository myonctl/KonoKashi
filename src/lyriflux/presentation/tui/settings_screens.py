"""Modal editors and help for the Textual settings frontend."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option


class OrderedListEditorScreen(ModalScreen[tuple[str, ...] | None]):
    """Explicit draft editor for ordered string and path settings."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        title: str,
        values: tuple[str, ...],
        *,
        path_values: bool = False,
    ) -> None:
        super().__init__()
        self._title = title
        self._values = list(values)
        self._path_values = path_values

    def compose(self) -> ComposeResult:
        placeholder = (
            "Add one absolute folder path" if self._path_values else "Add value"
        )
        with Vertical(id="list-dialog"):
            yield Label(self._title, id="list-dialog-title")
            yield Static(
                "Ordering is significant. Changes are a draft until Apply.",
                id="list-dialog-hint",
            )
            yield OptionList(id="draft-values", markup=False)
            with Horizontal(classes="input-row"):
                yield Input(placeholder=placeholder, id="new-value")
                yield Button("Add", id="add-value", variant="primary")
            with Horizontal(classes="button-row"):
                yield Button("Remove", id="remove-value")
                yield Button("Move up", id="move-value-up")
                yield Button("Move down", id="move-value-down")
            with Horizontal(classes="button-row"):
                yield Button("Cancel", id="cancel-list")
                yield Button("Apply", id="apply-list", variant="success")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#new-value", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "new-value":
            self._add()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions: dict[str, Callable[[], object]] = {
            "add-value": self._add,
            "remove-value": self._remove,
            "move-value-up": lambda: self._move(-1),
            "move-value-down": lambda: self._move(1),
            "cancel-list": self.action_cancel,
            "apply-list": lambda: self.dismiss(tuple(self._values)),
        }
        action = actions.get(event.button.id or "")
        if action is not None:
            action()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _add(self) -> None:
        editor = self.query_one("#new-value", Input)
        value = editor.value.strip()
        if not value:
            self.notify("Enter a non-empty value.", severity="warning")
            return
        self._values.append(value)
        editor.value = ""
        self._refresh(highlight=len(self._values) - 1)
        editor.focus()

    def _remove(self) -> None:
        options = self.query_one("#draft-values", OptionList)
        index = options.highlighted
        if index is None or not 0 <= index < len(self._values):
            self.notify("Select a value to remove.", severity="warning")
            return
        self._values.pop(index)
        self._refresh(highlight=min(index, len(self._values) - 1))

    def _move(self, offset: int) -> None:
        options = self.query_one("#draft-values", OptionList)
        index = options.highlighted
        if index is None:
            return
        target = index + offset
        if not 0 <= index < len(self._values) or not 0 <= target < len(self._values):
            return
        self._values[index], self._values[target] = (
            self._values[target],
            self._values[index],
        )
        self._refresh(highlight=target)

    def _refresh(self, *, highlight: int | None = None) -> None:
        options = self.query_one("#draft-values", OptionList)
        options.clear_options()
        options.add_options(
            Option(value, id=f"draft-{index}")
            for index, value in enumerate(self._values)
        )
        if self._values:
            options.highlighted = 0 if highlight is None else max(0, highlight)


class SettingsHelpScreen(ModalScreen[None]):
    """Discoverable keyboard and semantics reference."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close"),
        Binding("question_mark", "close", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Label("LyriFlux settings help", id="help-title")
            yield Static(
                "↑/↓ or j/k  Navigate settings\n"
                "1-4          Select Players, Lyrics, Desktop, Library\n"
                "/            Search title, description, or canonical key\n"
                "Enter/Space  Activate the focused control\n"
                "Tab/Shift-Tab Move focus\n"
                "r            Reset the selected setting\n"
                "Esc          Clear search, cancel, or return to navigation\n"
                "?            Toggle this help\n"
                "q / Ctrl+C   Quit cleanly\n\n"
                "All writes use the canonical validated TOML service. Invalid "
                "external files keep the last-known-good values visible.",
                id="help-content",
            )
            yield Button("Close", id="close-help", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-help":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
