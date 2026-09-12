"""Developer-facing lyric and synchronization details dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from konokashi.application.desktop_state import DesktopViewState
from konokashi.presentation.desktop.lyric_viewport import _reading_layer_name


def _provenance_name(provenance: str | None) -> str:
    if provenance is None:
        return "unavailable"
    return {
        "provider": "Provider",
        "imported": "Imported",
        "local": "Local",
        "user": "Your version",
        "generated": "Generated",
    }.get(provenance, provenance)


def _representation_details(state: DesktopViewState) -> tuple[str, ...]:
    """Describe active layer identity without repeating lyric text."""

    details: list[str] = []
    seen: set[tuple[object, ...]] = set()
    for group in state.active or state.static_lines:
        for layer, text, metadata in (
            (
                _reading_layer_name(group),
                group.romanized_or_transliterated,
                group.reading_metadata,
            ),
            ("Translation", group.translation, group.translation_metadata),
        ):
            if text is None:
                continue
            identity = (
                layer,
                None if metadata is None else metadata.kind,
                None if metadata is None else metadata.provenance,
                None if metadata is None else metadata.approval_state,
                None if metadata is None else metadata.source_name,
                None if metadata is None else metadata.source_version,
                None if metadata is None else metadata.language,
                None if metadata is None else metadata.script,
                None if metadata is None else metadata.uncertainty,
            )
            if identity in seen:
                continue
            seen.add(identity)
            source = "unavailable" if metadata is None else metadata.source_name
            if metadata is not None and metadata.source_version:
                source = f"{source or 'unavailable'} {metadata.source_version}"
            provenance = _provenance_name(
                None if metadata is None else metadata.provenance
            )
            parts = [
                f"provenance {provenance}",
                f"source {source or 'unavailable'}",
            ]
            if metadata is not None:
                for name, value in (
                    ("kind", metadata.kind),
                    ("approval", metadata.approval_state),
                    ("language", metadata.language),
                    ("script", metadata.script),
                    ("uncertainty", metadata.uncertainty),
                ):
                    if value:
                        parts.append(f"{name} {value}")
            details.append(f"- {layer}: {'; '.join(parts)}")
    return tuple(details)


class DiagnosticsDialog(QDialog):
    """Bounded details surface that keeps diagnostics out of the lyric view."""

    def __init__(self, state: DesktopViewState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("KonoKashi details")
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setAccessibleName("KonoKashi diagnostic details")
        lines = [
            f"state: {state.state.value}",
            f"player: {state.player or 'unavailable'}",
            f"playback: {state.playback_state.value}",
            f"lyrics source: {state.lyrics_source or 'unavailable'}",
            f"match confidence: {state.match_confidence or 'unknown'}",
            "sync health: "
            + (state.sync_health.value if state.sync_health else "unavailable"),
            f"document display delay: {state.display_delay_us / 1000:+.0f} ms",
        ]
        representation_details = _representation_details(state)
        lines.extend(
            (
                "",
                "Representation layers:",
                *(
                    representation_details
                    or ("- No active reading or translation layer is available.",)
                ),
            )
        )
        if state.diagnostics:
            lines.extend(("", "Limitations / diagnostics:"))
            lines.extend(f"- {item}" for item in state.diagnostics[:20])
            if len(state.diagnostics) > 20:
                lines.append(f"- … {len(state.diagnostics) - 20} more")
        details.setPlainText("\n".join(lines))
        layout.addWidget(details)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
