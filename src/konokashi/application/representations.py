"""Stage 5 script routing, line generation, precedence, import, and correction."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from hashlib import sha256

from konokashi.application.ports import (
    LanguageEvidenceProviderPort,
    RepresentationRepositoryPort,
    RomanizationProviderPort,
)
from konokashi.domain.lyrics import (
    ApprovalState,
    ContentProvenance,
    LyricDocument,
    LyricLine,
    RepresentationKind,
)
from konokashi.domain.representations import (
    DocumentLanguageOverride,
    EffectiveRepresentationLine,
    GenerationStatus,
    ImportedRepresentationLine,
    LanguageRoutingEvidence,
    LanguageRoutingStatus,
    RepresentationAvailability,
    RepresentationCandidate,
    RepresentationDecision,
    RepresentationGenerationReport,
    RepresentationImportResult,
    RepresentationLayerStatus,
    RepresentationUncertainty,
    RomanizationProviderResult,
    RomanizationRequest,
    RomanizationRoute,
    ScriptRoutingDecision,
)
from konokashi.domain.scripts import UnicodeScript, analyze_scripts

_JAPANESE_HINTS = {"ja", "jpn", "japanese", "jpan"}
_CHINESE_HINTS = {"zh", "zho", "chi", "chinese"}
_GENERIC_ROUTES = {
    UnicodeScript.CYRILLIC: RomanizationRoute.CYRILLIC,
    UnicodeScript.GREEK: RomanizationRoute.GREEK,
    UnicodeScript.ARABIC: RomanizationRoute.ARABIC,
    UnicodeScript.THAI: RomanizationRoute.THAI,
}
_PROVENANCE_RANK = {
    ContentProvenance.PROVIDER: 0,
    ContentProvenance.IMPORTED: 1,
    ContentProvenance.LOCAL: 1,
    ContentProvenance.USER: 2,
    ContentProvenance.GENERATED: 3,
}


def _hint_tokens(*hints: str | None) -> set[str]:
    tokens: set[str] = set()
    for hint in hints:
        if not hint:
            continue
        lowered = hint.casefold().replace("_", "-")
        tokens.add(lowered)
        tokens.add(lowered.split("-", 1)[0])
    return tokens


def normalize_language_override(language: str) -> str:
    """Validate a user-approved language for genuinely ambiguous Han text."""

    hints = _hint_tokens(language)
    if hints & _CHINESE_HINTS:
        return "zh"
    if hints & _JAPANESE_HINTS:
        return "ja"
    raise ValueError("language override must identify Chinese (zh) or Japanese (ja)")


def document_language_evidence(
    document: LyricDocument,
    classifier: LanguageEvidenceProviderPort | None,
) -> LanguageRoutingEvidence:
    """Resolve bounded aggregate evidence without pretending Han is a language."""

    originals = original_lines(document)
    text = "\n".join(line.text for line in originals)
    analysis = analyze_scripts(text)
    scripts = set(analysis.scripts)
    japanese = {
        UnicodeScript.HAN,
        UnicodeScript.HIRAGANA,
        UnicodeScript.KATAKANA,
        UnicodeScript.LATIN,
    }
    if (
        scripts & {UnicodeScript.HIRAGANA, UnicodeScript.KATAKANA}
        and scripts <= japanese
    ):
        return LanguageRoutingEvidence(
            "ja",
            RepresentationUncertainty.AMBIGUOUS,
            "document-level kana composition supports Japanese routing",
        )
    if UnicodeScript.HAN not in scripts:
        return LanguageRoutingEvidence(
            None,
            RepresentationUncertainty.NONE,
            "document has no ambiguous Han-only lines requiring language evidence",
            LanguageRoutingStatus.UNAVAILABLE,
        )
    disallowed = scripts - {UnicodeScript.HAN, UnicodeScript.LATIN}
    if disallowed:
        return LanguageRoutingEvidence(
            None,
            RepresentationUncertainty.AMBIGUOUS,
            f"mixed document scripts prevent Han language inference ({analysis.label})",
            LanguageRoutingStatus.AMBIGUOUS,
        )
    if classifier is None:
        return LanguageRoutingEvidence(
            None,
            RepresentationUncertainty.AMBIGUOUS,
            "no document-level Chinese language-evidence adapter is configured",
            LanguageRoutingStatus.UNAVAILABLE,
        )
    return classifier.classify_han(text)


def route_romanization(
    text: str,
    *,
    language_hint: str | None = None,
    script_hint: str | None = None,
) -> ScriptRoutingDecision:
    """Choose a conservative route from composition plus explicit metadata hints."""

    analysis = analyze_scripts(text)
    scripts = set(analysis.scripts)
    non_latin = scripts - {UnicodeScript.LATIN}
    language_hints = _hint_tokens(language_hint)
    script_hints = _hint_tokens(script_hint)
    if not non_latin:
        reason = (
            "line contains no letters requiring romanization"
            if not scripts
            else "line is already Latin script"
        )
        return ScriptRoutingDecision(
            analysis,
            None,
            None,
            language_hint,
            RepresentationUncertainty.NONE,
            reason,
            eligible=False,
        )
    japanese_scripts = {
        UnicodeScript.HAN,
        UnicodeScript.HIRAGANA,
        UnicodeScript.KATAKANA,
    }
    if (
        non_latin
        & {
            UnicodeScript.HIRAGANA,
            UnicodeScript.KATAKANA,
        }
        and non_latin <= japanese_scripts
    ):
        return ScriptRoutingDecision(
            analysis,
            RomanizationRoute.JAPANESE,
            RepresentationKind.ROMANIZED,
            "ja",
            (
                RepresentationUncertainty.AMBIGUOUS
                if UnicodeScript.HAN in non_latin
                else RepresentationUncertainty.NONE
            ),
            "kana composition supports Japanese routing",
        )
    if non_latin == {UnicodeScript.HANGUL}:
        return ScriptRoutingDecision(
            analysis,
            RomanizationRoute.KOREAN,
            RepresentationKind.ROMANIZED,
            "ko",
            RepresentationUncertainty.AMBIGUOUS,
            "Hangul composition supports Korean romanization routing",
        )
    if non_latin == {UnicodeScript.HAN}:
        if (language_hints | script_hints) & _JAPANESE_HINTS:
            return ScriptRoutingDecision(
                analysis,
                RomanizationRoute.JAPANESE,
                RepresentationKind.ROMANIZED,
                "ja",
                RepresentationUncertainty.AMBIGUOUS,
                "explicit Japanese metadata disambiguates Han-only text",
            )
        if language_hints & _CHINESE_HINTS:
            return ScriptRoutingDecision(
                analysis,
                RomanizationRoute.CHINESE,
                RepresentationKind.ROMANIZED,
                "zh",
                RepresentationUncertainty.AMBIGUOUS,
                "explicit Chinese metadata disambiguates Han-only text",
            )
        return ScriptRoutingDecision(
            analysis,
            None,
            RepresentationKind.ROMANIZED,
            None,
            RepresentationUncertainty.AMBIGUOUS,
            "Han-only text is ambiguous without a Japanese or Chinese language hint",
        )
    if len(non_latin) == 1:
        script = next(iter(non_latin))
        route = _GENERIC_ROUTES.get(script)
        if route is not None:
            return ScriptRoutingDecision(
                analysis,
                route,
                RepresentationKind.TRANSLITERATED,
                language_hint,
                RepresentationUncertainty.NONE,
                f"{script.value} script routed to a standards-backed ICU transform",
            )
    return ScriptRoutingDecision(
        analysis,
        None,
        RepresentationKind.TRANSLITERATED,
        language_hint,
        RepresentationUncertainty.AMBIGUOUS,
        f"mixed or unsupported script composition ({analysis.label})",
    )


def original_lines(document: LyricDocument) -> tuple[LyricLine, ...]:
    """Return canonical originals; alternate representations never become evidence."""

    representation = next(
        (
            item
            for item in document.representations
            if item.kind is RepresentationKind.ORIGINAL
        ),
        None,
    )
    return () if representation is None else representation.lines


def _require_unique_original_ids(
    originals: tuple[LyricLine, ...],
) -> dict[str, LyricLine]:
    by_id = {line.line_id: line for line in originals}
    if len(by_id) != len(originals):
        raise ValueError("duplicate original lyric line IDs are unsafe")
    return by_id


def _document_candidates(
    document: LyricDocument, kind: RepresentationKind
) -> tuple[RepresentationCandidate, ...]:
    """Project persisted document-native aligned layers into candidate values."""

    source_by_target = {
        alignment.target_line_id: alignment.source_line_id
        for alignment in document.alignments
    }
    timestamp = document.retrieved_at
    candidates: list[RepresentationCandidate] = []
    for representation in document.representations:
        if representation.kind is not kind:
            continue
        for line in representation.lines:
            source_line_id = (
                line.source_line_id
                or source_by_target.get(line.line_id)
                or line.line_id
            )
            candidates.append(
                RepresentationCandidate(
                    f"document:{representation.representation_id}:{line.line_id}",
                    document.document_id,
                    source_line_id,
                    kind,
                    (
                        GenerationStatus.AVAILABLE
                        if line.text.strip()
                        else GenerationStatus.EMPTY
                    ),
                    representation.provenance,
                    representation.generator_name or document.source_name,
                    representation.generator_version,
                    representation.approval_state,
                    RepresentationUncertainty.NONE,
                    timestamp,
                    timestamp,
                    line.text if line.text.strip() else None,
                    representation.language,
                    representation.script,
                    ("representation persisted in the lyric document",),
                )
            )
    return tuple(candidates)


def _candidate_id(
    document_id: str,
    source_line_id: str,
    kind: RepresentationKind,
    provenance: ContentProvenance,
    source_name: str,
    source_version: str | None,
    status: GenerationStatus,
    text: str | None,
) -> str:
    payload = "\0".join(
        (
            document_id,
            source_line_id,
            kind.value,
            provenance.value,
            source_name,
            source_version or "",
            status.value,
            text or "",
        )
    )
    return "representation-" + sha256(payload.encode()).hexdigest()


class RepresentationService:
    """Own generated/imported candidates and durable user precedence decisions."""

    def __init__(
        self,
        provider: RomanizationProviderPort,
        repository: RepresentationRepositoryPort,
        *,
        language_evidence: LanguageEvidenceProviderPort | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._language_evidence = language_evidence
        self._now = now or (lambda: datetime.now(UTC))

    def candidates(self, document_id: str) -> tuple[RepresentationCandidate, ...]:
        """Expose retained evidence for diagnostics without leaking the repository."""

        return self._repository.candidates(document_id)

    def decisions(self, document_id: str) -> tuple[RepresentationDecision, ...]:
        """Expose durable decision state for bounded diagnostics."""

        return self._repository.decisions(document_id)

    def language_override(self, document_id: str) -> DocumentLanguageOverride | None:
        """Expose the exact-document approved routing hint for diagnostics."""

        return self._repository.language_override(document_id)

    def set_language_override(
        self, document: LyricDocument, language: str
    ) -> DocumentLanguageOverride:
        """Approve one document language and invalidate only generated fallback."""

        normalized = normalize_language_override(language)
        existing = self._repository.language_override(document.document_id)
        timestamp = self._now()
        override = DocumentLanguageOverride(
            document.document_id,
            normalized,
            existing.created_at if existing is not None else timestamp,
            timestamp,
        )
        self._repository.put_language_override(override)
        self._repository.delete_generated(document.document_id)
        return override

    def reset_language_override(self, document: LyricDocument) -> bool:
        """Remove one language approval and derived fallback, preserving evidence."""

        removed = self._repository.delete_language_override(document.document_id)
        if removed:
            self._repository.delete_generated(document.document_id)
        return removed

    def _routing_language(
        self, document: LyricDocument, explicit: str | None
    ) -> LanguageRoutingEvidence:
        if explicit:
            return LanguageRoutingEvidence(
                explicit,
                RepresentationUncertainty.AMBIGUOUS,
                "explicit command language hint selected",
                LanguageRoutingStatus.EXPLICIT,
            )
        override = self._repository.language_override(document.document_id)
        if override is not None:
            return LanguageRoutingEvidence(
                override.language,
                RepresentationUncertainty.NONE,
                "user-approved document language override selected",
                LanguageRoutingStatus.EXPLICIT,
            )
        if document.language:
            return LanguageRoutingEvidence(
                document.language,
                RepresentationUncertainty.AMBIGUOUS,
                "provider/document language metadata selected",
                LanguageRoutingStatus.AUTOMATIC,
            )
        return document_language_evidence(document, self._language_evidence)

    def routing_language(self, document: LyricDocument) -> LanguageRoutingEvidence:
        """Explain the effective durable/automatic document routing evidence."""

        return self._routing_language(document, None)

    def generate(
        self,
        document: LyricDocument,
        *,
        language_hint: str | None = None,
        line_ids: Sequence[str] | None = None,
        regenerate: bool = False,
    ) -> RepresentationGenerationReport:
        """Generate independently per original line, preserving every user approval."""

        originals = original_lines(document)
        language_evidence = self._routing_language(document, language_hint)
        by_id = _require_unique_original_ids(originals)
        selected_ids = tuple(by_id) if line_ids is None else tuple(line_ids)
        unknown = [line_id for line_id in selected_ids if line_id not in by_id]
        if unknown:
            raise ValueError(f"unknown original line ID: {unknown[0]}")
        decisions = {
            (item.source_line_id, item.kind): item
            for item in self._repository.decisions(document.document_id)
        }
        existing = self._repository.candidates(document.document_id)
        generated = reused = unavailable = failed = generated_empty = 0
        eligible = candidate_persisted = 0
        diagnostics: list[str] = [
            f"document language routing: {language_evidence.diagnostic}"
        ]
        for line_id in selected_ids:
            line = by_id[line_id]
            routing = route_romanization(
                line.text,
                language_hint=language_evidence.language,
                script_hint=document.script,
            )
            diagnostics.append(f"{line_id}: {routing.diagnostic}")
            if routing.kind is None:
                unavailable += 1
                continue
            if routing.eligible:
                eligible += 1
            decision = decisions.get((line_id, routing.kind))
            if (
                decision is not None
                and decision.approval_state is ApprovalState.APPROVED
            ):
                reused += 1
                diagnostics.append(f"{line_id}: user-approved value preserved")
                continue
            if decision is not None and not regenerate:
                reused += 1
                diagnostics.append(
                    f"{line_id}: persisted {decision.approval_state.value} "
                    "decision preserved"
                )
                continue
            if decision is not None and regenerate:
                self._repository.delete_decision(
                    document.document_id, line_id, routing.kind
                )
            if routing.route is None:
                result = RomanizationProviderResult(
                    (
                        GenerationStatus.FAILED
                        if language_evidence.status is LanguageRoutingStatus.FAILED
                        else GenerationStatus.UNAVAILABLE
                    ),
                    "KonoKashi language routing",
                    "1",
                    routing.kind,
                    uncertainty=routing.uncertainty,
                    diagnostics=(language_evidence.diagnostic, routing.diagnostic),
                )
            else:
                try:
                    result = self._provider.generate(
                        RomanizationRequest(
                            line_id,
                            line.text,
                            routing.route,
                            routing.language,
                        )
                    )
                except Exception as error:
                    result = RomanizationProviderResult(
                        GenerationStatus.FAILED,
                        self._provider.name,
                        "unavailable",
                        routing.kind,
                        diagnostics=(
                            f"romanization adapter failed: {type(error).__name__}",
                        ),
                    )
            if (
                result.kind is not routing.kind
                or (
                    result.status is GenerationStatus.AVAILABLE
                    and (result.text is None or not result.text.strip())
                )
                or (
                    result.status is GenerationStatus.AVAILABLE
                    and result.text is not None
                    and bool(
                        set(analyze_scripts(result.text).scripts)
                        & {
                            UnicodeScript.HAN,
                            UnicodeScript.HIRAGANA,
                            UnicodeScript.KATAKANA,
                            UnicodeScript.HANGUL,
                            UnicodeScript.CYRILLIC,
                            UnicodeScript.GREEK,
                            UnicodeScript.ARABIC,
                            UnicodeScript.THAI,
                        }
                    )
                )
                or not result.provider_name.strip()
                or not result.provider_version.strip()
            ):
                if (
                    result.kind is routing.kind
                    and result.status is GenerationStatus.AVAILABLE
                    and (result.text is None or not result.text.strip())
                    and result.provider_name.strip()
                    and result.provider_version.strip()
                ):
                    result = RomanizationProviderResult(
                        GenerationStatus.EMPTY,
                        result.provider_name,
                        result.provider_version,
                        routing.kind,
                        uncertainty=result.uncertainty,
                        diagnostics=(
                            *result.diagnostics,
                            "romanization engine generated empty text",
                        ),
                    )
                else:
                    result = RomanizationProviderResult(
                        GenerationStatus.FAILED,
                        self._provider.name,
                        "invalid-result",
                        routing.kind,
                        diagnostics=(
                            "romanization adapter returned a malformed result",
                        ),
                    )
            same = next(
                (
                    item
                    for item in existing
                    if item.source_line_id == line_id
                    and item.kind is result.kind
                    and item.provenance is ContentProvenance.GENERATED
                    and item.source_name == result.provider_name
                    and item.source_version == result.provider_version
                    and item.status is result.status
                    and item.text == result.text
                ),
                None,
            )
            if same is not None and not regenerate:
                reused += 1
                continue
            timestamp = self._now()
            candidate = RepresentationCandidate(
                _candidate_id(
                    document.document_id,
                    line_id,
                    result.kind,
                    ContentProvenance.GENERATED,
                    result.provider_name,
                    result.provider_version,
                    result.status,
                    result.text,
                ),
                document.document_id,
                line_id,
                result.kind,
                result.status,
                ContentProvenance.GENERATED,
                result.provider_name,
                result.provider_version,
                ApprovalState.UNREVIEWED,
                result.uncertainty,
                timestamp,
                timestamp,
                result.text,
                result.language,
                result.script,
                result.diagnostics,
            )
            self._repository.replace_generated(
                document.document_id, line_id, result.kind, candidate
            )
            candidate_persisted += 1
            diagnostics.extend(f"{line_id}: {item}" for item in result.diagnostics)
            if result.status is GenerationStatus.AVAILABLE:
                generated += 1
            elif result.status is GenerationStatus.UNAVAILABLE:
                unavailable += 1
            elif result.status is GenerationStatus.EMPTY:
                generated_empty += 1
            else:
                failed += 1
        return RepresentationGenerationReport(
            document.document_id,
            len(originals),
            generated,
            reused,
            unavailable,
            failed,
            tuple(diagnostics),
            eligible,
            generated_empty,
            candidate_persisted,
        )

    def import_aligned(
        self,
        document: LyricDocument,
        *,
        kind: RepresentationKind,
        lines: Sequence[ImportedRepresentationLine],
        provenance: ContentProvenance,
        source_name: str,
        source_version: str | None = None,
        language: str | None = None,
    ) -> RepresentationImportResult:
        """Import only explicit original IDs; unequal/missing lines remain valid."""

        if kind is RepresentationKind.ORIGINAL:
            raise ValueError("import cannot replace the original representation")
        if provenance not in {
            ContentProvenance.PROVIDER,
            ContentProvenance.IMPORTED,
            ContentProvenance.LOCAL,
        }:
            raise ValueError("import provenance must be provider, imported, or local")
        originals = {line.line_id for line in original_lines(document)}
        source_ids = [line.source_line_id for line in lines]
        if len(set(source_ids)) != len(source_ids):
            return RepresentationImportResult(
                0, ("duplicate source line IDs are unsafe",)
            )
        unknown = [line_id for line_id in source_ids if line_id not in originals]
        if unknown:
            return RepresentationImportResult(
                0, (f"unknown original line ID prevents safe import: {unknown[0]}",)
            )
        if any(not line.text.strip() for line in lines):
            return RepresentationImportResult(
                0, ("blank imported representation text",)
            )
        timestamp = self._now()
        candidates = tuple(
            RepresentationCandidate(
                _candidate_id(
                    document.document_id,
                    line.source_line_id,
                    kind,
                    provenance,
                    source_name,
                    source_version,
                    GenerationStatus.AVAILABLE,
                    line.text,
                ),
                document.document_id,
                line.source_line_id,
                kind,
                GenerationStatus.AVAILABLE,
                provenance,
                source_name,
                source_version,
                ApprovalState.UNREVIEWED,
                RepresentationUncertainty.NONE,
                timestamp,
                timestamp,
                line.text,
                language,
                "Latn",
            )
            for line in lines
        )
        self._repository.put_candidates(candidates)
        missing = len(originals) - len(lines)
        diagnostics = (
            ()
            if missing == 0
            else (f"partial import: {missing} original line(s) remain unrepresented",)
        )
        return RepresentationImportResult(len(candidates), diagnostics)

    def effective_lines(
        self, document: LyricDocument, kind: RepresentationKind
    ) -> tuple[EffectiveRepresentationLine, ...]:
        """Apply approved > provider/imported > generated precedence per line ID."""

        if kind is RepresentationKind.ORIGINAL:
            raise ValueError("original lines are already canonical")
        originals = original_lines(document)
        _require_unique_original_ids(originals)
        candidates = (
            *self._repository.candidates(document.document_id),
            *_document_candidates(document, kind),
        )
        decisions = {
            (item.source_line_id, item.kind): item
            for item in self._repository.decisions(document.document_id)
        }
        output: list[EffectiveRepresentationLine] = []
        for original in originals:
            decision = decisions.get((original.line_id, kind))
            if (
                decision is not None
                and decision.approval_state is ApprovalState.APPROVED
                and decision.text is not None
            ):
                based_on = next(
                    (
                        item
                        for item in candidates
                        if item.candidate_id == decision.based_on_candidate_id
                    ),
                    None,
                )
                output.append(
                    EffectiveRepresentationLine(
                        original,
                        kind,
                        decision.text,
                        ContentProvenance.USER,
                        ApprovalState.APPROVED,
                        "user",
                        None,
                        RepresentationUncertainty.NONE,
                        ("user-approved value outranks retained candidates",),
                        original.start_ms,
                        decision.based_on_candidate_id,
                        None if based_on is None else based_on.language,
                        None if based_on is None else based_on.script,
                    )
                )
                continue
            available = [
                item
                for item in candidates
                if item.source_line_id == original.line_id
                and item.kind is kind
                and item.status is GenerationStatus.AVAILABLE
                and item.text is not None
                and not (
                    decision is not None
                    and decision.approval_state is ApprovalState.REJECTED
                    and item.provenance is ContentProvenance.GENERATED
                )
            ]
            available.sort(
                key=lambda item: (
                    _PROVENANCE_RANK[item.provenance],
                    item.source_name.casefold(),
                    item.candidate_id,
                )
            )
            winner = available[0] if available else None
            decision_diagnostic: tuple[str, ...] = ()
            if decision is not None:
                if decision.approval_state is ApprovalState.REJECTED:
                    decision_diagnostic = ("generated value rejected by user",)
                elif decision.approval_state is ApprovalState.UNREVIEWED:
                    decision_diagnostic = ("user draft exists but is not approved",)
            if winner is None:
                unavailable_diagnostics = tuple(
                    diagnostic
                    for item in candidates
                    if item.source_line_id == original.line_id and item.kind is kind
                    for diagnostic in item.diagnostics
                )
                output.append(
                    EffectiveRepresentationLine(
                        original,
                        kind,
                        None,
                        None,
                        decision.approval_state if decision is not None else None,
                        None,
                        None,
                        None,
                        (*decision_diagnostic, *unavailable_diagnostics),
                        original.start_ms,
                    )
                )
                continue
            output.append(
                EffectiveRepresentationLine(
                    original,
                    kind,
                    winner.text,
                    winner.provenance,
                    winner.approval_state,
                    winner.source_name,
                    winner.source_version,
                    winner.uncertainty,
                    (*decision_diagnostic, *winner.diagnostics),
                    original.start_ms,
                    winner.candidate_id,
                    winner.language,
                    winner.script,
                )
            )
        return tuple(output)

    def layer_status(
        self,
        document: LyricDocument,
        kind: RepresentationKind,
        *,
        visible: bool,
    ) -> RepresentationLayerStatus:
        """Describe eligibility through rendering without inventing availability."""

        if kind is RepresentationKind.ORIGINAL:
            raise ValueError("original lyrics are not an alternate representation")
        originals = original_lines(document)
        _require_unique_original_ids(originals)
        effective = self.effective_lines(document, kind)
        candidates = tuple(
            item
            for item in (
                *self._repository.candidates(document.document_id),
                *_document_candidates(document, kind),
            )
            if item.kind is kind
        )
        routing_diagnostics: tuple[str, ...]
        if kind is RepresentationKind.TRANSLATED:
            eligible = len(originals)
            routing_diagnostics = (
                "automatic translation backend is not implemented; translations "
                "come only from provider, local/imported, or user-aligned text",
            )
        else:
            language = self._routing_language(document, None).language
            routed = tuple(
                route_romanization(
                    line.text,
                    language_hint=language,
                    script_hint=document.script,
                )
                for line in originals
            )
            eligible = sum(item.eligible and item.kind is kind for item in routed)
            routing_diagnostics = tuple(
                dict.fromkeys(
                    item.diagnostic
                    for item in routed
                    if item.kind is kind and item.route is None
                )
            )
        selected = tuple(item for item in effective if item.text is not None)
        selected_count = len(selected)
        failed_count = sum(
            item.status is GenerationStatus.FAILED for item in candidates
        )
        if selected_count == len(originals) and originals:
            availability = (
                RepresentationAvailability.AVAILABLE_SHOWN
                if visible
                else RepresentationAvailability.AVAILABLE_HIDDEN
            )
        elif selected_count:
            availability = RepresentationAvailability.PARTIAL
        elif failed_count:
            availability = RepresentationAvailability.FAILED
        else:
            availability = RepresentationAvailability.UNAVAILABLE
        origins = tuple(
            dict.fromkeys(
                f"{item.source_name}"
                + ("" if item.source_version is None else f" {item.source_version}")
                for item in selected
                if item.source_name is not None
            )
        )
        diagnostics = tuple(
            dict.fromkeys(
                (
                    *routing_diagnostics,
                    *(
                        diagnostic
                        for item in effective
                        for diagnostic in item.diagnostics
                    ),
                )
            )
        )
        return RepresentationLayerStatus(
            kind,
            availability,
            len(originals),
            eligible,
            sum(
                item.provenance is ContentProvenance.GENERATED
                and item.status is GenerationStatus.AVAILABLE
                for item in candidates
            ),
            sum(
                item.provenance is ContentProvenance.GENERATED
                and item.status is GenerationStatus.EMPTY
                for item in candidates
            ),
            sum(
                item.provenance is ContentProvenance.GENERATED
                and item.status is GenerationStatus.UNAVAILABLE
                for item in candidates
            ),
            failed_count,
            len(candidates),
            selected_count,
            selected_count if visible else 0,
            origins,
            diagnostics,
        )

    def set_draft(
        self,
        document: LyricDocument,
        source_line_id: str,
        kind: RepresentationKind,
        text: str,
    ) -> RepresentationDecision:
        """Persist an editable user draft without silently approving it."""

        self._require_line(document, source_line_id)
        if kind is RepresentationKind.ORIGINAL or not text.strip():
            raise ValueError("alternate representation draft text must not be blank")
        existing = self._decision(document.document_id, source_line_id, kind)
        timestamp = self._now()
        decision = RepresentationDecision(
            document.document_id,
            source_line_id,
            kind,
            ApprovalState.UNREVIEWED,
            existing.created_at if existing is not None else timestamp,
            timestamp,
            text,
        )
        self._repository.put_decision(decision)
        return decision

    def approve(
        self, document: LyricDocument, source_line_id: str, kind: RepresentationKind
    ) -> RepresentationDecision:
        """Approve a draft or snapshot the current provider/generated winner."""

        self._require_line(document, source_line_id)
        existing = self._decision(document.document_id, source_line_id, kind)
        text = existing.text if existing is not None else None
        based_on = existing.based_on_candidate_id if existing is not None else None
        if not text:
            effective = next(
                item
                for item in self.effective_lines(document, kind)
                if item.original_line.line_id == source_line_id
            )
            text = effective.text
            based_on = effective.candidate_id
        if not text:
            raise ValueError("no representation value is available to approve")
        timestamp = self._now()
        decision = RepresentationDecision(
            document.document_id,
            source_line_id,
            kind,
            ApprovalState.APPROVED,
            existing.created_at if existing is not None else timestamp,
            timestamp,
            text,
            based_on,
        )
        self._repository.put_decision(decision)
        return decision

    def reject_generated(
        self, document: LyricDocument, source_line_id: str, kind: RepresentationKind
    ) -> RepresentationDecision:
        """Persist rejection of generated fallback without suppressing provider data."""

        self._require_line(document, source_line_id)
        generated = next(
            (
                item
                for item in self._repository.candidates(document.document_id)
                if item.source_line_id == source_line_id
                and item.kind is kind
                and item.provenance is ContentProvenance.GENERATED
                and item.status is GenerationStatus.AVAILABLE
            ),
            None,
        )
        if generated is None:
            raise ValueError("no generated representation is available to reject")
        existing = self._decision(document.document_id, source_line_id, kind)
        timestamp = self._now()
        decision = RepresentationDecision(
            document.document_id,
            source_line_id,
            kind,
            ApprovalState.REJECTED,
            existing.created_at if existing is not None else timestamp,
            timestamp,
            None,
            generated.candidate_id,
        )
        self._repository.put_decision(decision)
        return decision

    def reset(
        self, document: LyricDocument, source_line_id: str, kind: RepresentationKind
    ) -> bool:
        """Remove only the selected decision so retained candidates win again."""

        self._require_line(document, source_line_id)
        return self._repository.delete_decision(
            document.document_id, source_line_id, kind
        )

    def _decision(
        self, document_id: str, source_line_id: str, kind: RepresentationKind
    ) -> RepresentationDecision | None:
        return next(
            (
                item
                for item in self._repository.decisions(document_id)
                if item.source_line_id == source_line_id and item.kind is kind
            ),
            None,
        )

    @staticmethod
    def _require_line(document: LyricDocument, source_line_id: str) -> None:
        if source_line_id not in {line.line_id for line in original_lines(document)}:
            raise ValueError(f"unknown original line ID: {source_line_id}")
