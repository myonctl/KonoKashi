"""Stage 15 regressions for actual navigation, editing and layout failures."""

from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Input, OptionList, Static

from lyriflux.application.settings import SETTINGS_SCHEMA, SettingCategory
from lyriflux.presentation.tui.settings_app import SettingsApp
from lyriflux.presentation.tui.settings_controls import SettingSwitch
from tests.test_settings_tui import (
    CountingSettingsFile,
    CountingSettingsService,
    _run_app,
    _service,
    _text,
    _wait_until,
)


def test_rapid_snapshot_projection_never_saves_any_setting(tmp_path: Path) -> None:
    config = CountingSettingsFile(tmp_path / "config.toml")
    service = CountingSettingsService(config)
    assert service.initialize().applied
    baseline = service.current

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        await pilot.pause()
        config.reads = config.updates = config.resets = 0
        for _ in range(3):
            for category in range(4):
                app.action_category(category)
                options = app.query_one("#settings-list", OptionList)
                for index in range(options.option_count):
                    options.highlighted = index
            search = app.query_one("#search", Input)
            search.value = "romanized"
            search.value = "no-results"
            search.value = ""
        await pilot.pause()
        assert service.current == baseline
        assert (config.reads, config.updates, config.resets) == (0, 0, 0)
        assert not app.query_one("#status", Static).has_class("error")

    _run_app(SettingsApp(service=service, watch_interval=0), scenario)


def test_user_toggle_remains_bound_to_key_when_selection_moves(tmp_path: Path) -> None:
    service = _service(tmp_path / "config.toml")

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        app.action_category(1)
        app.query_one("#boolean-value", SettingSwitch).toggle()
        app.query_one("#settings-list", OptionList).highlighted = 1
        await _wait_until(lambda: not service.get("lyrics.display.original"))
        await pilot.pause()
        assert service.get("lyrics.display.romanized") is True
        assert app._selected_key == "lyrics.display.romanized"

    _run_app(SettingsApp(service=service, watch_interval=0), scenario)


def test_search_enter_clear_and_category_return_have_explicit_focus(
    tmp_path: Path,
) -> None:
    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        await pilot.press("/", *"lyrics.display.romanized", "enter")
        assert app.focused is app.query_one("#settings-list", OptionList)
        assert app._selected_key == "lyrics.display.romanized"
        assert "1 results" in str(app.query_one("#settings-list").border_title)
        await pilot.press("4")
        assert app.query_one("#search", Input).value == ""
        assert app._category is SettingCategory.LIBRARY
        await pilot.press("/", *"nothing matches")
        assert not app._selected_key
        assert "clear" in _text(app.query_one("#detail-description", Static))
        assert await pilot.click("#clear-search")
        assert app.focused is app.query_one("#search", Input)
        await pilot.press("escape")
        assert app.focused is app.query_one("#settings-list", OptionList)

    _run_app(
        SettingsApp(service=_service(tmp_path / "config.toml"), watch_interval=0),
        scenario,
    )


def test_vim_category_navigation_and_numeric_escape_do_not_save(tmp_path: Path) -> None:
    service = _service(tmp_path / "config.toml")

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        categories = app.query_one("#categories", OptionList)
        categories.focus()
        await pilot.press("j")
        assert app._category is SettingCategory.LYRICS
        assert app.focused is categories
        await pilot.press("4", "j", "j", "enter")
        editor = app.query_one("#integer-value", Input)
        assert app.focused is editor
        editor.value = "8"
        await pilot.press("escape")
        assert editor.value == "4"
        assert service.get("library.metadata_workers") == 4
        assert app.focused is app.query_one("#settings-list", OptionList)

    _run_app(SettingsApp(service=service, watch_interval=0), scenario)


