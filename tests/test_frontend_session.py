"""Shared desktop/future-TUI current-session application boundary tests."""

from __future__ import annotations

from dataclasses import replace

from konokashi.application.frontend_session import (
    FrontendLyricsBundle,
    FrontendSessionService,
)
from konokashi.application.lyric_corrections import LyricCorrectionService
from konokashi.application.settings import DesktopInteractionSettings
from konokashi.domain.identity import YouTubeIdentity
from konokashi.domain.lyric_corrections import LyricLineCorrection, LyricLineEdit
from konokashi.domain.lyrics import (
    LyricsAlternativeResult,
    LyricsResolutionResult,
    LyricsResolutionStatus,
    RepresentationKind,
)
from konokashi.domain.models import PlayerListResult
from konokashi.domain.representations import (
    EffectiveRepresentationLine,
    LanguageRoutingEvidence,
    RepresentationAvailability,
    RepresentationDisplaySettings,
    RepresentationGenerationReport,
    RepresentationLayerStatus,
    RepresentationUncertainty,
)
from konokashi.domain.synchronization import LyricDocumentTiming
from konokashi.domain.tracks import (
    ArtistCredit,
    Confidence,
    PlayerAssessment,
    PlayerSelectionResult,
    ResolvedTrack,
    TrackCandidate,
)
from konokashi.domain.youtube_metadata import YouTubeMetadataEnrichmentResult
from tests.stage2_helpers import fixture_snapshot
from tests.test_lyrics_sync import document


def _track() -> ResolvedTrack:
    raw = replace(fixture_snapshot("stage2/youtube_jesskah.json"), rate=1.0)
    return ResolvedTrack(
        raw,
        YouTubeIdentity("xa4WrgqI7q0"),
        TrackCandidate("Track", ("Artist",), "Album", 5_000_000),
        Confidence.HIGH,
    )


class _Selection:
    def __init__(self, track: ResolvedTrack) -> None:
        self.track = track
        self.config = None

    def select(  # type: ignore[no-untyped-def]
        self, players, config, *, player_override=None
    ):
        self.config = config
        self.player_override = player_override
        return PlayerSelectionResult(PlayerAssessment(self.track, ("selected",), (1,)))


class _Lyrics:
    def __init__(self, track: ResolvedTrack) -> None:
        self.result = LyricsResolutionResult(
            track.source_identity,
            LyricsResolutionStatus.FOUND_TIMED,
            document=document(),
        )

    def resolve(self, track, *, offline=False, refresh=False):  # type: ignore[no-untyped-def]
        assert track.source_identity == self.result.source_identity
        assert offline is False
        assert refresh is False
        return self.result


class _Representations:
    def __init__(self) -> None:
        self.generated = []

    def generate(self, lyric_document):  # type: ignore[no-untyped-def]
        self.generated.append(lyric_document.document_id)
        return RepresentationGenerationReport(lyric_document.document_id, 1, 1, 0, 0, 0)

    def effective_lines(self, lyric_document, kind):  # type: ignore[no-untyped-def]
        line = lyric_document.representations[0].lines[0]
        text = "romaji" if kind is RepresentationKind.ROMANIZED else None
        return (
            EffectiveRepresentationLine(line, kind, text, None, None, None, None, None),
        )

    def layer_status(self, lyric_document, kind, *, visible):  # type: ignore[no-untyped-def]
        return RepresentationLayerStatus(
            kind,
            (
                RepresentationAvailability.AVAILABLE_SHOWN
                if visible
                else RepresentationAvailability.AVAILABLE_HIDDEN
            ),
            1,
            1,
            1,
            0,
            0,
            0,
            1,
            1,
            1 if visible else 0,
            ("fixture",),
        )

    def routing_language(self, _document):  # type: ignore[no-untyped-def]
        return LanguageRoutingEvidence(
            "ja", RepresentationUncertainty.NONE, "fixture routing"
        )


class _Settings:
    def __init__(self) -> None:
        from konokashi.domain.tracks import PlayerSelectionConfig

        self.player = PlayerSelectionConfig(preferred_players=("strawberry",))
        self.player_writes = []
        self.display = RepresentationDisplaySettings(True, True, False)
        self.interactions = DesktopInteractionSettings()

    def get_player_selection(self):  # type: ignore[no-untyped-def]
        return self.player

    def put_player_selection(self, value):  # type: ignore[no-untyped-def]
        self.player_writes.append(value)
        self.player = value

    def get_representation_display(self):  # type: ignore[no-untyped-def]
        return self.display

    def put_representation_display(self, value):  # type: ignore[no-untyped-def]
        self.display = value

    def get_desktop_interaction(self):  # type: ignore[no-untyped-def]
        return self.interactions

    def put_desktop_interaction(self, value):  # type: ignore[no-untyped-def]
        self.interactions = value


class _Timing:
    def get_document_timing(self, document_id):  # type: ignore[no-untyped-def]
        return LyricDocumentTiming(document_id, 25_000)


class _LineCorrections:
    def __init__(self) -> None:
        self.values: dict[str, tuple[LyricLineCorrection, ...]] = {}

    def get(self, document_id: str) -> tuple[LyricLineCorrection, ...]:
        return self.values.get(document_id, ())

    def replace(
        self, document_id: str, corrections: tuple[LyricLineCorrection, ...]
    ) -> None:
        self.values[document_id] = corrections

    def reset(self, document_id: str) -> int:
        return len(self.values.pop(document_id, ()))


