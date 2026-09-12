"""Regression coverage for intentional desktop responsibility boundaries."""

from konokashi.presentation.desktop.diagnostics_dialog import (
    DiagnosticsDialog as ExtractedDiagnosticsDialog,
)
from konokashi.presentation.desktop.lyric_editor_dialog import (
    LyricEditorDialog as ExtractedLyricEditorDialog,
)
from konokashi.presentation.desktop.lyric_viewport import (
    LyricBand as ExtractedLyricBand,
)
from konokashi.presentation.desktop.lyric_viewport import (
    LyricTransitionViewport as ExtractedLyricTransitionViewport,
)
from konokashi.presentation.desktop.main_window import (
    DiagnosticsDialog,
    LyricBand,
    LyricTransitionViewport,
)
from konokashi.presentation.desktop.review_alternative_panel import (
    ReviewAlternativePanel,
)
from konokashi.presentation.desktop.review_dialog import LyricEditorDialog


def test_legacy_desktop_imports_reexport_extracted_widget_types() -> None:
    """Existing presentation imports remain source-compatible after extraction."""

    assert LyricBand is ExtractedLyricBand
    assert LyricTransitionViewport is ExtractedLyricTransitionViewport
    assert DiagnosticsDialog is ExtractedDiagnosticsDialog
    assert LyricEditorDialog is ExtractedLyricEditorDialog


def test_widgets_are_owned_by_their_coherent_presentation_modules() -> None:
    assert ExtractedLyricBand.__module__.endswith(".lyric_viewport")
    assert ExtractedLyricTransitionViewport.__module__.endswith(".lyric_viewport")
    assert ExtractedDiagnosticsDialog.__module__.endswith(".diagnostics_dialog")
    assert ExtractedLyricEditorDialog.__module__.endswith(".lyric_editor_dialog")
    assert ReviewAlternativePanel.__module__.endswith(".review_alternative_panel")
