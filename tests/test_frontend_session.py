"""Shared desktop/future-TUI current-session application boundary tests."""

from __future__ import annotations

from dataclasses import replace

from konokashi.application.frontend_session import FrontendSessionService
from konokashi.application.settings import DesktopInteractionSettings
from konokashi.domain.identity import YouTubeIdentity
from konokashi.domain.lyrics import (
    LyricsResolutionResult,
    LyricsResolutionStatus,
    RepresentationKind,
)
from konokashi.domain.models import PlayerListResult
from konokashi.domain.representations import (
    EffectiveRepresentationLine,
    RepresentationDisplaySettings,
)
from konokashi.domain.synchronization import LyricDocumentTiming
from konokashi.domain.tracks import (
    Confidence,
    PlayerAssessment,
    PlayerSelectionResult,
    ResolvedTrack,
    TrackCandidate,
)
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

    def select(self, players, config):  # type: ignore[no-untyped-def]
        self.config = config
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

    def effective_lines(self, lyric_document, kind):  # type: ignore[no-untyped-def]
        line = lyric_document.representations[0].lines[0]
        text = "romaji" if kind is RepresentationKind.ROMANIZED else None
        return (
            EffectiveRepresentationLine(line, kind, text, None, None, None, None, None),
        )


class _Settings:
    def __init__(self) -> None:
        from konokashi.domain.tracks import PlayerSelectionConfig

        self.player = PlayerSelectionConfig(preferred_players=("strawberry",))
        self.display = RepresentationDisplaySettings(True, True, False)
        self.interactions = DesktopInteractionSettings()

    def get_player_selection(self):  # type: ignore[no-untyped-def]
        return self.player

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

    selected = service.select_track(PlayerListResult())
    assert selected.selected is not None
    assert selected.selected.track is track
    assert selection.config == settings.player

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
    assert representations.generated == [bundle.resolution.document.document_id]  # type: ignore[union-attr]

    changed = RepresentationDisplaySettings(False, True, True)
    service.put_display_settings(changed)
    assert settings.display == changed
    interactions = DesktopInteractionSettings(True)
    service.put_interaction_settings(interactions)
    assert settings.interactions == interactions
    service.cancel_inflight()
    assert cancellations == ["cancelled"]
