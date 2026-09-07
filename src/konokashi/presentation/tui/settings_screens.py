"""Modal editors and help for the Textual settings frontend."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from konokashi.presentation.tui.settings_bindings import help_text
from konokashi.presentation.tui.settings_controls import FixedSurfaceOptionList


class OrderedListEditorScreen(ModalScreen[tuple[str, ...] | None]):
    """Explicit draft editor for ordered string and path settings."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel", show=False),
        Binding("ctrl+s", "apply", "Apply"),
        Binding("delete", "remove", "Remove", show=False),
        Binding("alt+up", "move(-1)", "Move up", show=False),
        Binding("alt+down", "move(1)", "Move down", show=False),
        Binding("j", "next_item", "Next", show=False),
        Binding("k", "previous_item", "Previous", show=False),
    ]

    def __init__(
        self,
        title: str,
        values: tuple[str, ...],
        *,
        path_values: bool = False,
        preferred: bool = True,
        validate: Callable[[tuple[str, ...]], str | None] | None = None,
    ) -> None:
        super().__init__()
        self._title = title
        self._values = list(values)
        self._path_values = path_values
        self._preferred = preferred and not path_values
        self._validate = validate

    def compose(self) -> ComposeResult:
        placeholder = (
            "Absolute folder path (spaces are fine)"
            if self._path_values
            else "Player, e.g. strawberry"
        )
        yield Static(
            "Resize to at least 50 x 20 cells. Esc cancels the draft.",
            id="modal-too-small",
        )
        with Vertical(id="list-dialog"):
            yield Label(self._title, id="list-dialog-title")
            yield Static(
                ("First player has highest preference. " if self._preferred else "")
                + "Changes are a draft until Apply.\n"
                "Esc cancels · Ctrl+S applies · Alt+↑/↓ moves an item",
                id="list-dialog-hint",
            )
            yield Static("", id="draft-empty", markup=False)
            yield FixedSurfaceOptionList(id="draft-values", markup=False)
            yield Static("", id="draft-selected", markup=False)
            yield Static("", id="draft-feedback", markup=False)
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
        self.watch(
            self.query_one("#draft-values", OptionList),
            "highlighted",
            self._selection_changed,
        )
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
            "apply-list": self.action_apply,
        }
        action = actions.get(event.button.id or "")
        if action is not None:
            action()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_apply(self) -> None:
        if self.query_one("#new-value", Input).value.strip() and not self._add():
            return
        values = tuple(self._values)
        diagnostic = self._validate(values) if self._validate else None
        if diagnostic:
            self.query_one("#draft-feedback", Static).update(diagnostic)
            return
        self.dismiss(values)

    def action_remove(self) -> None:
        if self.focused is self.query_one("#draft-values", OptionList):
            self._remove()

    def action_move(self, offset: int) -> None:
        self._move(offset)

    def action_next_item(self) -> None:
        options = self.query_one("#draft-values", OptionList)
        options.focus()
        options.action_cursor_down()

    def action_previous_item(self) -> None:
        options = self.query_one("#draft-values", OptionList)
        options.focus()
        options.action_cursor_up()

    def _add(self) -> bool:
        editor = self.query_one("#new-value", Input)
        value = editor.value.strip()
        if not value:
            self.query_one("#draft-feedback", Static).update("Enter an item first.")
            editor.focus()
            return False
        if value in self._values:
            self.query_one(
                "#draft-values", OptionList
            ).highlighted = self._values.index(value)
            self.query_one("#draft-feedback", Static).update(
                "That item is already in the list."
            )
            editor.focus()
            return False
        self._values.append(value)
        editor.value = ""
        self._refresh(highlight=len(self._values) - 1)
        self.query_one("#draft-feedback", Static).update(
            "Added to draft · Apply to save"
        )
        editor.focus()
        return True

    def _remove(self) -> None:
        options = self.query_one("#draft-values", OptionList)
        index = options.highlighted
        if index is None or not 0 <= index < len(self._values):
            return
        self._values.pop(index)
        self._refresh(highlight=min(index, len(self._values) - 1))
        if self._values:
            options.focus()
        else:
            self.query_one("#new-value", Input).focus()

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
        options.focus()

    def _refresh(self, *, highlight: int | None = None) -> None:
        options = self.query_one("#draft-values", OptionList)
        options.clear_options()
        options.add_options(
            Option(value, id=f"draft-{index}")
            for index, value in enumerate(self._values)
        )
        if self._values:
            options.highlighted = 0 if highlight is None else max(0, highlight)
        options.border_title = f"{len(self._values)} items"
        empty = self.query_one("#draft-empty", Static)
        empty.display = not self._values
        empty.update(
            "No folders yet. Add a folder below."
            if self._path_values
            else "No players listed. Add a player below."
        )
        self.query_one("#draft-feedback", Static).update("")
        self._selection_changed(options.highlighted)

    def _selection_changed(self, index: int | None) -> None:
        selected = index is not None and 0 <= index < len(self._values)
        self.query_one("#remove-value", Button).disabled = not selected
        self.query_one("#move-value-up", Button).disabled = not selected or index == 0
        self.query_one("#move-value-down", Button).disabled = (
            not selected or index == len(self._values) - 1
        )
        text = (
            f"Selected: {self._values[index]}" if selected and index is not None else ""
        )
        self.query_one("#draft-selected", Static).update(text)


class SettingsHelpScreen(ModalScreen[None]):
    """Discoverable keyboard and semantics reference."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close"),
        Binding("question_mark", "close", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Static(
            "Resize to at least 50 x 20 cells. Esc closes help.", id="modal-too-small"
        )
        with Vertical(id="help-dialog"):
            yield Label("KonoKashi settings help", id="help-title")
            with VerticalScroll(id="help-scroll"):
                yield Static(help_text(), id="help-content", markup=False)
            yield Button("Close", id="close-help", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-help":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
