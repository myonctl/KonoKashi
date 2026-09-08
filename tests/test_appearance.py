"""Stage 16 semantic appearance model and desktop adapter regressions."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from konokashi.application.appearance import (
    APPEARANCE_PRESETS,
    AppearancePreset,
    TextAlignment,
    normalize_color,
)
from konokashi.application.desktop_state import (
    DesktopLyricGroup,
    DesktopLyricsState,
    DesktopViewState,
)
from konokashi.application.settings import (
    SETTINGS_BY_KEY,
    SettingsValidationError,
    default_settings_snapshot,
    validate_settings_values,
)
from konokashi.application.settings_service import CanonicalSettingsService
from konokashi.domain.synchronization import PlaybackState
from konokashi.infrastructure.configuration.toml_file import TomlSettingsFile
from konokashi.presentation.desktop.main_window import MainWindow


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    application = QApplication.instance() or QApplication(["appearance-test"])
    assert isinstance(application, QApplication)
    return application


def _snapshot(**values: object):  # type: ignore[no-untyped-def]
    return validate_settings_values(values, explicit_keys=frozenset(values))


def _state() -> DesktopViewState:
    groups = tuple(
        DesktopLyricGroup(
            f"line-{index}",
            f"原文 {index}",
            f"Romanized {index}",
            f"Translation {index}",
        )
        for index in range(7)
    )
    return DesktopViewState(
        DesktopLyricsState.TIMED,
        "Synchronized lyrics",
        title="Unicode 曲名",
        artists=("Исполнитель",),
        player="player",
        playback_state=PlaybackState.PLAYING,
        progress_fraction=0.5,
        position_us=50_000_000,
        duration_us=100_000_000,
        previous=groups[:3],
        active=(groups[3],),
        next=groups[4:],
        lyrics_source="LRCLIB",
    )


def test_default_profile_is_typed_complete_and_frontend_neutral() -> None:
    appearance = default_settings_snapshot().appearance

    assert appearance.preset is AppearancePreset.DEFAULT
    assert appearance.original.size == 22
    assert appearance.romanization.size == 16
    assert appearance.translation.size == 15
    assert appearance.colors.accent == "#39B9C7"
    assert appearance.lyric_alignment is TextAlignment.CENTER
    assert appearance.context.previous == appearance.context.following == 2
    assert len(SETTINGS_BY_KEY) == 75


@pytest.mark.parametrize(
    ("value", "normalized"),
    (("#00aaff", "#00AAFF"), ("#1020307f", "#1020307F")),
)
def test_color_normalization(value: str, normalized: str) -> None:
    assert normalize_color(value) == normalized
    snapshot = _snapshot(**{"appearance.colors.active_lyric": value})
    assert snapshot.get("appearance.colors.active_lyric") == normalized


@pytest.mark.parametrize(
    "value",
    ("cyan", "#fff", "#12345G", "#123456789", "123456", ""),
)
def test_malformed_colors_are_rejected(value: str) -> None:
    with pytest.raises(SettingsValidationError, match="RRGGBB"):
        _snapshot(**{"appearance.colors.background": value})


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("appearance.typography.original.size", 5),
        ("appearance.typography.original.size", 97),
        ("appearance.spacing.outer_margin", -1),
        ("appearance.spacing.maximum_lyric_width", 10_000),
        ("appearance.context.previous", 9),
        ("appearance.opacity.background", 101),
        ("appearance.motion.transition_ms", -1),
        ("appearance.alignment.lyrics", "diagonal"),
        ("appearance.preset", "remote-code"),
    ),
)
def test_unsafe_appearance_values_are_rejected(key: str, value: object) -> None:
    with pytest.raises(SettingsValidationError):
        _snapshot(**{key: value})


def test_at_least_one_lyric_representation_must_remain_visible() -> None:
    values = {
        "lyrics.display.original": False,
        "lyrics.display.romanized": False,
        "lyrics.display.translated": False,
    }
    with pytest.raises(SettingsValidationError, match="At least one"):
        _snapshot(**values)


def test_unicode_and_unavailable_font_names_are_preserved() -> None:
    family = "存在しない書体 Ω Ж"
    snapshot = _snapshot(
        **{
            "appearance.typography.original.family": family,
            "appearance.typography.romanization.family": "Noto Sans CJK JP",
        }
    )

    assert snapshot.appearance.original.family == family
    assert snapshot.appearance.romanization.family == "Noto Sans CJK JP"


@pytest.mark.parametrize("preset", tuple(AppearancePreset))
def test_presets_are_declarative_and_resolve_through_the_same_model(
    preset: AppearancePreset,
) -> None:
    values = {"appearance.preset": preset.value}
    appearance = _snapshot(**values).appearance

    assert appearance.preset is preset
    assert all(not callable(value) for value in APPEARANCE_PRESETS[preset].values())
    if preset is AppearancePreset.CURRENT_LINE:
        assert appearance.context.previous == appearance.context.following == 0
        assert not appearance.visibility.inactive_context
    if preset is AppearancePreset.LARGE_DISPLAY:
        assert appearance.original.size == 44


def test_explicit_customization_overrides_selected_preset() -> None:
    appearance = _snapshot(
        **{
            "appearance.preset": "large-display",
            "appearance.typography.original.size": 51,
            "appearance.alignment.lyrics": "right",
        }
    ).appearance

    assert appearance.preset is AppearancePreset.LARGE_DISPLAY
    assert appearance.original.size == 51
    assert appearance.romanization.size == 28
    assert appearance.lyric_alignment is TextAlignment.RIGHT


def test_reduced_motion_and_disabled_scrolling_resolve_to_zero_duration() -> None:
    reduced = _snapshot(
        **{
            "appearance.motion.reduced": True,
            "appearance.motion.transition_ms": 900,
            "appearance.motion.emphasis_transition_ms": 700,
        }
    ).appearance.motion
    no_scroll = _snapshot(
        **{
            "appearance.motion.smooth_scrolling": False,
            "appearance.motion.transition_ms": 900,
        }
    ).appearance.motion

    assert reduced.effective_transition_ms == 0
    assert reduced.effective_emphasis_transition_ms == 0
    assert no_scroll.effective_transition_ms == 0


def test_toml_round_trip_preserves_comments_and_normalizes_colors(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text("schema_version = 1\n# keep me\n", encoding="utf-8")
    service = CanonicalSettingsService(TomlSettingsFile(path))
    assert service.initialize().applied

    service.set_many(
        {
            "appearance.typography.original.family": "書体 Ω",
            "appearance.colors.active_lyric": "#aabbccdd",
            "appearance.spacing.outer_margin": 7,
        }
    )
    reloaded = CanonicalSettingsService(TomlSettingsFile(path))
    assert reloaded.initialize().applied

    assert "# keep me" in path.read_text(encoding="utf-8")
    assert reloaded.current.appearance.original.family == "書体 Ω"
    assert reloaded.current.appearance.colors.active_lyric == "#AABBCCDD"
    assert reloaded.current.appearance.spacing.outer_margin == 7


def test_reset_many_restores_the_complete_appearance_profile_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = TomlSettingsFile(tmp_path / "config.toml")
    service = CanonicalSettingsService(config)
    assert service.initialize().applied
    service.set_many(
        {
            "appearance.preset": "compact",
            "appearance.colors.background": "#010203",
            "lyrics.display.original": False,
            "lyrics.display.translated": True,
        }
    )
    keys = tuple(
        key
        for key in SETTINGS_BY_KEY
        if key.startswith("appearance.") or key.startswith("lyrics.display.")
    )
    writes = 0
    original_write = config._write

    def counted_write(content: str) -> None:
        nonlocal writes
        writes += 1
        original_write(content)

    monkeypatch.setattr(config, "_write", counted_write)

    service.reset_many(keys)

    assert writes == 1
    assert service.current.appearance == default_settings_snapshot().appearance


def test_external_appearance_reload_is_atomic_and_last_known_good(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    service = CanonicalSettingsService(TomlSettingsFile(path))
    assert service.initialize().applied
    path.write_text(
        'schema_version = 1\n[appearance.colors]\nactive_lyric = "#ABCDEF"\n',
        encoding="utf-8",
    )
    assert service.reload().applied
    assert service.current.appearance.colors.active_lyric == "#ABCDEF"

    path.write_text(
        'schema_version = 1\n[appearance.colors]\nactive_lyric = "transparent"\n',
        encoding="utf-8",
    )
    rejected = service.reload()

    assert not rejected.applied
    assert rejected.snapshot.appearance.colors.active_lyric == "#ABCDEF"
    assert service.current.appearance.colors.active_lyric == "#ABCDEF"


def test_desktop_applies_fonts_colors_alignment_visibility_and_context_live(
    qt_app: QApplication,
) -> None:
    values = {
        "appearance.typography.original.family": "Uninstalled Stage 16 Font",
        "appearance.typography.original.size": 31,
        "appearance.typography.original.weight": 700,
        "appearance.typography.original.italic": True,
        "appearance.colors.active_lyric": "#FF1122",
        "appearance.colors.background": "#010203",
        "appearance.alignment.lyrics": "right",
        "appearance.alignment.metadata": "center",
        "appearance.visibility.source": False,
        "appearance.visibility.progress": False,
        "appearance.visibility.chrome": False,
        "appearance.context.previous": 1,
        "appearance.context.following": 2,
    }
    window = MainWindow(appearance=_snapshot(**values).appearance)
    window.render_state(_state())
    window.show()
    qt_app.processEvents()

    original = window.active_band._group_widgets[0].original
    assert original.font().family() == "Uninstalled Stage 16 Font"
    assert original.font().pointSizeF() > 31
    assert original.font().weight() == QFont.Weight.Bold
    assert original.font().italic()
    assert original.palette().color(QPalette.ColorRole.WindowText) == QColor("#FF1122")
    assert original.alignment() & Qt.AlignmentFlag.AlignRight
    assert window.title_label.alignment() & Qt.AlignmentFlag.AlignHCenter
    assert window.palette().color(QPalette.ColorRole.Window) == QColor("#010203")
    assert len(window.previous_band._groups) == 1
    assert len(window.next_band._groups) == 2
    assert not window.source_label.isVisible()
    assert not window.progress.isVisible()
    assert not window._actions_widget.isVisible()
    assert window.settings_action.isEnabled()
    window.close()


def test_current_line_preset_hides_context_without_losing_stable_identity(
    qt_app: QApplication,
) -> None:
    window = MainWindow(
        appearance=_snapshot(**{"appearance.preset": "current-line"}).appearance
    )
    state = _state()
    window.render_state(state)
    window.show()
    qt_app.processEvents()

    assert window.previous_band._groups == ()
    assert window.next_band._groups == ()
    assert window.active_band._groups[0].line_id == state.active[0].line_id
    assert window.active_band.isVisible()
    window.close()


def test_timed_lyrics_receive_the_available_horizontal_space(
    qt_app: QApplication,
) -> None:
    """Guard against equal-stretch side spacers collapsing the lyric column."""

    window = MainWindow()
    window.resize(1_100, 760)
    window.render_state(_state())
    window.show()
    qt_app.processEvents()

    assert window._lyric_column.width() >= 900
    assert window.active_band._group_widgets[0].original.width() >= 880
    window.close()


def test_track_change_preserves_styling_and_multilingual_text(
    qt_app: QApplication,
) -> None:
    appearance = _snapshot(
        **{
            "appearance.colors.active_lyric": "#A1B2C3",
            "appearance.typography.original.size": 29,
        }
    ).appearance
    window = MainWindow(appearance=appearance)
    window.render_state(_state())
    multilingual = DesktopLyricGroup(
        "multilingual-line",
        "日本語 中文 한국어 Кириллица café",
        "nihongo zhōngwén hangugeo kirillitsa café",
        "Japanese, Chinese, Korean, Cyrillic, accented Latin",
    )
    window.render_state(
        replace(
            _state(),
            title="次の曲",
            previous=(),
            active=(multilingual,),
            next=(),
        )
    )
    window.show()
    qt_app.processEvents()

    widget = window.active_band._group_widgets[0]
    assert window.appearance_profile is appearance
    assert widget.original.text() == multilingual.original
    assert widget.romanized.text() == multilingual.romanized_or_transliterated
    assert widget.translation.text() == multilingual.translation
    assert widget.original.palette().color(QPalette.ColorRole.WindowText) == QColor(
        "#A1B2C3"
    )
    assert widget.original.font().pointSizeF() > 29
    window.close()


def test_playback_ticks_do_not_reconstruct_appearance(
    qt_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()
    state = _state()
    window.render_state(state)
    calls = 0

    def unexpected(*_args: object) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(window.active_band, "apply_appearance", unexpected)
    for index in range(100):
        window.update_playback(
            replace(
                state,
                position_us=index * 100_000,
                progress_fraction=index / 100,
            )
        )

    assert calls == 0
    window.close()