@pytest.mark.parametrize("size", [(50, 20), (70, 24), (100, 28), (160, 40)])
def test_list_actions_fit_and_empty_editor_remains_keyboard_usable(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        await pilot.press("enter", "enter")
        await _wait_until(lambda: len(app.screen_stack) == 2)
        await pilot.pause()
        screen = app.screen
        for selector in ("#apply-list", "#cancel-list", "#new-value"):
            control = screen.query_one(selector)
            assert control.region.width > 0
            assert screen.region.contains_region(control.region), (
                size,
                selector,
                control.region,
            )
        assert screen.query_one("#remove-value", Button).disabled
        assert screen.query_one("#draft-empty", Static).display
        await pilot.press(*"音楽 player", "enter")
        await pilot.press(*"音楽 player", "enter")
        assert screen.query_one("#draft-values", OptionList).option_count == 1
        assert "already" in _text(screen.query_one("#draft-feedback", Static))
        await pilot.press("escape")
        assert len(app.screen_stack) == 1

    _run_app(
        SettingsApp(service=_service(tmp_path / "config.toml"), watch_interval=0),
        scenario,
        size=size,
    )


def test_invalid_list_stays_open_and_q_cancels_without_exiting(tmp_path: Path) -> None:
    service = _service(tmp_path / "config.toml")

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        await pilot.press("4", "enter", "enter", *"relative/path", "enter", "ctrl+s")
        assert len(app.screen_stack) == 2
        assert "absolute" in _text(app.screen.query_one("#draft-feedback", Static))
        app.screen.query_one("#draft-values", OptionList).focus()
        await pilot.press("q")
        assert len(app.screen_stack) == 1
        assert service.get("library.roots") == ()

    _run_app(SettingsApp(service=service, watch_interval=0), scenario)


@pytest.mark.parametrize("size", [(50, 20), (70, 24), (100, 28), (160, 40)])
def test_help_close_stays_visible_beside_scrollable_text(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        await pilot.press("?")
        await pilot.pause()
        close = app.screen.query_one("#close-help", Button)
        assert app.screen.region.contains_region(close.region)
        assert await pilot.click("#close-help")
        assert len(app.screen_stack) == 1

    _run_app(
        SettingsApp(service=_service(tmp_path / "config.toml"), watch_interval=0),
        scenario,
        size=size,
    )


def test_default_and_explicit_default_override_are_distinct(tmp_path: Path) -> None:
    service = _service(tmp_path / "config.toml")
    service.set("lyrics.display.original", True)

    async def scenario(app: SettingsApp, _pilot: Pilot[int]) -> None:
        app.action_category(1)
        assert "Customized" in app._setting_prompt(SETTINGS_SCHEMA[2])
        assert "Using default" in app._setting_prompt(SETTINGS_SCHEMA[3])
        assert not app.query_one("#reset-setting", Button).disabled

    _run_app(SettingsApp(service=service, watch_interval=0), scenario)


def test_apply_saves_pending_input_once_and_duplicate_keeps_draft(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "config.toml")

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        await pilot.press("enter", "enter", *"strawberry", "ctrl+s")
        await _wait_until(lambda: service.get("players.preferred") == ("strawberry",))
        assert len(app.screen_stack) == 1
        await pilot.pause()
        assert app.focused is app.query_one("#settings-list", OptionList)
        await pilot.press("enter", "enter", *"strawberry", "ctrl+s")
        assert len(app.screen_stack) == 2
        assert "already" in _text(app.screen.query_one("#draft-feedback", Static))
        assert service.get("players.preferred") == ("strawberry",)
        await pilot.press("escape")

    _run_app(SettingsApp(service=service, watch_interval=0), scenario)


def test_file_diagnostic_survives_draft_cancellation_until_repair(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    service = _service(path)

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        app._schedule_disk_check()
        await _wait_until(lambda: not app._watch_busy)
        path.write_text("schema_version = 999\n", encoding="utf-8")
        app._schedule_disk_check()
        await _wait_until(lambda: not app._watch_busy)
        await pilot.press("enter", "enter", "escape")
        assert "Last-known-good" in _text(app.query_one("#status", Static))
        path.write_text("schema_version = 1\n", encoding="utf-8")
        app._schedule_disk_check()
        await _wait_until(lambda: not app._watch_busy)
        assert not app.query_one("#status", Static).has_class("error")

    _run_app(SettingsApp(service=service, watch_interval=0), scenario)


def test_hidden_editors_never_enter_keyboard_focus_order(tmp_path: Path) -> None:
    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        for category, selected in ((0, 0), (1, 0), (3, 2)):
            app.action_category(category)
            app.query_one("#settings-list", OptionList).highlighted = selected
            await pilot.pause()
            for _ in range(14):
                await pilot.press("tab")
                assert app.focused is not None
                assert app.focused.visible
                assert not app.focused.disabled
            for selector in ("#boolean-row", "#integer-row", "#edit-list"):
                widget = app.query_one(selector)
                assert widget.visible is not widget.has_class("hidden")

    _run_app(
        SettingsApp(service=_service(tmp_path / "config.toml"), watch_interval=0),
        scenario,
        size=(55, 22),
    )
