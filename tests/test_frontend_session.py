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
    has_online_providers = True
    cancellation_generation = 0

    def __init__(self, track: ResolvedTrack) -> None:
        self.result = LyricsResolutionResult(
            track.source_identity,
            LyricsResolutionStatus.FOUND_TIMED,
            document=document(),
        )

    def resolve(self, track, *, offline=False, refresh=False, expected_generation=None):  # type: ignore[no-untyped-def]
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
        self.automatic_web_metadata = True

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

    def get_automatic_web_metadata(self) -> bool:
        return self.automatic_web_metadata


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


def test_inadequate_youtube_resolution_enriches_and_retries_automatically() -> None:
    initial = _track()
    weak = replace(
        initial,
        candidate=TrackCandidate("Luce sul mare", (), None, 198_000_000),
        confidence=Confidence.LOW,
        interpretation_candidates=(
            TrackCandidate("Luce sul mare", (), None, 198_000_000),
        ),
    )
    native = TrackCandidate(
        "Luce sul mare",
        ("Cantante Fittizia",),
        None,
        198_000_000,
        strategy="youtube-enrichment:labelled-description",
    )

    class Lyrics:
        has_online_providers = True
        cancellation_generation = 0

        calls: list[ResolvedTrack]

        def __init__(self) -> None:
            self.calls = []

        def resolve(
            self, track, *, offline=False, refresh=False, expected_generation=None
        ):  # type: ignore[no-untyped-def]
            self.calls.append(track)
            if not any(
                candidate.identity_confidence is Confidence.MEDIUM
                for candidate in track.interpretation_candidates
            ):
                return LyricsResolutionResult(
                    track.source_identity,
                    LyricsResolutionStatus.NO_RESULT,
                    diagnostics=("initial query missed",),
                    network_used=True,
                )
            return LyricsResolutionResult(
                track.source_identity,
                LyricsResolutionStatus.FOUND_TIMED,
                document=document(),
                diagnostics=("enriched query succeeded",),
                cache_hit=True,
            )

    class Enricher:
        calls = 0

        def enrich(self, track, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            assert track is weak
            assert kwargs == {"offline": False, "refresh": False}
            return YouTubeMetadataEnrichmentResult(
                weak.source_identity,  # type: ignore[arg-type]
                (native,),
                ("metadata-only enrichment",),
                network_used=True,
            )

    lyrics = Lyrics()
    enricher = Enricher()
    settings = _Settings()
    ticks = iter((1.0, 1.1, 1.1, 1.25, 1.25, 1.4, 1.4))
    service = FrontendSessionService(
        _Selection(weak),  # type: ignore[arg-type]
        lyrics,  # type: ignore[arg-type]
        _Representations(),  # type: ignore[arg-type]
        settings,  # type: ignore[arg-type]
        _Timing(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        youtube_metadata=enricher,  # type: ignore[arg-type]
        monotonic_clock=lambda: next(ticks, 1.4),
    )

    bundle = service.load_track(weak)

    assert len(lyrics.calls) == 2
    assert enricher.calls == 1
    assert bundle.resolution.status is LyricsResolutionStatus.FOUND_TIMED
    assert bundle.resolution.cache_hit and bundle.resolution.network_used
    assert bundle.resolution.diagnostics[:3] == (
        "initial query missed",
        "metadata-only enrichment",
        "enriched query succeeded",
    )
    assert bundle.resolution.diagnostics[3] == (
        "automatic YouTube retry: first_pass=100 ms; metadata=150 ms; "
        "retry=150 ms; total=400 ms; retried=True"
    )
    assert bundle.track.candidate == weak.candidate
    assert bundle.track.raw_snapshot == weak.raw_snapshot
    assert bundle.track.interpretation_candidates[-1].identity_confidence is (
        Confidence.MEDIUM
    )
    settings.automatic_web_metadata = False
    disabled = service.load_track(weak)
    assert disabled.resolution.document is None
    assert len(lyrics.calls) == 3
    assert enricher.calls == 1
    settings.automatic_web_metadata = True
    lyrics.has_online_providers = False
    no_provider = service.load_track(weak)
    assert no_provider.resolution.document is None
    assert enricher.calls == 1


def test_superseded_youtube_enrichment_cannot_start_a_stale_provider_retry() -> None:
    track = _track()
    calls: list[str] = []
    service: FrontendSessionService

    class Lyrics:
        has_online_providers = True
        cancellation_generation = 0

        def resolve(self, _track, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("lyrics")
            return LyricsResolutionResult(
                track.source_identity, LyricsResolutionStatus.NO_RESULT
            )

    class Enricher:
        def enrich(self, _track, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("enrichment")
            service.cancel_inflight()
            return YouTubeMetadataEnrichmentResult(
                track.source_identity,  # type: ignore[arg-type]
                (TrackCandidate("Song", ("Artist",), None, 5_000_000),),
            )

    service = FrontendSessionService(
        _Selection(track),  # type: ignore[arg-type]
        Lyrics(),  # type: ignore[arg-type]
        _Representations(),  # type: ignore[arg-type]
        _Settings(),  # type: ignore[arg-type]
        _Timing(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        lambda: calls.append("cancelled"),
        Enricher(),  # type: ignore[arg-type]
    )

    result = service.load_track(track)

    assert result.resolution.document is None
    assert calls == ["lyrics", "enrichment", "cancelled"]


def test_automatic_youtube_retry_preserves_offline_and_refresh_intent() -> None:
    track = _track()
    calls: list[tuple[str, bool, bool]] = []

    class Lyrics:
        has_online_providers = True
        cancellation_generation = 0

        def resolve(
            self, resolved, *, offline=False, refresh=False, expected_generation=None
        ):  # type: ignore[no-untyped-def]
            calls.append(("lyrics", offline, refresh))
            if len(calls) == 1:
                return LyricsResolutionResult(
                    resolved.source_identity,
                    LyricsResolutionStatus.OFFLINE_MISS,
                )
            return LyricsResolutionResult(
                resolved.source_identity,
                LyricsResolutionStatus.FOUND_TIMED,
                document=document(),
                cache_hit=True,
            )

    class Enricher:
        def enrich(self, resolved, *, offline=False, refresh=False):  # type: ignore[no-untyped-def]
            calls.append(("metadata", offline, refresh))
            return YouTubeMetadataEnrichmentResult(
                resolved.source_identity,
                (TrackCandidate("New Song", ("New Artist",), None, 5_000_000),),
                cache_hit=True,
            )

    service = FrontendSessionService(
        _Selection(track),  # type: ignore[arg-type]
        Lyrics(),  # type: ignore[arg-type]
        _Representations(),  # type: ignore[arg-type]
        _Settings(),  # type: ignore[arg-type]
        _Timing(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        youtube_metadata=Enricher(),  # type: ignore[arg-type]
    )

    bundle = service.load_track(track, offline=True, refresh=True)

    assert calls == [
        ("lyrics", True, True),
        ("metadata", True, True),
        ("lyrics", True, True),
    ]
    assert bundle.resolution.status is LyricsResolutionStatus.FOUND_TIMED
    assert bundle.resolution.cache_hit is True
    assert bundle.resolution.network_used is False


def test_successful_first_pass_never_requests_youtube_metadata() -> None:
    track = _track()
    enrichment_calls = 0

    class Enricher:
        def enrich(self, _track, **_kwargs):  # type: ignore[no-untyped-def]
            nonlocal enrichment_calls
            enrichment_calls += 1
            raise AssertionError("metadata must not be requested after a match")

    service = FrontendSessionService(
        _Selection(track),  # type: ignore[arg-type]
        _Lyrics(track),  # type: ignore[arg-type]
        _Representations(),  # type: ignore[arg-type]
        _Settings(),  # type: ignore[arg-type]
        _Timing(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        youtube_metadata=Enricher(),  # type: ignore[arg-type]
    )

    assert (
        service.load_track(track).resolution.status
        is LyricsResolutionStatus.FOUND_TIMED
    )
    assert enrichment_calls == 0


def test_opening_review_builds_audit_without_requesting_provider_alternatives() -> None:
    track = _track()
    lyrics = _Lyrics(track)

    class Corrections:
        def snapshot(  # type: ignore[no-untyped-def]
            self, snapshot_track, resolution, alternatives, **kwargs
        ):
            assert snapshot_track is track
            assert resolution.status is LyricsResolutionStatus.NO_RESULT
            assert alternatives == LyricsAlternativeResult(track.source_identity)
            assert kwargs == {"alternatives_searched": False}
            return "review without search"

    service = FrontendSessionService(
        _Selection(track),  # type: ignore[arg-type]
        lyrics,  # type: ignore[arg-type]
        _Representations(),  # type: ignore[arg-type]
        _Settings(),  # type: ignore[arg-type]
        _Timing(),  # type: ignore[arg-type]
        Corrections(),  # type: ignore[arg-type]
    )
    bundle = FrontendLyricsBundle(
        track,
        LyricsResolutionResult(track.source_identity, LyricsResolutionStatus.NO_RESULT),
        (),
        RepresentationDisplaySettings(),
        None,
    )

    assert service.review_track(bundle) == "review without search"


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

        def snapshot(  # type: ignore[no-untyped-def]
            self, snapshot_track, resolution, alternatives, **kwargs
        ):
            assert snapshot_track is track
            assert resolution.source_identity == track.source_identity
            assert kwargs == {"alternatives_searched": True}
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