def test_frontend_session_combines_existing_services_without_adapter_values() -> None:
    track = _track()
    selection = _Selection(track)
    settings = _Settings()
    representations = _Representations()
    cancellations: list[str] = []
    service = FrontendSessionService(
        selection,  # type: ignore[arg-type]
        _Lyrics(track),  # type: ignore[arg-type]
        representations,  # type: ignore[arg-type]
        settings,  # type: ignore[arg-type]
        _Timing(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        lambda: cancellations.append("cancelled"),
    )

    selected = service.select_track(PlayerListResult(), player_override="strawberry")
    assert selected.selected is not None
    assert selected.selected.track is track
    assert selection.config == settings.player
    assert selection.player_override == "strawberry"
    assert settings.player_writes == []

    bundle = service.load_track(track)
    assert bundle.track is track
    assert bundle.resolution.status is LyricsResolutionStatus.FOUND_TIMED
    assert bundle.display_settings == settings.display
    assert bundle.document_timing == LyricDocumentTiming(
        bundle.resolution.document.document_id,  # type: ignore[union-attr]
        25_000,
    )
    assert len(bundle.representations) == 3
    assert bundle.representations[0].text == "romaji"
    assert bundle.line_cache is not None
    assert bundle.line_cache.original_indexes["one"] == 0
    assert (
        bundle.line_cache.representations[("one", RepresentationKind.ROMANIZED)].text
        == "romaji"
    )
    assert representations.generated == [bundle.resolution.document.document_id]  # type: ignore[union-attr]

    changed = RepresentationDisplaySettings(False, True, True)
    service.put_display_settings(changed)
    assert settings.display == changed
    interactions = DesktopInteractionSettings(True)
    service.put_interaction_settings(interactions)
    assert settings.interactions == interactions
    service.cancel_inflight()
    assert cancellations == ["cancelled"]


def test_review_only_youtube_enrichment_feeds_bounded_interpretations() -> None:
    track = _track()
    native = TrackCandidate(
        "アンドロイドガール",
        ("DECO*27",),
        None,
        215_200_000,
        strategy="youtube-enrichment:youtube_description",
        artist_credit=ArtistCredit(("DECO*27",), ("初音ミク",)),
    )

    class Lyrics:
        searched_track = None

        def alternatives(self, searched_track, **kwargs):  # type: ignore[no-untyped-def]
            self.searched_track = searched_track
            assert kwargs["title"] is None
            return LyricsAlternativeResult(searched_track.source_identity)

    class Enricher:
        calls = 0

        def enrich(self, enriched_track, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            assert enriched_track is track
            assert kwargs == {"offline": False, "refresh": False}
            return YouTubeMetadataEnrichmentResult(
                track.source_identity,  # type: ignore[arg-type]
                (native,),
                ("enriched",),
            )

    class Corrections:
        alternatives = None

        def snapshot(self, snapshot_track, resolution, alternatives):  # type: ignore[no-untyped-def]
            assert snapshot_track is track
            assert resolution.source_identity == track.source_identity
            self.alternatives = alternatives
            return "review"

    lyrics = Lyrics()
    enricher = Enricher()
    corrections = Corrections()
    service = FrontendSessionService(
        _Selection(track),  # type: ignore[arg-type]
        lyrics,  # type: ignore[arg-type]
        _Representations(),  # type: ignore[arg-type]
        _Settings(),  # type: ignore[arg-type]
        _Timing(),  # type: ignore[arg-type]
        corrections,  # type: ignore[arg-type]
        youtube_metadata=enricher,  # type: ignore[arg-type]
    )
    bundle = FrontendLyricsBundle(
        track,
        LyricsResolutionResult(track.source_identity, LyricsResolutionStatus.NO_RESULT),
        (),
        RepresentationDisplaySettings(),
        None,
    )

    assert service.review_track(bundle, enrich_youtube=True) == "review"
    assert enricher.calls == 1
    assert lyrics.searched_track is not None
    assert native in lyrics.searched_track.interpretation_candidates
    assert corrections.alternatives is not None
    assert corrections.alternatives.diagnostics[0] == "enriched"


def test_frontend_projects_line_corrections_into_every_shared_display_value() -> None:
    track = _track()
    repository = _LineCorrections()
    service = FrontendSessionService(
        _Selection(track),  # type: ignore[arg-type]
        _Lyrics(track),  # type: ignore[arg-type]
        _Representations(),  # type: ignore[arg-type]
        _Settings(),  # type: ignore[arg-type]
        _Timing(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        lyric_corrections=LyricCorrectionService(repository),
    )
    initial = service.load_track(track)
    assert initial.source_document == document()
    assert initial.correction_projection is not None

    edits = tuple(
        LyricLineEdit(
            line.line_id,
            "first repaired" if line.line_id == "one" else line.effective_text,
            line.effective_start_ms,
        )
        for line in initial.correction_projection.editor.lines
    )
    assert service.put_lyric_edits(initial, edits) == 1

    repaired = service.load_track(track)
    assert repaired.resolution.document is not None
    assert repaired.resolution.document.representations[0].lines[0].text == (
        "first repaired"
    )
    assert repaired.line_cache is not None
    assert repaired.line_cache.originals[0].text == "first repaired"
    assert repaired.representations[0].original_line.text == "first repaired"
    assert repaired.resolution.source_label == "fixture + local corrections"
    assert service.reset_lyric_edits(repaired) == 1
