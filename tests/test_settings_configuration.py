"""Stage 11 canonical configuration schema, persistence, and migration tests."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Event, Thread

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from konokashi import cli
from konokashi.application.settings import (
    SETTINGS_BY_KEY,
    SETTINGS_SCHEMA,
    ReloadBehavior,
    SettingCategory,
    SettingOrigin,
    SettingScope,
    SettingsValidationError,
)
from konokashi.application.settings_service import (
    CanonicalSettingsService,
    SettingsFileError,
)
from konokashi.domain.library import LibrarySettings
from konokashi.domain.tracks import PlayerSelectionConfig
from konokashi.infrastructure.configuration.bootstrap import open_settings
from konokashi.infrastructure.configuration.paths import default_config_path
from konokashi.infrastructure.configuration.qt_watcher import QtSettingsWatcher
from konokashi.infrastructure.configuration.toml_file import TomlSettingsFile
from konokashi.infrastructure.storage.bootstrap import open_storage


def _service(path: Path) -> CanonicalSettingsService:
    service = CanonicalSettingsService(TomlSettingsFile(path))
    assert service.initialize().applied
    return service


def test_xdg_path_uses_absolute_override_and_home_fallback(tmp_path: Path) -> None:
    assert (
        default_config_path(
            environment={"XDG_CONFIG_HOME": str(tmp_path)}, home=Path("/unused")
        )
        == tmp_path / "konokashi" / "config.toml"
    )
    assert (
        default_config_path(environment={"XDG_CONFIG_HOME": "relative"}, home=tmp_path)
        == tmp_path / ".config" / "konokashi" / "config.toml"
    )


def test_schema_has_stable_complete_metadata() -> None:
    assert len(SETTINGS_SCHEMA) == len(SETTINGS_BY_KEY) == 9
    assert {item.scope for item in SETTINGS_SCHEMA} == {
        SettingScope.GLOBAL,
        SettingScope.DESKTOP,
    }
    assert {item.reload for item in SETTINGS_SCHEMA} == {
        ReloadBehavior.LIVE,
        ReloadBehavior.NEXT_OPERATION,
    }
    assert all(item.description.endswith(".") for item in SETTINGS_SCHEMA)
    assert all(item.title for item in SETTINGS_SCHEMA)
    assert {item.category for item in SETTINGS_SCHEMA} == set(SettingCategory)
    assert set(SETTINGS_BY_KEY) == {
        "players.preferred",
        "players.ignored",
        "lyrics.display.original",
        "lyrics.display.romanized",
        "lyrics.display.translated",
        "desktop.lyrics.selectable",
        "library.roots",
        "library.automatic_downloads",
        "library.metadata_workers",
    }
    workers = SETTINGS_BY_KEY["library.metadata_workers"]
    assert (workers.minimum, workers.maximum) == (1, 8)


def test_defaults_are_lower_precedence_than_explicit_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "schema_version = 1\n\n[lyrics.display]\ntranslated = true\n",
        encoding="utf-8",
    )
    snapshot = _service(path).current
    assert snapshot.representation_display.show_translated
    assert snapshot.resolved("lyrics.display.translated").origin is (
        SettingOrigin.CONFIG_FILE
    )
    assert snapshot.resolved("lyrics.display.original").origin is SettingOrigin.DEFAULT


def test_missing_empty_and_unicode_config_are_valid(tmp_path: Path) -> None:
    missing = _service(tmp_path / "missing.toml")
    assert missing.current.player_selection == PlayerSelectionConfig()
    empty_path = tmp_path / "empty.toml"
    empty_path.write_text("", encoding="utf-8")
    assert _service(empty_path).current.library == LibrarySettings()
    unicode_path = tmp_path / "unicode.toml"
    unicode_path.write_text(
        'schema_version = 1\n[players]\npreferred = ["音楽プレイヤー"]\n',
        encoding="utf-8",
    )
    assert _service(unicode_path).get_player_selection().preferred_players == (
        "音楽プレイヤー",
    )


def test_malformed_toml_retains_default_last_known_good(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("schema_version = [\n", encoding="utf-8")
    service = CanonicalSettingsService(TomlSettingsFile(path))
    before = service.current
    result = service.reload()
    assert not result.applied
    assert service.current is before
    assert "Invalid TOML" in result.diagnostics[0].message


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("schema_version = 2\n", "schema_version"),
        ("schema_version = 1\n[players]\nprefered = []\n", "Did you mean"),
        ("schema_version = 1\n[library]\nmetadata_workers = 9\n", "at most 8"),
        ('schema_version = 1\n[library]\nroots = ["relative"]\n', "absolute"),
    ],
)
def test_validation_is_actionable(tmp_path: Path, content: str, expected: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    service = CanonicalSettingsService(TomlSettingsFile(path))
    result = service.initialize()
    assert not result.applied
    assert expected in "\n".join(item.render() for item in result.diagnostics)
    with pytest.raises(SettingsValidationError):
        service.validate()


def test_reload_is_atomic_and_notifies_once(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    service = _service(path)
    changes: list[tuple[str, ...]] = []
    subscription = service.subscribe(lambda change: changes.append(change.changed_keys))

    path.write_text(
        "schema_version = 1\n[lyrics.display]\ntranslated = true\n",
        encoding="utf-8",
    )
    accepted = service.reload()
    assert accepted.applied
    assert service.current.representation_display.show_translated
    assert changes == [("lyrics.display.translated",)]

    path.write_text("schema_version = 1\n[library]\nmetadata_workers = 99\n")
    rejected = service.reload()
    assert not rejected.applied
    assert service.current is accepted.snapshot
    assert changes == [("lyrics.display.translated",)]
    path.write_text(
        "schema_version = 1\n[lyrics.display]\ntranslated = false\n",
        encoding="utf-8",
    )
    recovered = service.reload()
    assert recovered.applied
    assert not service.current.representation_display.show_translated
    subscription.close()
    path.write_text(
        "schema_version = 1\n[lyrics.display]\ntranslated = true\n",
        encoding="utf-8",
    )
    service.reload()
    assert len(changes) == 2


def test_edits_preserve_comments_permissions_and_avoid_noop_rewrite(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "# my rice\nschema_version = 1\n\n"
        "[lyrics.display]\ntranslated = false # keep\n",
        encoding="utf-8",
    )
    path.chmod(0o640)
    service = _service(path)
    service.set("lyrics.display.translated", True)
    content = path.read_text(encoding="utf-8")
    assert "# my rice" in content
    assert "translated = true # keep" in content
    assert path.stat().st_mode & 0o777 == 0o640
    modified = path.stat().st_mtime_ns
    service.set("lyrics.display.translated", True)
    assert path.stat().st_mtime_ns == modified


def test_invalid_set_does_not_write_and_reset_preserves_unrelated_content(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "# retained\nschema_version = 1\n"
        '[players]\npreferred = ["vlc"]\nignored = ["web"]\n',
        encoding="utf-8",
    )
    service = _service(path)
    before = path.read_bytes()
    with pytest.raises(SettingsValidationError):
        service.set("library.metadata_workers", 99)
    assert path.read_bytes() == before
    service.reset("players.preferred")
    content = path.read_text(encoding="utf-8")
    assert "# retained" in content
    assert 'ignored = ["web"]' in content
    assert "preferred" not in content


def test_symlink_edit_replaces_target_without_replacing_link(tmp_path: Path) -> None:
    target = tmp_path / "dotfiles" / "konokashi.toml"
    target.parent.mkdir()
    target.write_text("schema_version = 1\n", encoding="utf-8")
    link = tmp_path / "config.toml"
    link.symlink_to(target)

    _service(link).set("desktop.lyrics.selectable", True)

    assert link.is_symlink()
    assert "selectable = true" in target.read_text(encoding="utf-8")


def test_interrupted_atomic_replace_retains_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    original = "schema_version = 1\n# retained\n"
    path.write_text(original, encoding="utf-8")
    service = _service(path)

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(SettingsFileError, match="simulated replace failure"):
        service.set("desktop.lyrics.selectable", True)

    assert path.read_text(encoding="utf-8") == original
    assert tuple(tmp_path.glob(".config.toml.*.tmp")) == ()


def test_concurrent_services_do_not_lose_independent_updates(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    first_read = Event()
    release_first = Event()
    second_done = Event()
    errors: list[BaseException] = []

    class PausingTomlSettingsFile(TomlSettingsFile):
        paused = False

        def _editable_document(self):  # type: ignore[no-untyped-def]
            document = super()._editable_document()
            if not self.paused:
                self.paused = True
                first_read.set()
                if not release_first.wait(2):
                    raise TimeoutError(
                        "concurrent settings test did not release writer"
                    )
            return document

    first = CanonicalSettingsService(PausingTomlSettingsFile(path))
    second = CanonicalSettingsService(TomlSettingsFile(path))
    assert first.initialize().applied
    assert second.initialize().applied

    def update_first() -> None:
        try:
            first.set("lyrics.display.translated", True)
        except BaseException as error:
            errors.append(error)

    def update_second() -> None:
        try:
            second.set("desktop.lyrics.selectable", True)
        except BaseException as error:
            errors.append(error)
        finally:
            second_done.set()

    first_thread = Thread(target=update_first)
    second_thread = Thread(target=update_second)
    first_thread.start()
    assert first_read.wait(1)
    second_thread.start()
    try:
        assert not second_done.wait(0.1)
    finally:
        release_first.set()
        first_thread.join(2)
        second_thread.join(2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert errors == []
    snapshot = _service(path).current
    assert snapshot.representation_display.show_translated
    assert snapshot.desktop_interaction.allow_lyric_selection
    assert stat_mode(tmp_path / ".config.toml.lock") == 0o600


def test_oversized_config_is_rejected_without_parsing(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b"#" * 1_048_577)
    result = CanonicalSettingsService(TomlSettingsFile(path)).initialize()
    assert not result.applied
    assert "safety limit" in result.diagnostics[0].message


def test_legacy_settings_migrate_once_without_overriding_existing_toml(
    tmp_path: Path,
) -> None:
    storage = open_storage(tmp_path / "state.sqlite3")
    storage.settings.put_player_selection(PlayerSelectionConfig(("vlc",), ("web",)))
    storage.library.put_settings(LibrarySettings((str(tmp_path),), True, 2))
    path = tmp_path / "config.toml"
    path.write_text(
        'schema_version = 1\n[players]\npreferred = ["spotify"]\n',
        encoding="utf-8",
    )

    service = open_settings(storage, config_path=path)

    assert service.get_player_selection() == PlayerSelectionConfig(
        ("spotify",), ("web",)
    )
    assert service.get_library() == LibrarySettings((str(tmp_path),), True, 2)
    assert storage.settings.canonical_config_migrated()
    storage.settings.put_player_selection(PlayerSelectionConfig(("changed",), ()))
    assert open_settings(storage, config_path=path).get_player_selection() == (
        PlayerSelectionConfig(("spotify",), ("web",))
    )


def test_invalid_config_does_not_complete_legacy_migration(tmp_path: Path) -> None:
    storage = open_storage(tmp_path / "state.sqlite3")
    storage.settings.put_player_selection(PlayerSelectionConfig(("vlc",), ()))
    path = tmp_path / "config.toml"
    path.write_text("schema_version = 1\n[players]\npreferred = false\n")

    service = open_settings(storage, config_path=path)

    assert service.get_player_selection() == PlayerSelectionConfig(("vlc",), ())
    assert service.diagnostics
    assert not storage.settings.canonical_config_migrated()

    path.write_text("schema_version = 1\n", encoding="utf-8")
    recovered = service.reload()
    assert recovered.applied
    assert recovered.snapshot.player_selection == PlayerSelectionConfig(("vlc",), ())
    assert not storage.settings.canonical_config_migrated()

    restarted = open_settings(storage, config_path=path)
    assert restarted.get_player_selection() == PlayerSelectionConfig(("vlc",), ())
    assert storage.settings.canonical_config_migrated()


def test_config_cli_supports_path_validate_get_set_reset_and_defaults(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "state.sqlite3"
    path = tmp_path / "config.toml"
    keyword = {"database_path": database, "config_path": path}

    assert cli.main(["config", "path"], **keyword) == 0
    assert capsys.readouterr().out.strip() == str(path)
    assert not database.exists()
    assert cli.main(["config", "dump-defaults"], **keyword) == 0
    assert "schema_version = 1" in capsys.readouterr().out
    assert not database.exists()
    assert cli.main(["config", "set", "library.metadata_workers", "7"], **keyword) == 0
    assert "library.metadata_workers = 7" in capsys.readouterr().out
    assert cli.main(["config", "get", "library.metadata_workers"], **keyword) == 0
    assert "origin: config-file" in capsys.readouterr().out
    assert cli.main(["config", "validate"], **keyword) == 0
    assert "Valid KonoKashi configuration" in capsys.readouterr().out
    assert cli.main(["config", "reset", "library.metadata_workers"], **keyword) == 0
    output = capsys.readouterr().out
    assert "library.metadata_workers = 4" in output
    assert "origin: built-in-default" in output


def test_dangling_symlink_is_rejected_without_replacement(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.symlink_to(tmp_path / "missing.toml")
    result = CanonicalSettingsService(TomlSettingsFile(path)).initialize()
    assert not result.applied
    assert path.is_symlink()
    assert "dangling" in result.diagnostics[0].message


def test_qt_watcher_debounces_and_recovers_after_invalid_save(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication(["settings-test"])
    assert isinstance(application, QApplication)
    path = tmp_path / "config.toml"
    path.write_text("schema_version = 1\n", encoding="utf-8")
    service = _service(path)
    results = []
    watcher = QtSettingsWatcher(service, results.append, debounce_ms=20)

    path.write_text(
        "schema_version = 1\n[lyrics.display]\ntranslated = true\n",
        encoding="utf-8",
    )
    QTest.qWait(100)
    assert results and results[-1].applied
    assert service.current.representation_display.show_translated

    before_count = len(results)
    path.write_text("schema_version = 1\n[library]\nmetadata_workers = 99\n")
    watcher._schedule(str(path))
    watcher._schedule(str(path))
    watcher._schedule(str(path))
    QTest.qWait(100)
    assert len(results) == before_count + 1
    assert not results[-1].applied
    assert service.current.representation_display.show_translated

    path.write_text(
        "schema_version = 1\n[lyrics.display]\ntranslated = false\n",
        encoding="utf-8",
    )
    QTest.qWait(100)
    assert results[-1].applied
    assert not service.current.representation_display.show_translated
    watcher.close()


def test_new_config_is_private(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _service(path).set("players.preferred", ("vlc",))
    assert stat_mode(path) == 0o600


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777
