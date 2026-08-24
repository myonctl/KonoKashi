"""Stage 5 generation, alignment, precedence, correction, and persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lyriflux.application.representation_diagnostics import render_representations
from lyriflux.application.representations import RepresentationService
from lyriflux.application.settings import DesktopInteractionSettings
from lyriflux.domain.lyrics import (
    ApprovalState,
    ContentProvenance,
    LyricDocument,
    LyricDocumentKind,
    LyricLine,
    LyricRepresentation,
    RepresentationKind,
    TimingProvenance,
)
from lyriflux.domain.representations import (
    GenerationStatus,
    ImportedRepresentationLine,
    RepresentationDisplaySettings,
    RepresentationUncertainty,
    RomanizationProviderResult,
    RomanizationRequest,
)
from lyriflux.infrastructure.romanization.offline import (
    IcuHanLanguageEvidenceAdapter,
    OfflineRomanizationProvider,
)
from lyriflux.infrastructure.storage.bootstrap import open_storage

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


def _document(
    *, language: str | None = "ja", texts: tuple[str, ...] = ("同じ行", "同じ行")
) -> LyricDocument:
    lines = tuple(
        LyricLine(
            f"line-{index + 1:04d}",
            text,
            index * 5_000,
            timing_provenance=TimingProvenance.PROVIDER,
        )
        for index, text in enumerate(texts)
    )
    return LyricDocument(
        "stage5-document",
        LyricDocumentKind.SYNCED,
        "fixture",
        "\n".join(texts),
        "checksum",
        ApprovalState.UNREVIEWED,
        NOW,
        representations=(
            LyricRepresentation(
                "original",
                RepresentationKind.ORIGINAL,
                ContentProvenance.PROVIDER,
                ApprovalState.UNREVIEWED,
                lines,
                language=language,
            ),
        ),
        language=language,
    )


class _FakeRomanizer:
    name = "fake-offline"

    def __init__(self, *, fail_line: str | None = None, version: str = "1") -> None:
        self.fail_line = fail_line
        self.version = version
        self.calls: list[RomanizationRequest] = []

    def generate(self, request: RomanizationRequest) -> RomanizationProviderResult:
        self.calls.append(request)
        if request.original_line_id == self.fail_line:
            return RomanizationProviderResult(
                GenerationStatus.FAILED,
                "fake-engine",
                self.version,
                RepresentationKind.ROMANIZED,
                diagnostics=("controlled one-line failure",),
            )
        return RomanizationProviderResult(
            GenerationStatus.AVAILABLE,
            "fake-engine",
            self.version,
            RepresentationKind.ROMANIZED,
            text=f"romaji {request.original_line_id}",
            language="ja-Latn",
            uncertainty=RepresentationUncertainty.AMBIGUOUS,
        )


class _BrokenRomanizer:
    name = "broken"

    def generate(self, _request: RomanizationRequest) -> RomanizationProviderResult:
        raise RuntimeError("adapter unavailable")


class _MalformedRomanizer:
    name = "malformed"

    def generate(self, _request: RomanizationRequest) -> RomanizationProviderResult:
        return RomanizationProviderResult(
            GenerationStatus.AVAILABLE,
            "malformed",
            "1",
            RepresentationKind.ORIGINAL,
            text="still original 君",
        )


def _service(
    path: Path, document: LyricDocument, provider: _FakeRomanizer
) -> RepresentationService:
    storage = open_storage(path)
    storage.lyrics.put(document)
    return RepresentationService(provider, storage.representations, now=lambda: NOW)


def test_generate_round_trip_keeps_repeated_lines_distinct_and_inherits_timing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "generated.sqlite3"
    document = _document()
    service = _service(path, document, _FakeRomanizer())

    report = service.generate(document)
    restarted = RepresentationService(
        _FakeRomanizer(), open_storage(path).representations, now=lambda: NOW
    )
    effective = restarted.effective_lines(document, RepresentationKind.ROMANIZED)

    assert report.generated == 2
    assert [line.original_line.line_id for line in effective] == [
        "line-0001",
        "line-0002",
    ]
    assert [line.inherited_start_ms for line in effective] == [0, 5_000]
    assert effective[0].text != effective[1].text
    assert all(line.provenance is ContentProvenance.GENERATED for line in effective)


def test_user_draft_approval_restart_regeneration_protection_and_reset(
    tmp_path: Path,
) -> None:
    path = tmp_path / "approval.sqlite3"
    document = _document(texts=("今日も君を想ってる",))
    service = _service(path, document, _FakeRomanizer(version="1"))
    service.generate(document)
    service.set_draft(
        document, "line-0001", RepresentationKind.ROMANIZED, "Kyō mo kimi o omotteru"
    )
    service.approve(document, "line-0001", RepresentationKind.ROMANIZED)

    restarted = RepresentationService(
        _FakeRomanizer(version="2"), open_storage(path).representations, now=lambda: NOW
    )
    report = restarted.generate(document, regenerate=True)
    approved = restarted.effective_lines(document, RepresentationKind.ROMANIZED)[0]

    assert report.reused == 1
    assert approved.text == "Kyō mo kimi o omotteru"
    assert approved.approval_state is ApprovalState.APPROVED
    assert restarted.reset(document, "line-0001", RepresentationKind.ROMANIZED)
    fallback = restarted.effective_lines(document, RepresentationKind.ROMANIZED)[0]
    assert fallback.text == "romaji line-0001"
    assert fallback.provenance is ContentProvenance.GENERATED


def test_rejection_persists_and_explicit_regeneration_clears_it(tmp_path: Path) -> None:
    path = tmp_path / "rejection.sqlite3"
    document = _document(texts=("君の声",))
    service = _service(path, document, _FakeRomanizer())
    service.generate(document)
    service.reject_generated(document, "line-0001", RepresentationKind.ROMANIZED)

    restarted = RepresentationService(
        _FakeRomanizer(), open_storage(path).representations, now=lambda: NOW
    )
    rejected = restarted.effective_lines(document, RepresentationKind.ROMANIZED)[0]
    preserved = restarted.generate(document)
    regenerated = restarted.generate(document, regenerate=True)

    assert rejected.text is None
    assert rejected.approval_state is ApprovalState.REJECTED
    assert preserved.reused == 1
    assert regenerated.generated == 1
    assert restarted.effective_lines(document, RepresentationKind.ROMANIZED)[0].text


def test_provider_import_outranks_generated_and_user_approval_outranks_provider(
    tmp_path: Path,
) -> None:
    path = tmp_path / "precedence.sqlite3"
    document = _document(texts=("君の声", "次の行"))
    service = _service(path, document, _FakeRomanizer())
    service.generate(document)
    imported = service.import_aligned(
        document,
        kind=RepresentationKind.ROMANIZED,
        lines=(ImportedRepresentationLine("line-0001", "provider kimi no koe"),),
        provenance=ContentProvenance.PROVIDER,
        source_name="fixture-provider",
    )

    effective = service.effective_lines(document, RepresentationKind.ROMANIZED)
    assert imported.imported == 1
    assert "partial import" in imported.diagnostics[0]
    assert effective[0].text == "provider kimi no koe"
    assert effective[0].provenance is ContentProvenance.PROVIDER
    assert effective[1].provenance is ContentProvenance.GENERATED

    service.set_draft(
        document, "line-0001", RepresentationKind.ROMANIZED, "approved Kimi no koe"
    )
    service.approve(document, "line-0001", RepresentationKind.ROMANIZED)
    assert (
        service.effective_lines(document, RepresentationKind.ROMANIZED)[0].provenance
        is ContentProvenance.USER
    )


def test_unsafe_import_never_blindly_zips_or_mutates_candidates(tmp_path: Path) -> None:
    path = tmp_path / "unsafe-import.sqlite3"
    document = _document()
    service = _service(path, document, _FakeRomanizer())

    unknown = service.import_aligned(
        document,
        kind=RepresentationKind.ROMANIZED,
        lines=(ImportedRepresentationLine("missing", "unsafe"),),
        provenance=ContentProvenance.PROVIDER,
        source_name="provider",
    )
    duplicate = service.import_aligned(
        document,
        kind=RepresentationKind.ROMANIZED,
        lines=(
            ImportedRepresentationLine("line-0001", "one"),
            ImportedRepresentationLine("line-0001", "two"),
        ),
        provenance=ContentProvenance.PROVIDER,
        source_name="provider",
    )

    assert unknown.imported == duplicate.imported == 0
    assert service.candidates(document.document_id) == ()


def test_translation_is_independent_aligned_approvable_and_restart_safe(
    tmp_path: Path,
) -> None:
    path = tmp_path / "translation.sqlite3"
    document = _document(texts=("君の声",))
    service = _service(path, document, _FakeRomanizer())
    service.import_aligned(
        document,
        kind=RepresentationKind.TRANSLATED,
        lines=(ImportedRepresentationLine("line-0001", "I hear your voice"),),
        provenance=ContentProvenance.IMPORTED,
        source_name="local-fixture",
        language="en",
    )
    service.approve(document, "line-0001", RepresentationKind.TRANSLATED)

    restarted = RepresentationService(
        _FakeRomanizer(), open_storage(path).representations, now=lambda: NOW
    )
    translated = restarted.effective_lines(document, RepresentationKind.TRANSLATED)[0]
    romanized = restarted.effective_lines(document, RepresentationKind.ROMANIZED)[0]

    assert translated.text == "I hear your voice"
    assert translated.approval_state is ApprovalState.APPROVED
    assert translated.inherited_start_ms == 0
    assert romanized.text is None


def test_one_line_failure_is_partial_and_original_document_survives(
    tmp_path: Path,
) -> None:
    path = tmp_path / "partial.sqlite3"
    document = _document(texts=("君の声", "次の行"))
    service = _service(path, document, _FakeRomanizer(fail_line="line-0002"))

    report = service.generate(document)
    effective = service.effective_lines(document, RepresentationKind.ROMANIZED)

    assert report.generated == 1
    assert report.failed == 1
    assert effective[0].text
    assert effective[1].text is None
    assert open_storage(path).lyrics.get(document.document_id) == document


@pytest.mark.parametrize("provider", (_BrokenRomanizer(), _MalformedRomanizer()))
def test_full_or_malformed_adapter_failure_is_controlled(
    tmp_path: Path, provider: _BrokenRomanizer | _MalformedRomanizer
) -> None:
    path = tmp_path / f"{provider.name}.sqlite3"
    document = _document(texts=("君の声",))
    storage = open_storage(path)
    storage.lyrics.put(document)
    service = RepresentationService(provider, storage.representations, now=lambda: NOW)

    report = service.generate(document)
    effective = service.effective_lines(document, RepresentationKind.ROMANIZED)[0]

    assert report.failed == 1
    assert effective.text is None
    assert effective.diagnostics
    assert open_storage(path).lyrics.get(document.document_id) == document


def test_latin_and_punctuation_do_not_create_redundant_candidates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "latin.sqlite3"
    document = _document(language="en", texts=("I love you", "123 ♪", ""))
    provider = _FakeRomanizer()
    service = _service(path, document, provider)

    report = service.generate(document)

    assert report.generated == 0
    assert report.unavailable == 3
    assert provider.calls == []
    assert service.candidates(document.document_id) == ()


def test_display_settings_default_and_fresh_process_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "display.sqlite3"
    first = open_storage(path).settings
    assert first.get_representation_display() == RepresentationDisplaySettings()

    expected = RepresentationDisplaySettings(False, True, True)
    first.put_representation_display(expected)

    assert open_storage(path).settings.get_representation_display() == expected


def test_desktop_interaction_defaults_passive_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "desktop-interaction.sqlite3"
    first = open_storage(path).settings
    assert first.get_desktop_interaction() == DesktopInteractionSettings(False)

    expected = DesktopInteractionSettings(True)
    first.put_desktop_interaction(expected)

    assert open_storage(path).settings.get_desktop_interaction() == expected


def test_display_settings_control_each_diagnostic_preview_layer(tmp_path: Path) -> None:
    path = tmp_path / "display-preview.sqlite3"
    document = _document(texts=("君の声",))
    service = _service(path, document, _FakeRomanizer())
    service.generate(document)
    service.import_aligned(
        document,
        kind=RepresentationKind.TRANSLATED,
        lines=(ImportedRepresentationLine("line-0001", "I hear your voice"),),
        provenance=ContentProvenance.IMPORTED,
        source_name="local-fixture",
        language="en",
    )

    translation_only = render_representations(
        document,
        service,
        RepresentationDisplaySettings(False, False, True),
    )
    original_and_pronunciation = render_representations(
        document,
        service,
        RepresentationDisplaySettings(True, True, False),
    )

    assert "  original [" not in translation_only
    assert "  romanized [" not in translation_only
    assert "  translated [imported]: I hear your voice" in translation_only
    assert "  original [line-0001]" in original_and_pronunciation
    assert "  romanized [generated" in original_and_pronunciation
    assert "  translated [" not in original_and_pronunciation


def test_original_refresh_does_not_delete_stage_five_evidence(tmp_path: Path) -> None:
    path = tmp_path / "refresh.sqlite3"
    document = _document(texts=("君の声",))
    storage = open_storage(path)
    storage.lyrics.put(document)
    service = RepresentationService(
        _FakeRomanizer(), storage.representations, now=lambda: NOW
    )
    service.generate(document)
    service.approve(document, "line-0001", RepresentationKind.ROMANIZED)

    storage.lyrics.put(document)
    restarted = RepresentationService(
        _FakeRomanizer(), open_storage(path).representations, now=lambda: NOW
    )

    assert restarted.effective_lines(document, RepresentationKind.ROMANIZED)[0].text
    assert (
        restarted.decisions(document.document_id)[0].approval_state
        is ApprovalState.APPROVED
    )


def test_chinese_document_evidence_generates_pinyin_and_reuses_it_after_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "chinese-auto.sqlite3"
    document = _document(
        language=None,
        texts=("阳光彩虹小白马", "阳光彩虹小白马"),
    )
    storage = open_storage(path)
    storage.lyrics.put(document)
    service = RepresentationService(
        OfflineRomanizationProvider(),
        storage.representations,
        language_evidence=IcuHanLanguageEvidenceAdapter(),
        now=lambda: NOW,
    )

    report = service.generate(document)
    effective = service.effective_lines(document, RepresentationKind.ROMANIZED)
    restarted = RepresentationService(
        OfflineRomanizationProvider(),
        open_storage(path).representations,
        language_evidence=IcuHanLanguageEvidenceAdapter(),
        now=lambda: NOW,
    )

    assert report.generated == 2
    assert [item.text for item in effective] == [
        "Yáng guāng cǎi hóng xiǎo bái mǎ",
        "Yáng guāng cǎi hóng xiǎo bái mǎ",
    ]
    assert [item.original_line.line_id for item in effective] == [
        "line-0001",
        "line-0002",
    ]
    assert [item.inherited_start_ms for item in effective] == [0, 5_000]
    assert all(item.provenance is ContentProvenance.GENERATED for item in effective)
    assert all(item.source_name == "pypinyin Hanyu Pinyin" for item in effective)
    assert restarted.generate(document).reused == 2
    assert (
        restarted.effective_lines(document, RepresentationKind.ROMANIZED) == effective
    )
    assert [line.text for line in document.representations[0].lines] == [
        "阳光彩虹小白马",
        "阳光彩虹小白马",
    ]


def test_provider_chinese_language_metadata_routes_without_classifier(
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider-language.sqlite3"
    document = _document(language="zh-Hant", texts=("我聽見你的聲音",))
    storage = open_storage(path)
    storage.lyrics.put(document)
    service = RepresentationService(
        OfflineRomanizationProvider(), storage.representations, now=lambda: NOW
    )

    report = service.generate(document)
    effective = service.effective_lines(document, RepresentationKind.ROMANIZED)[0]

    assert report.generated == 1
    assert effective.text == "Wǒ tīng jiàn nǐ de shēng yīn"
    assert "provider/document language metadata" in report.diagnostics[0]


def test_ambiguous_han_override_persists_and_reset_restores_unavailable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "language-override.sqlite3"
    document = _document(language=None, texts=("東京",))
    storage = open_storage(path)
    storage.lyrics.put(document)
    service = RepresentationService(
        OfflineRomanizationProvider(),
        storage.representations,
        language_evidence=IcuHanLanguageEvidenceAdapter(),
        now=lambda: NOW,
    )

    ambiguous = service.generate(document)
    missing = service.effective_lines(document, RepresentationKind.ROMANIZED)[0]
    override = service.set_language_override(document, "zh-Hant")
    generated = service.generate(document)
    restarted = RepresentationService(
        OfflineRomanizationProvider(),
        open_storage(path).representations,
        language_evidence=IcuHanLanguageEvidenceAdapter(),
        now=lambda: NOW,
    )

    assert ambiguous.unavailable == 1
    assert missing.text is None
    assert "too short" in missing.diagnostics[0]
    assert override.language == "zh"
    assert generated.generated == 1
    assert restarted.language_override(document.document_id) == override
    assert restarted.effective_lines(document, RepresentationKind.ROMANIZED)[0].text
    assert restarted.reset_language_override(document)
    after_reset = restarted.generate(document)
    reset_line = restarted.effective_lines(document, RepresentationKind.ROMANIZED)[0]
    assert after_reset.unavailable == 1
    assert reset_line.text is None
    assert "too short" in reset_line.diagnostics[0]
    assert restarted.language_override(document.document_id) is None


def test_document_kana_routes_han_only_neighbor_to_japanese_not_chinese(
    tmp_path: Path,
) -> None:
    path = tmp_path / "document-japanese.sqlite3"
    document = _document(language=None, texts=("東京", "君の声が聞こえる"))
    storage = open_storage(path)
    storage.lyrics.put(document)
    service = RepresentationService(
        OfflineRomanizationProvider(),
        storage.representations,
        language_evidence=IcuHanLanguageEvidenceAdapter(),
        now=lambda: NOW,
    )

    report = service.generate(document)
    effective = service.effective_lines(document, RepresentationKind.ROMANIZED)

    assert report.generated == 2
    assert "document-level kana" in report.diagnostics[0]
    assert all(item.source_name == "Cutlet Modified Hepburn" for item in effective)
    assert all(item.text for item in effective)


@pytest.mark.parametrize(
    ("text", "kind", "expected_fragment"),
    (
        ("너의 목소리가 들려 English", RepresentationKind.ROMANIZED, "English"),
        ("Я слышу твой voice", RepresentationKind.TRANSLITERATED, "voice"),
        ("Ακούω τη φωνή σου", RepresentationKind.TRANSLITERATED, "Akoúō"),  # noqa: RUF001
        ("أسمع صوتك", RepresentationKind.TRANSLITERATED, "ṣwtk"),
        ("ฉันได้ยินเสียง", RepresentationKind.TRANSLITERATED, "dị̂yin"),
    ),
)
def test_claimed_non_latin_targets_preserve_original_and_generated_provenance(
    tmp_path: Path,
    text: str,
    kind: RepresentationKind,
    expected_fragment: str,
) -> None:
    path = tmp_path / f"{kind.value}.sqlite3"
    document = _document(language=None, texts=(text,))
    storage = open_storage(path)
    storage.lyrics.put(document)
    service = RepresentationService(
        OfflineRomanizationProvider(),
        storage.representations,
        language_evidence=IcuHanLanguageEvidenceAdapter(),
        now=lambda: NOW,
    )

    report = service.generate(document)
    effective = service.effective_lines(document, kind)[0]

    assert report.generated == 1
    assert effective.text is not None
    assert expected_fragment in effective.text
    assert effective.provenance is ContentProvenance.GENERATED
    assert effective.original_line.text == text
    assert document.representations[0].lines[0].text == text
