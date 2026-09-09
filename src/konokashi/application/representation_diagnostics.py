"""Bounded Stage 5 diagnostics for aligned multilingual lyric layers."""

from __future__ import annotations

from collections.abc import Sequence

from konokashi.application.representations import RepresentationService, original_lines
from konokashi.domain.lyrics import (
    ApprovalState,
    ContentProvenance,
    LyricDocument,
    RepresentationKind,
)
from konokashi.domain.representations import (
    EffectiveRepresentationLine,
    RepresentationDisplaySettings,
    RepresentationGenerationReport,
)
from konokashi.domain.scripts import analyze_scripts

DEFAULT_REPRESENTATION_PREVIEW_LINES = 3


def _status(line: EffectiveRepresentationLine) -> str:
    if line.approval_state is ApprovalState.APPROVED:
        return "approved"
    if line.approval_state is ApprovalState.REJECTED and line.text is None:
        return "rejected"
    if line.provenance is ContentProvenance.PROVIDER:
        return "provider"
    if line.provenance in {ContentProvenance.IMPORTED, ContentProvenance.LOCAL}:
        return "imported"
    if line.provenance is ContentProvenance.GENERATED:
        suffix = (
            " — ambiguous"
            if line.uncertainty and line.uncertainty.value == "ambiguous"
            else ""
        )
        return f"generated{suffix}"
    return "unavailable"


def _line_map(
    lines: Sequence[EffectiveRepresentationLine],
) -> dict[str, EffectiveRepresentationLine]:
    return {line.original_line.line_id: line for line in lines}


def render_representations(
    document: LyricDocument,
    service: RepresentationService,
    settings: RepresentationDisplaySettings,
    *,
    full: bool = False,
    report: RepresentationGenerationReport | None = None,
) -> str:
    """Show source/status/alignment and a bounded stacked preview."""

    originals = original_lines(document)
    romanized = service.effective_lines(document, RepresentationKind.ROMANIZED)
    transliterated = service.effective_lines(
        document, RepresentationKind.TRANSLITERATED
    )
    translated = service.effective_lines(document, RepresentationKind.TRANSLATED)
    candidates = service.candidates(document.document_id)
    decisions = service.decisions(document.document_id)
    language_override = service.language_override(document.document_id)
    routing = service.routing_language(document)
    layer_statuses = tuple(
        service.layer_status(document, kind, visible=visible)
        for kind, visible in (
            (RepresentationKind.ROMANIZED, settings.show_romanized),
            (RepresentationKind.TRANSLITERATED, settings.show_romanized),
            (RepresentationKind.TRANSLATED, settings.show_translated),
        )
    )
    scripts = sorted({analyze_scripts(line.text).label for line in originals})
    effective_pronunciation = tuple(
        line
        for pair in zip(romanized, transliterated, strict=True)
        for line in (pair[0] if pair[0].text is not None else pair[1],)
    )
    pronunciation_aligned = sum(
        line.text is not None for line in effective_pronunciation
    )
    pronunciation_missing = len(effective_pronunciation) - pronunciation_aligned
    output = [
        f"lyric document: {document.document_id}",
        f"lyrics source: {document.source_name}",
        f"original scripts: {', '.join(scripts) if scripts else 'none'}",
        "display layers: "
        f"original={'on' if settings.show_original else 'off'}, "
        f"romanized={'on' if settings.show_romanized else 'off'}, "
        f"translated={'on' if settings.show_translated else 'off'}",
        f"line count: original {len(originals)}",
        f"representation candidates: {len(candidates)}",
        f"user decisions: {len(decisions)}",
        "user-approved language: "
        + ("none" if language_override is None else language_override.language),
        f"routing state: {routing.status.value}",
        "effective routing language: "
        + (routing.status.value if routing.language is None else routing.language),
        f"routing evidence: {routing.diagnostic}",
        f"romanized/transliterated aligned: {pronunciation_aligned}",
        f"romanized/transliterated missing: {pronunciation_missing}",
        f"translated aligned: {sum(line.text is not None for line in translated)}",
        *(
            (
                f"{status.kind.value} state: {status.availability.value}; "
                f"eligible {status.eligible}/{status.original_lines}; "
                f"persisted {status.candidate_persisted}; "
                f"selected {status.candidate_selected}; "
                f"rendered {status.candidate_rendered}; "
                f"origins {', '.join(status.origins) or 'none'}"
            )
            for status in layer_statuses
        ),
    ]
    if report is not None:
        output.extend(
            (
                "generation: "
                f"generated {report.generated}, reused {report.reused}, "
                f"unavailable {report.unavailable}, failed {report.failed}",
            )
        )
    shown = originals if full else originals[:DEFAULT_REPRESENTATION_PREVIEW_LINES]
    romanized_by_id = _line_map(romanized)
    transliterated_by_id = _line_map(transliterated)
    translated_by_id = _line_map(translated)
    output.append("representation preview:" if not full else "representations:")
    for original in shown:
        timing = ""
        if original.start_ms is not None:
            minutes, remainder = divmod(original.start_ms, 60_000)
            seconds, milliseconds = divmod(remainder, 1000)
            timing = f"[{minutes:02d}:{seconds:02d}.{milliseconds:03d}] "
        if settings.show_original:
            output.append(f"  original [{original.line_id}]: {timing}{original.text}")
        romanized_line = romanized_by_id[original.line_id]
        transliterated_line = transliterated_by_id[original.line_id]
        pronunciation = romanized_line
        if pronunciation.text is None and (
            transliterated_line.text is not None
            or transliterated_line.diagnostics
            or transliterated_line.approval_state is ApprovalState.REJECTED
        ):
            pronunciation = transliterated_line
        if settings.show_romanized and pronunciation.text is not None:
            output.append(
                f"  {pronunciation.kind.value} [{_status(pronunciation)}]: "
                f"{pronunciation.text}"
            )
            if pronunciation.source_name:
                version = (
                    ""
                    if pronunciation.source_version is None
                    else f" {pronunciation.source_version}"
                )
                output.append(f"    source: {pronunciation.source_name}{version}")
        elif (
            settings.show_romanized
            and pronunciation.approval_state is ApprovalState.REJECTED
        ):
            output.append("  romanized/transliterated [rejected]: unavailable")
        elif settings.show_romanized and pronunciation.diagnostics:
            output.append(
                "  romanized/transliterated [unavailable]: "
                f"{pronunciation.diagnostics[0]}"
            )
        translation = translated_by_id[original.line_id]
        if settings.show_translated and translation.text is not None:
            output.append(f"  translated [{_status(translation)}]: {translation.text}")
        elif settings.show_translated:
            output.append("  translated [unavailable]: no aligned translation")
    if not full and len(originals) > len(shown):
        output.append(f"  ... {len(originals) - len(shown)} more lines; use --full")
    if report is not None and report.diagnostics:
        output.append("generation diagnostics:")
        output.extend(f"  - {item}" for item in report.diagnostics[:12])
        if len(report.diagnostics) > 12:
            output.append(f"  - ... {len(report.diagnostics) - 12} more diagnostics")
    return "\n".join(output)
