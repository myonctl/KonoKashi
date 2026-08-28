"""Stage 13 composed Textual settings application regressions."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Input, Label, OptionList, Static

from lyriflux import cli
from lyriflux.application.settings import SETTINGS_SCHEMA, SettingCategory, SettingType
from lyriflux.application.settings_service import CanonicalSettingsService
from lyriflux.infrastructure.configuration.toml_file import TomlSettingsFile
from lyriflux.presentation.tui.settings_app import SettingsApp
from lyriflux.presentation.tui.settings_runtime import config_signature

Scenario = Callable[[SettingsApp, Pilot[int]], Awaitable[None]]


def _service(path: Path) -> CanonicalSettingsService:
    service = CanonicalSettingsService(TomlSettingsFile(path))
    assert service.initialize().applied
    return service


def _run_app(
    app: SettingsApp,
    scenario: Scenario,
    *,
    size: tuple[int, int] = (120, 40),
) -> None:
    async def run() -> None:
        async with app.run_test(size=size) as pilot:
            await _wait_until(lambda: app._ui_ready)
            await scenario(app, pilot)

    loop = asyncio.new_event_loop()
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tui-test")
    loop.set_default_executor(executor)
    try:
        loop.run_until_complete(run())
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        loop.close()


async def _wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("timed out waiting for Textual state")
        await asyncio.sleep(0.01)


def _text(widget: Static | Label) -> str:
    return str(widget.render())


def test_schema_drives_all_categories_types_and_details(tmp_path: Path) -> None:
    service = _service(tmp_path / "config.toml")

    async def scenario(app: SettingsApp, _pilot: Pilot[int]) -> None:
        categories = app.query_one("#categories", OptionList)
        assert categories.option_count == len(SettingCategory) == 4
        seen: list[str] = []
        for index, category in enumerate(SettingCategory):
            app.action_category(index)
            expected = tuple(
                item for item in SETTINGS_SCHEMA if item.category is category
            )
            assert app.query_one("#settings-list", OptionList).option_count == len(
                expected
            )
            for item_index, definition in enumerate(expected):
                app.query_one("#settings-list", OptionList).highlighted = item_index
                await _wait_until(
                    lambda definition=definition: app._selected_key == definition.key
                )
                seen.append(app._selected_key)
                assert definition.title in _text(app.query_one("#detail-title", Label))
                metadata = _text(app.query_one("#detail-metadata", Static))
                assert definition.key in metadata
                assert definition.scope.value in metadata
                row = {
                    SettingType.BOOLEAN: "#boolean-row",
                    SettingType.INTEGER: "#integer-row",
                    SettingType.STRING_LIST: "#edit-list",
                }[definition.value_type]
                assert not app.query_one(row).has_class("hidden")
        assert seen == [item.key for item in SETTINGS_SCHEMA]

    _run_app(SettingsApp(service=service, watch_interval=0), scenario)


@pytest.mark.parametrize(
    ("query", "key"),
    [
        ("metadata workers", "library.metadata_workers"),
        ("policy-approved", "library.automatic_downloads"),
        ("desktop.lyrics.selectable", "desktop.lyrics.selectable"),
    ],
)
def test_search_matches_title_description_and_key(
    tmp_path: Path, query: str, key: str
) -> None:
    app = SettingsApp(service=_service(tmp_path / "config.toml"), watch_interval=0)

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        await pilot.press("/")
        await pilot.press(*query)
        await _wait_until(lambda: app.query_one("#search", Input).value == query)
        assert app.query_one("#settings-list", OptionList).option_count == 1
        assert app._visible_definitions[0].key == key
        await pilot.press("escape")
        assert app.query_one("#search", Input).value == ""

    _run_app(app, scenario)


def test_category_shortcuts_and_text_input_keep_printable_keys(tmp_path: Path) -> None:
    app = SettingsApp(service=_service(tmp_path / "config.toml"), watch_interval=0)

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        assert app.focused is app.query_one("#settings-list", OptionList)
        await pilot.press("4")
        assert app._category is SettingCategory.LIBRARY
        await pilot.press("/")
        await pilot.press(*"q1r?jk")
        assert app.query_one("#search", Input).value == "q1r?jk"
        assert app.screen_stack[-1] is app.screen

    _run_app(app, scenario)


def test_boolean_keyboard_change_and_individual_reset(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    service = _service(path)
    app = SettingsApp(service=service, watch_interval=0)

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        await pilot.press("2")
        assert app._selected_key == "lyrics.display.original"
        await pilot.press("enter", "space")
        await _wait_until(lambda: service.get("lyrics.display.original") is False)
        assert "original = false" in path.read_text(encoding="utf-8")
        app.query_one("#settings-list", OptionList).focus()
        await pilot.press("r")
        await _wait_until(lambda: service.get("lyrics.display.original") is True)
        assert "original" not in path.read_text(encoding="utf-8")

    _run_app(app, scenario)


def test_mouse_category_navigation_and_boolean_control(tmp_path: Path) -> None:
    service = _service(tmp_path / "config.toml")
    app = SettingsApp(service=service, watch_interval=0)

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        assert await pilot.click("#categories", offset=(3, 2))
        await _wait_until(lambda: app._category is SettingCategory.LYRICS)
        assert app._selected_key == "lyrics.display.original"
        assert await pilot.click("#boolean-value")
        await _wait_until(lambda: service.get("lyrics.display.original") is False)

    _run_app(app, scenario)


def test_integer_control_applies_bounds_and_preserves_prior_value(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "config.toml")
    app = SettingsApp(service=service, watch_interval=0)

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        app.action_category(3)
        app.query_one("#settings-list", OptionList).highlighted = 2
        await _wait_until(lambda: app._selected_key == "library.metadata_workers")
        await pilot.press("enter")
        app.query_one("#integer-value", Input).value = "8"
        await pilot.press("enter")
        await _wait_until(lambda: service.get("library.metadata_workers") == 8)
        editor = app.query_one("#integer-value", Input)
        editor.focus()
        await pilot.press("ctrl+a", "9", "enter")
        await asyncio.sleep(0.05)
        assert service.get("library.metadata_workers") == 8
        assert "Maximum" in _text(app.query_one("#status", Static))

    _run_app(app, scenario)


def test_ordered_list_editor_adds_reorders_and_applies(tmp_path: Path) -> None:
    service = _service(tmp_path / "config.toml")
    app = SettingsApp(service=service, watch_interval=0)

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        await pilot.press("enter", "enter")
        await _wait_until(lambda: len(app.screen_stack) == 2)
        await pilot.pause(0.05)
        await pilot.press(*"strawberry", "enter")
        await pilot.press(*"vlc", "enter")
        draft = app.screen.query_one("#draft-values", OptionList)
        assert draft.option_count == 2
        draft.highlighted = 1
        assert await pilot.click("#move-value-up")
        assert await pilot.click("#apply-list")
        await _wait_until(
            lambda: service.get("players.preferred") == ("vlc", "strawberry")
        )

    _run_app(app, scenario)


def test_list_cancel_and_stale_draft_never_overwrite(tmp_path: Path) -> None:
    service = _service(tmp_path / "config.toml")
    app = SettingsApp(service=service, watch_interval=0)

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        await pilot.press("enter", "enter")
        await pilot.press(*"discarded", "enter", "escape")
        await _wait_until(lambda: len(app.screen_stack) == 1)
        assert service.get("players.preferred") == ()

        app.query_one("#edit-list", Button).press()
        await _wait_until(lambda: len(app.screen_stack) == 2)
        await pilot.press(*"draft", "enter")
        await asyncio.to_thread(service.set, "players.preferred", ("external",))
        assert await pilot.click("#apply-list")
        await _wait_until(lambda: len(app.screen_stack) == 1)
        assert service.get("players.preferred") == ("external",)
        assert "changed externally" in _text(app.query_one("#status", Static))

    _run_app(app, scenario)


def test_library_path_draft_uses_canonical_validation(tmp_path: Path) -> None:
    service = _service(tmp_path / "config.toml")
    app = SettingsApp(service=service, watch_interval=0)

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        app.action_category(3)
        await pilot.press("enter", "enter")
        await pilot.press(*"relative/path", "enter")
        assert await pilot.click("#apply-list")
        await _wait_until(lambda: "absolute" in _text(app.query_one("#status", Static)))
        assert service.get("library.roots") == ()

    _run_app(app, scenario)


def test_external_valid_invalid_repair_keeps_last_known_good(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    service = _service(path)
    app = SettingsApp(service=service, watch_interval=0)

    async def observe(app: SettingsApp) -> None:
        app._schedule_disk_check()
        await _wait_until(lambda: not app._watch_busy)

    async def scenario(app: SettingsApp, _pilot: Pilot[int]) -> None:
        await observe(app)
        path.write_text(
            "schema_version = 1\n[lyrics.display]\ntranslated = true\n",
            encoding="utf-8",
        )
        await observe(app)
        assert service.get("lyrics.display.translated") is True

        path.write_text(
            "schema_version = 1\n[library]\nmetadata_workers = 99\n",
            encoding="utf-8",
        )
        await observe(app)
        assert service.get("lyrics.display.translated") is True
        assert "rejected" in _text(app.query_one("#status", Static)).casefold()

        path.write_text(
            "schema_version = 1\n[library]\nmetadata_workers = 6\n",
            encoding="utf-8",
        )
        await observe(app)
        assert service.get("library.metadata_workers") == 6
        assert "loaded" in _text(app.query_one("#status", Static)).casefold()

    _run_app(app, scenario)


def test_signature_detects_atomic_replace_symlink_target_and_removal(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.toml"
    target.write_text("schema_version = 1\n", encoding="utf-8")
    link = tmp_path / "config.toml"
    link.symlink_to(target)
    initial = config_signature(link)

    replacement = tmp_path / "replacement.toml"
    replacement.write_text("schema_version = 1\n# new\n", encoding="utf-8")
    os.replace(replacement, target)
    replaced = config_signature(link)
    assert replaced != initial

    target.unlink()
    missing_target = config_signature(link)
    assert missing_target != replaced
    target.write_text("schema_version = 1\n", encoding="utf-8")
    assert config_signature(link) != missing_target
    link.unlink()
    assert config_signature(link).link == ("missing",)


def test_tui_write_preserves_comments_mode_and_symlink(tmp_path: Path) -> None:
    target = tmp_path / "managed.toml"
    target.write_text(
        "# keep this rice comment\n"
        "schema_version = 1\n"
        "[lyrics.display]\n"
        "translated = false # inline\n",
        encoding="utf-8",
    )
    target.chmod(0o640)
    link = tmp_path / "config.toml"
    link.symlink_to(target)
    service = _service(link)
    app = SettingsApp(service=service, watch_interval=0)

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        await pilot.press("2", "down", "down", "enter", "space")
        await _wait_until(lambda: service.get("lyrics.display.translated") is True)
        assert link.is_symlink()
        assert target.stat().st_mode & 0o777 == 0o640
        content = target.read_text(encoding="utf-8")
        assert "# keep this rice comment" in content
        assert "translated = true # inline" in content

    _run_app(app, scenario)


def test_external_atomic_remove_and_recreate_refreshes_app(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    service = _service(path)
    service.set("lyrics.display.translated", True)
    app = SettingsApp(service=service, watch_interval=0)

    async def observe(app: SettingsApp) -> None:
        app._schedule_disk_check()
        await _wait_until(lambda: not app._watch_busy)

    async def scenario(app: SettingsApp, _pilot: Pilot[int]) -> None:
        await observe(app)
        replacement = tmp_path / "replacement.toml"
        replacement.write_text(
            "schema_version = 1\n[library]\nmetadata_workers = 7\n",
            encoding="utf-8",
        )
        os.replace(replacement, path)
        await observe(app)
        assert service.get("lyrics.display.translated") is False
        assert service.get("library.metadata_workers") == 7

        path.unlink()
        await observe(app)
        assert service.get("library.metadata_workers") == 4
        path.write_text(
            "schema_version = 1\n[lyrics.display]\ntranslated = true\n",
            encoding="utf-8",
        )
        await observe(app)
        assert service.get("lyrics.display.translated") is True

    _run_app(app, scenario)


def test_two_tui_instances_converge_with_deterministic_last_valid_write(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    first_service = _service(path)
    second_service = _service(path)
    first = SettingsApp(service=first_service, watch_interval=0)
    second = SettingsApp(service=second_service, watch_interval=0)

    async def run() -> None:
        async with first.run_test(size=(80, 25)), second.run_test(size=(80, 25)):
            await _wait_until(lambda: first._ui_ready and second._ui_ready)
            first._schedule_disk_check()
            second._schedule_disk_check()
            await _wait_until(lambda: not first._watch_busy and not second._watch_busy)

            await asyncio.to_thread(
                first_service.set,
                "lyrics.display.translated",
                True,
            )
            second._schedule_disk_check()
            await _wait_until(lambda: not second._watch_busy)
            assert second_service.get("lyrics.display.translated") is True

            await asyncio.to_thread(
                second_service.set,
                "lyrics.display.translated",
                False,
            )
            first._schedule_disk_check()
            await _wait_until(lambda: not first._watch_busy)
            assert first_service.get("lyrics.display.translated") is False
            assert _service(path).get("lyrics.display.translated") is False

    loop = asyncio.new_event_loop()
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="multi-tui-test")
    loop.set_default_executor(executor)
    try:
        loop.run_until_complete(run())
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        loop.close()

    assert first_service._subscriptions == {}
    assert second_service._subscriptions == {}


def test_resize_help_mouse_and_clean_subscription_shutdown(tmp_path: Path) -> None:
    service = _service(tmp_path / "config.toml")
    app = SettingsApp(service=service, watch_interval=0)

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        assert len(service._subscriptions) == 1
        await pilot.resize_terminal(49, 13)
        assert app.screen.has_class("too-small")
        await pilot.resize_terminal(70, 24)
        assert app.screen.has_class("-narrow")
        assert not app.screen.has_class("too-small")
        await pilot.resize_terminal(120, 40)
        assert await pilot.click("#show-help")
        await _wait_until(lambda: len(app.screen_stack) == 2)
        help_text = _text(app.screen.query_one("#help-content", Static)).casefold()
        assert "navigate settings" in help_text
        assert "canonical validated toml" in help_text
        await pilot.press("escape")
        await _wait_until(lambda: len(app.screen_stack) == 1)

    _run_app(app, scenario)
    assert service._subscriptions == {}


def test_unicode_and_long_values_remain_visible_and_searchable(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    service = _service(path)
    values = (
        "音楽プレイヤー",
        "播放器",
        "음악-플레이어",
        "Проигрыватель",
        "café-e\u0301",
        "player-" + "x" * 120,
    )
    service.set("players.preferred", values)
    app = SettingsApp(service=service, watch_interval=0)

    async def scenario(app: SettingsApp, _pilot: Pilot[int]) -> None:
        rendered = _text(app.query_one("#detail-value", Static))
        assert all(value in rendered for value in values)
        assert app.query_one("#settings-list", OptionList).option_count == 2

    _run_app(app, scenario, size=(55, 22))


def test_cli_dispatches_canonical_settings_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path | None, Path | None]] = []

    def fake_run_settings_tui(
        *, database_path: Path | None = None, config_path: Path | None = None
    ) -> int:
        calls.append((database_path, config_path))
        return 7

    monkeypatch.setattr(
        "lyriflux.presentation.tui.settings_app.run_settings_tui",
        fake_run_settings_tui,
    )
    database = tmp_path / "state.sqlite3"
    config = tmp_path / "config.toml"
    assert cli.main(["settings"], database_path=database, config_path=config) == 7
    assert calls == [(database, config)]


def test_startup_failure_is_controlled_and_quit_returns_failure() -> None:
    def fail() -> CanonicalSettingsService:
        raise RuntimeError("bounded startup failure")

    app = SettingsApp(service_factory=fail, watch_interval=0)

    async def scenario(app: SettingsApp, pilot: Pilot[int]) -> None:
        await _wait_until(lambda: app._startup_failed)
        assert "bounded startup failure" in _text(app.query_one("#status", Static))
        await pilot.press("q")

    _run_app(app, scenario)
    assert app.return_value == 1
