"""End-to-end multilingual document-to-frontend representation tests."""

from __future__ import annotations

import socket
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from konokashi.application.frontend_session import FrontendSessionService
from konokashi.application.representations import RepresentationService
from konokashi.application.review_corrections import ReviewCorrectionService
from konokashi.domain.identity import LocalFileIdentity
from konokashi.domain.lyrics import (
    ApprovalState,
    ContentProvenance,
    LyricDocument,
    LyricDocumentKind,
    LyricLine,
    LyricRepresentation,
    LyricsAlternativeResult,
    LyricsResolutionResult,
    LyricsResolutionStatus,
    RepresentationKind,
)
from konokashi.domain.models import PlayerCapabilities, PlayerSnapshot, RawTrackMetadata
from konokashi.domain.representations import (
    GenerationStatus,
    LanguageRoutingStatus,
    RepresentationAvailability,
    RepresentationCandidate,
    RepresentationDisplaySettings,
    RepresentationUncertainty,
    RomanizationProviderResult,
    RomanizationRequest,
)
from konokashi.domain.tracks import Confidence, ResolvedTrack, TrackCandidate
from konokashi.infrastructure.romanization.offline import (
    IcuHanLanguageEvidenceAdapter,
    OfflineRomanizationProvider,
)
from konokashi.infrastructure.storage.bootstrap import open_storage

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)


def _document(
    document_id: str,
    texts: tuple[str, ...],
    *,
    language: str | None = None,
    extra: tuple[LyricRepresentation, ...] = (),
) -> LyricDocument:
    originals = tuple(
        LyricLine(f"line-{index + 1:04d}", text, index * 5_000)
        for index, text in enumerate(texts)
    )
    return LyricDocument(
        document_id,
        LyricDocumentKind.SYNCED,
        "fixture-provider",
        "\n".join(texts),
        f"checksum-{document_id}",
        ApprovalState.UNREVIEWED,
        NOW,
        (
            LyricRepresentation(
                "original",
                RepresentationKind.ORIGINAL,
                ContentProvenance.PROVIDER,
                ApprovalState.UNREVIEWED,
                originals,
                language=language,
            ),
            *extra,
        ),
        language=language,
    )


def _track() -> ResolvedTrack:
    identity = LocalFileIdentity("/music/multilingual.flac")
    metadata = RawTrackMetadata(
        title="Multilingual",
        artists=("Fixture",),
        url="file:///music/multilingual.flac",
        duration_us=30_000_000,
    )
    snapshot = PlayerSnapshot(
        "fixture",
        "org.mpris.MediaPlayer2.fixture",
        "Fixture",
        "fixture",
        "Playing",
        metadata,
        0,
        PlayerCapabilities(),
    )
    return ResolvedTrack(
        snapshot,
        identity,
        TrackCandidate("Multilingual", ("Fixture",), None, 30_000_000),
        Confidence.HIGH,
    )


class _Lyrics:
    def __init__(self, document: LyricDocument, track: ResolvedTrack) -> None:
        self.document = document
        self.track = track

    def resolve(
        self, track: ResolvedTrack, *, offline: bool = False, refresh: bool = False
    ) -> LyricsResolutionResult:
        assert track is self.track
        assert not offline
        assert not refresh
        return LyricsResolutionResult(
            track.source_identity,
            LyricsResolutionStatus.FOUND_TIMED,
            self.document,
        )

    def alternatives(
        self,
        track: ResolvedTrack,
        **_kwargs: object,
    ) -> LyricsAlternativeResult:
        assert track is self.track
        return LyricsAlternativeResult(track.source_identity)


def _frontend(
    path: Path,
    document: LyricDocument,
    *,
    display: RepresentationDisplaySettings | None = None,
) -> tuple[FrontendSessionService, ResolvedTrack, RepresentationService]:
    storage = open_storage(path)
    storage.lyrics.put(document)
    storage.settings.put_representation_display(
        display or RepresentationDisplaySettings()
    )
    representations = RepresentationService(
        OfflineRomanizationProvider(),
        storage.representations,
        language_evidence=IcuHanLanguageEvidenceAdapter(),
        now=lambda: NOW,
    )
    track = _track()
    corrections = ReviewCorrectionService(
        track_overrides=storage.track_overrides,
        lyrics=storage.lyrics,
        matches=storage.lyrics_matches,
        provider_documents=storage.lyrics,
        timing=storage.timing_calibrations,
        now=lambda: NOW,
    )
    return (
        FrontendSessionService(
            object(),  # type: ignore[arg-type]
            _Lyrics(document, track),  # type: ignore[arg-type]
            representations,
            storage.settings,
            storage.timing_calibrations,
            corrections,
        ),
        track,
        representations,
    )


@pytest.mark.parametrize(
    ("document_id", "texts", "language", "kind"),
    (
        ("japanese-kana", ("君の声が聞こえる",), None, RepresentationKind.ROMANIZED),
        ("japanese-han", ("東京",), "ja", RepresentationKind.ROMANIZED),
        ("mandarin", ("阳光彩虹小白马",), None, RepresentationKind.ROMANIZED),
        ("mandarin-shared-han", ("世界和平",), "zh", RepresentationKind.ROMANIZED),
        ("korean", ("너의 목소리가 들려",), None, RepresentationKind.ROMANIZED),
        ("cyrillic", ("Я слышу твой голос",), None, RepresentationKind.TRANSLITERATED),
        (
            "mixed-latin",
            ("Café — я слышу тебя",),
            None,
            RepresentationKind.TRANSLITERATED,
        ),
    ),
)
def test_supported_scripts_generate_locally_into_frontend_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    document_id: str,
    texts: tuple[str, ...],
    language: str | None,
    kind: RepresentationKind,
) -> None:
    def forbidden_network(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("local representation generation attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    document = _document(document_id, texts, language=language)
    service, track, _representations = _frontend(tmp_path / "state.sqlite3", document)

    bundle = service.load_track(track)
    selected = next(item for item in bundle.representations if item.kind is kind)
    status = next(item for item in bundle.layer_statuses if item.kind is kind)

    assert selected.original_line.line_id == "line-0001"
    assert selected.original_line.text == texts[0]
    assert selected.text
    assert selected.provenance is ContentProvenance.GENERATED
    assert selected.source_name
    assert selected.source_version
    assert status.availability is RepresentationAvailability.AVAILABLE_SHOWN
    assert status.generated_successfully == 1
    assert status.candidate_selected == 1
    assert status.candidate_rendered == 1
    assert bundle.generation_report is not None
    assert bundle.generation_report.generated == 1


def test_ambiguous_mixed_and_ineligible_lines_remain_explainable_and_persisted(
    tmp_path: Path,
) -> None:
    document = _document(
        "ambiguous",
        ("東京", "Привет 안녕", "♪ 123", ""),
    )
    service, track, representations = _frontend(tmp_path / "state.sqlite3", document)

    bundle = service.load_track(track)
    routing = bundle.routing
    candidates = representations.candidates(document.document_id)
    transliterated = next(
        item
        for item in bundle.layer_statuses
        if item.kind is RepresentationKind.TRANSLITERATED
    )

    assert routing is not None
    assert routing.status is LanguageRoutingStatus.AMBIGUOUS
    assert bundle.generation_report is not None
    assert bundle.generation_report.eligible == 2
    assert bundle.generation_report.unavailable == 4
    assert bundle.generation_report.candidate_persisted == 2
    assert any(
        candidate.source_line_id == "line-0002"
        and candidate.status is GenerationStatus.UNAVAILABLE
        and candidate.diagnostics
        for candidate in candidates
    )
    assert transliterated.engine_unavailable == 1
    assert transliterated.availability is RepresentationAvailability.UNAVAILABLE


def test_language_adapter_failure_reaches_frontend_as_failed_not_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenTransform:
        def transliterate(self, _text: str) -> str:
            raise RuntimeError("fixture failure")

    document = _document("routing-failed", ("阳光彩虹小白马",))
    path = tmp_path / "state.sqlite3"
    storage = open_storage(path)
    storage.lyrics.put(document)
    evidence = IcuHanLanguageEvidenceAdapter()
    monkeypatch.setattr(evidence, "_simplified_to_traditional", BrokenTransform())
    monkeypatch.setattr(evidence, "_traditional_to_simplified", BrokenTransform())
    representations = RepresentationService(
        OfflineRomanizationProvider(),
        storage.representations,
        language_evidence=evidence,
        now=lambda: NOW,
    )

    report = representations.generate(document)
    status = representations.layer_status(
        document, RepresentationKind.ROMANIZED, visible=True
    )

    assert report.failed == 1
    assert report.unavailable == 0
    assert status.availability is RepresentationAvailability.FAILED
    assert status.generation_failed == 1
    assert "failed: RuntimeError" in " ".join(status.diagnostics)


def test_exact_document_han_override_set_reset_and_restart(tmp_path: Path) -> None:
    document = _document("shared-han", ("世界和平",))
    path = tmp_path / "state.sqlite3"
    service, track, representations = _frontend(path, document)
    ambiguous = service.load_track(track)

    assert ambiguous.routing is not None
    assert ambiguous.routing.status is LanguageRoutingStatus.AMBIGUOUS
    assert (
        next(
            line
            for line in ambiguous.representations
            if line.kind is RepresentationKind.ROMANIZED
        ).text
        is None
    )

    representations.set_draft(
        document,
        "line-0001",
        RepresentationKind.ROMANIZED,
        "user reading",
    )
    representations.approve(document, "line-0001", RepresentationKind.ROMANIZED)
    service.set_document_language(ambiguous, "zh")
    restarted, restarted_track, restarted_representations = _frontend(path, document)
    explicit = restarted.load_track(restarted_track)
    assert explicit.routing is not None
    assert explicit.routing.status is LanguageRoutingStatus.EXPLICIT
    assert explicit.routing.language == "zh"
    assert restarted_representations.language_override(document.document_id) is not None
    assert (
        next(
            line
            for line in explicit.representations
            if line.kind is RepresentationKind.ROMANIZED
        ).text
        == "user reading"
    )

    assert restarted.reset_document_language(explicit)
    after_reset, after_reset_track, reset_representations = _frontend(path, document)
    automatic = after_reset.load_track(after_reset_track)
    assert automatic.routing is not None
    assert automatic.routing.status is LanguageRoutingStatus.AMBIGUOUS
    assert reset_representations.language_override(document.document_id) is None
    assert (
        next(
            line
            for line in automatic.representations
            if line.kind is RepresentationKind.ROMANIZED
        ).text
        == "user reading"
    )


def test_document_native_translation_visibility_and_user_restart_are_truthful(
    tmp_path: Path,
) -> None:
    translated = LyricRepresentation(
        "provider-translation",
        RepresentationKind.TRANSLATED,
        ContentProvenance.PROVIDER,
        ApprovalState.UNREVIEWED,
        (
            LyricLine(
                "translated-0001",
                "I hear your voice",
                source_line_id="line-0001",
            ),
        ),
        language="en",
    )
    document = _document("translated", ("君の声",), language="ja", extra=(translated,))
    path = tmp_path / "state.sqlite3"
    service, track, _representations = _frontend(path, document)

    hidden = service.load_track(track)
    hidden_status = next(
        item
        for item in hidden.layer_statuses
        if item.kind is RepresentationKind.TRANSLATED
    )
    provider_line = next(
        item
        for item in hidden.representations
        if item.kind is RepresentationKind.TRANSLATED
    )
    assert hidden_status.availability is RepresentationAvailability.AVAILABLE_HIDDEN
    assert provider_line.text == "I hear your voice"
    assert provider_line.provenance is ContentProvenance.PROVIDER
    review = service.review_track(hidden)
    assert review.routing_status is LanguageRoutingStatus.AUTOMATIC
    assert review.routing_language == "ja"
    assert review.translation_lines[0].source_line_id == "line-0001"
    assert review.translation_lines[0].translated_text == "I hear your voice"

    shown_settings = replace(hidden.display_settings, show_translated=True)
    shown_status = next(
        item
        for item in service.representation_statuses(hidden, shown_settings)
        if item.kind is RepresentationKind.TRANSLATED
    )
    assert shown_status.availability is RepresentationAvailability.AVAILABLE_SHOWN
    assert shown_status.candidate_rendered == 1

    service.put_translation(
        hidden, source_line_id="line-0001", text="I can hear your voice"
    )
    restarted, restarted_track, _ = _frontend(path, document, display=shown_settings)
    bundle = restarted.load_track(restarted_track)
    user_line = next(
        item
        for item in bundle.representations
        if item.kind is RepresentationKind.TRANSLATED
    )
    assert user_line.text == "I can hear your voice"
    assert user_line.provenance is ContentProvenance.USER
    assert user_line.approval_state is ApprovalState.APPROVED
    assert user_line.original_line.text == "君の声"
    assert restarted.reset_translation(bundle, source_line_id="line-0001")
    fallback = next(
        item
        for item in restarted.load_track(restarted_track).representations
        if item.kind is RepresentationKind.TRANSLATED
    )
    assert fallback.text == "I hear your voice"
    assert fallback.provenance is ContentProvenance.PROVIDER


def test_document_native_generated_layer_preserves_engine_and_version(
    tmp_path: Path,
) -> None:
    generated = LyricRepresentation(
        "generated-translation",
        RepresentationKind.TRANSLATED,
        ContentProvenance.GENERATED,
        ApprovalState.UNREVIEWED,
        (LyricLine("translated-1", "Voice", source_line_id="line-0001"),),
        language="en",
        generator_name="offline-fixture-engine",
        generator_version="4.2",
    )
    document = _document("generated-layer", ("声",), language="ja", extra=(generated,))
    _frontend_service, _track_value, representations = _frontend(
        tmp_path / "state.sqlite3", document
    )

    line = representations.effective_lines(document, RepresentationKind.TRANSLATED)[0]
    assert line.provenance is ContentProvenance.GENERATED
    assert line.source_name == "offline-fixture-engine"
    assert line.source_version == "4.2"


def test_translation_unavailable_partial_and_actual_failure_are_distinct(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite3"
    partial_layer = LyricRepresentation(
        "partial-translation",
        RepresentationKind.TRANSLATED,
        ContentProvenance.LOCAL,
        ApprovalState.UNREVIEWED,
        (LyricLine("translated-1", "First", source_line_id="line-0001"),),
        language="en",
    )
    partial_document = _document(
        "partial", ("最初", "次"), language="ja", extra=(partial_layer,)
    )
    partial_service, partial_track, _ = _frontend(
        path,
        partial_document,
        display=RepresentationDisplaySettings(show_translated=True),
    )
    partial_bundle = partial_service.load_track(partial_track)
    partial = next(
        item
        for item in partial_bundle.layer_statuses
        if item.kind is RepresentationKind.TRANSLATED
    )
    assert partial.availability is RepresentationAvailability.PARTIAL
    assert partial.candidate_selected == 1
    assert partial.candidate_rendered == 1

    unavailable_document = _document("unavailable-translation", ("君",), language="ja")
    unavailable_service, unavailable_track, _ = _frontend(
        tmp_path / "unavailable.sqlite3",
        unavailable_document,
        display=RepresentationDisplaySettings(show_translated=True),
    )
    unavailable_bundle = unavailable_service.load_track(unavailable_track)
    unavailable = next(
        item
        for item in unavailable_bundle.layer_statuses
        if item.kind is RepresentationKind.TRANSLATED
    )
    assert unavailable.availability is RepresentationAvailability.UNAVAILABLE

    failed_document = _document("failed-translation", ("君",), language="ja")
    failed_storage = open_storage(tmp_path / "failed.sqlite3")
    failed_storage.lyrics.put(failed_document)
    failed_storage.representations.put_candidates(
        (
            RepresentationCandidate(
                "failed-source",
                failed_document.document_id,
                "line-0001",
                RepresentationKind.TRANSLATED,
                GenerationStatus.FAILED,
                ContentProvenance.LOCAL,
                "local-translation-import",
                "1",
                ApprovalState.UNREVIEWED,
                RepresentationUncertainty.NONE,
                NOW,
                NOW,
                diagnostics=("local translation source could not be read",),
            ),
        )
    )
    failed_representations = RepresentationService(
        OfflineRomanizationProvider(),
        failed_storage.representations,
        now=lambda: NOW,
    )
    failed = failed_representations.layer_status(
        failed_document,
        RepresentationKind.TRANSLATED,
        visible=True,
    )
    assert failed.availability is RepresentationAvailability.FAILED
    assert failed.generation_failed == 1
    assert "local translation source could not be read" in failed.diagnostics


class _EmptyRomanizer:
    name = "empty-fixture"

    def generate(self, request: RomanizationRequest) -> RomanizationProviderResult:
        return RomanizationProviderResult(
            GenerationStatus.AVAILABLE,
            self.name,
            "1",
            RepresentationKind.ROMANIZED,
            text="",
        )


class _UnavailableRomanizer:
    name = "unavailable-fixture"

    def generate(self, _request: RomanizationRequest) -> RomanizationProviderResult:
        return RomanizationProviderResult(
            GenerationStatus.UNAVAILABLE,
            self.name,
            "1",
            RepresentationKind.ROMANIZED,
            diagnostics=("fixture engine unavailable",),
        )


def test_eligible_engine_unavailable_is_retained_separately(tmp_path: Path) -> None:
    document = _document("engine-unavailable", ("君の声",), language="ja")
    storage = open_storage(tmp_path / "state.sqlite3")
    storage.lyrics.put(document)
    representations = RepresentationService(
        _UnavailableRomanizer(), storage.representations, now=lambda: NOW
    )

    report = representations.generate(document)
    status = representations.layer_status(
        document, RepresentationKind.ROMANIZED, visible=True
    )

    assert report.eligible == 1
    assert report.unavailable == 1
    assert report.failed == 0
    assert status.engine_unavailable == 1
    assert status.generation_failed == 0
    assert status.availability is RepresentationAvailability.UNAVAILABLE
    assert "fixture engine unavailable" in status.diagnostics


def test_generated_empty_is_distinct_and_document_replacement_cannot_leak_ids(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite3"
    first = _document("first", ("君",), language="ja")
    storage = open_storage(path)
    storage.lyrics.put(first)
    empty = RepresentationService(
        _EmptyRomanizer(), storage.representations, now=lambda: NOW
    )

    report = empty.generate(first)
    status = empty.layer_status(first, RepresentationKind.ROMANIZED, visible=True)
    assert report.generated_empty == 1
    assert status.generated_empty == 1
    assert status.availability is RepresentationAvailability.UNAVAILABLE

    second = _document("second", ("君", "君"), language="ja")
    storage.lyrics.put(second)
    second_service = RepresentationService(
        OfflineRomanizationProvider(), storage.representations, now=lambda: NOW
    )
    second_service.generate(second)
    lines = second_service.effective_lines(second, RepresentationKind.ROMANIZED)
    assert [line.original_line.line_id for line in lines] == ["line-0001", "line-0002"]
    assert all(line.text for line in lines)
    assert all("first" not in (line.candidate_id or "") for line in lines)


def test_duplicate_original_line_ids_are_rejected_before_generation(
    tmp_path: Path,
) -> None:
    document = _document("duplicate", ("君", "声"), language="ja")
    duplicate = replace(
        document,
        representations=(
            replace(
                document.representations[0],
                lines=(
                    document.representations[0].lines[0],
                    replace(document.representations[0].lines[1], line_id="line-0001"),
                ),
            ),
        ),
    )
    repository = open_storage(tmp_path / "state.sqlite3").representations
    service = RepresentationService(
        OfflineRomanizationProvider(), repository, now=lambda: NOW
    )

    with pytest.raises(ValueError, match="duplicate original lyric line IDs"):
        service.generate(duplicate)
