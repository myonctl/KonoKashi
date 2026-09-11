"""Frontend-neutral Stage 8 review, correction, reset, and audit use cases."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from konokashi.application.ports import (
    LyricsCandidateDocumentPort,
    LyricsMatchRepositoryPort,
    LyricsRepositoryPort,
    TimingCalibrationRepositoryPort,
    TrackOverrideRepositoryPort,
)
from konokashi.domain.identity import (
    PersistenceScope,
    SourceIdentity,
    YouTubeIdentity,
)
from konokashi.domain.lyric_corrections import LyricEditorSnapshot
from konokashi.domain.lyrics import (
    ApprovalState,
    ContentProvenance,
    LyricDocument,
    LyricsAlternative,
    LyricsAlternativeResult,
    LyricsMatch,
    LyricsMatchConfidence,
    LyricsMatchDecision,
    LyricsResolutionResult,
)
from konokashi.domain.representations import (
    LanguageRoutingStatus,
    RepresentationLayerStatus,
)
from konokashi.domain.synchronization import LyricDocumentTiming
from konokashi.domain.tracks import ApprovedTrackIdentity, ResolvedTrack


class ReviewCorrectionError(ValueError):
    """A controlled invalid or unavailable correction request."""


@dataclass(frozen=True, slots=True)
class TrackAuditEvidence:
    """Raw, automatic, and effective track values kept visibly separate."""

    raw_title: str | None
    raw_artists: tuple[str, ...] | None
    raw_album: str | None
    raw_url: str | None
    raw_duration_us: int | None
    automatic_title: str | None
    automatic_artists: tuple[str, ...]
    automatic_album: str | None
    automatic_confidence: str
    effective_title: str | None
    effective_artists: tuple[str, ...]
    effective_album: str | None
    effective_confidence: str
    transformations: tuple[str, ...] = field(default_factory=tuple)
    evidence: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class TranslationReviewLine:
    """One exact original-line mapping for local translation authoring."""

    source_line_id: str
    original_text: str
    translated_text: str | None = None
    provenance: ContentProvenance | None = None
    approval_state: ApprovalState | None = None
    diagnostics: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ReviewCorrectionSnapshot:
    """One source-bound review model shared by desktop and future TUI surfaces."""

    source_identity: SourceIdentity
    durable: bool
    track: TrackAuditEvidence
    has_track_override: bool
    current_document_id: str | None
    current_lyrics_source: str | None
    current_provider_title: str | None
    current_provider_artist: str | None
    current_provider_album: str | None
    current_provider_duration_ms: int | None
    current_match_decision: LyricsMatchDecision | None
    current_match_confidence: LyricsMatchConfidence | None
    current_match_evidence: tuple[str, ...]
    display_delay_us: int
    alternatives: tuple[LyricsAlternative, ...] = field(default_factory=tuple)
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    search_title: str | None = None
    search_artists: tuple[str, ...] = field(default_factory=tuple)
    youtube_enrichment_available: bool = False
    routing_status: LanguageRoutingStatus | None = None
    routing_language: str | None = None
    routing_diagnostic: str | None = None
    language_override: str | None = None
    layer_statuses: tuple[RepresentationLayerStatus, ...] = field(default_factory=tuple)
    translation_lines: tuple[TranslationReviewLine, ...] = field(default_factory=tuple)
    lyric_editor: LyricEditorSnapshot | None = None


class ReviewCorrectionService:
    """Apply deliberate user choices without modifying raw media or provider data."""

    def __init__(
        self,
        *,
        track_overrides: TrackOverrideRepositoryPort,
        lyrics: LyricsRepositoryPort,
        matches: LyricsMatchRepositoryPort,
        provider_documents: LyricsCandidateDocumentPort,
        timing: TimingCalibrationRepositoryPort,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._track_overrides = track_overrides
        self._lyrics = lyrics
        self._matches = matches
        self._provider_documents = provider_documents
        self._timing = timing
        self._now = now or (lambda: datetime.now(UTC))

    def snapshot(
        self,
        track: ResolvedTrack,
        resolution: LyricsResolutionResult,
        alternatives: LyricsAlternativeResult,
    ) -> ReviewCorrectionSnapshot:
        """Build bounded audit evidence for the exact source currently reviewed."""

        self._require_same_source(track, resolution.source_identity)
        self._require_same_source(track, alternatives.source_identity)
        automatic = track.automatic_candidate or track.candidate
        automatic_confidence = track.automatic_confidence or track.confidence
        raw = track.raw_snapshot.metadata
        match = self._matches.get(track.source_identity)
        document_id = (
            None if resolution.document is None else resolution.document.document_id
        )
        timing = (
            None
            if document_id is None
            else self._timing.get_document_timing(document_id)
        )
        return ReviewCorrectionSnapshot(
            source_identity=track.source_identity,
            durable=(
                track.source_identity.persistence_scope is PersistenceScope.PERMANENT
            ),
            track=TrackAuditEvidence(
                raw_title=raw.title,
                raw_artists=raw.artists,
                raw_album=raw.album,
                raw_url=raw.url,
                raw_duration_us=raw.duration_us,
                automatic_title=automatic.title,
                automatic_artists=automatic.artists,
                automatic_album=automatic.album,
                automatic_confidence=automatic_confidence.value,
                effective_title=track.candidate.title,
                effective_artists=track.candidate.artists,
                effective_album=track.candidate.album,
                effective_confidence=track.confidence.value,
                transformations=automatic.transformations,
                evidence=track.evidence,
                warnings=track.warnings,
            ),
            has_track_override=self._track_overrides.get(track.source_identity)
            is not None,
            current_document_id=document_id,
            current_lyrics_source=(
                resolution.source_label
                if resolution.source_label is not None
                else (
                    None
                    if resolution.document is None
                    else resolution.document.source_name
                )
            ),
            current_provider_title=(
                None
                if resolution.document is None
                else resolution.document.source_title
            ),
            current_provider_artist=(
                None
                if resolution.document is None
                else resolution.document.source_artist
            ),
            current_provider_album=(
                None
                if resolution.document is None
                else resolution.document.source_album
            ),
            current_provider_duration_ms=(
                None if resolution.document is None else resolution.document.duration_ms
            ),
            current_match_decision=None if match is None else match.decision,
            current_match_confidence=(
                resolution.confidence
                if resolution.confidence is not None
                else (None if match is None else match.confidence)
            ),
            current_match_evidence=(
                resolution.evidence
                if resolution.evidence
                else (() if match is None else match.evidence)
            ),
            display_delay_us=(0 if timing is None else timing.lyrics_display_delay_us),
            alternatives=alternatives.alternatives,
            diagnostics=(*resolution.diagnostics, *alternatives.diagnostics),
            search_title=alternatives.search_title,
            search_artists=alternatives.search_artists,
            youtube_enrichment_available=isinstance(
                track.source_identity, YouTubeIdentity
            ),
        )

    def put_track_override(
        self,
        track: ResolvedTrack,
        *,
        title: str,
        artists: tuple[str, ...],
        album: str | None,
    ) -> None:
        """Approve one corrected identity while retaining the raw snapshot."""

        self._require_durable(track)
        cleaned_title = title.strip()
        cleaned_artists = tuple(artist.strip() for artist in artists if artist.strip())
        if not cleaned_title or not cleaned_artists:
            raise ReviewCorrectionError(
                "a corrected track requires a non-blank title and artist"
            )
        self._track_overrides.put(
            track.source_identity,
            ApprovedTrackIdentity(
                cleaned_title,
                cleaned_artists,
                None if album is None else album.strip() or None,
            ),
        )

    def reset_track_override(self, track: ResolvedTrack) -> bool:
        """Reset only the source-identity correction."""

        self._require_durable(track)
        return self._track_overrides.delete(track.source_identity)

    def approve_current(
        self, track: ResolvedTrack, resolution: LyricsResolutionResult
    ) -> None:
        """Approve the current document without altering its provider content."""

        self._require_durable(track)
        document = self._require_current_document(track, resolution)
        evidence = tuple(
            dict.fromkeys((*resolution.evidence, "explicitly approved by the user"))
        )
        self._matches.approve(
            track.source_identity,
            LyricsMatch(
                document.document_id,
                LyricsMatchDecision.APPROVED,
                ContentProvenance.USER,
                self._now(),
                LyricsMatchConfidence.APPROVED,
                evidence,
            ),
        )

    def reject_current(
        self, track: ResolvedTrack, resolution: LyricsResolutionResult
    ) -> None:
        """Reject the current document so automatic resolution skips it."""

        self._require_durable(track)
        document = self._require_current_document(track, resolution)
        evidence = tuple(
            dict.fromkeys((*resolution.evidence, "explicitly rejected by the user"))
        )
        rejected = LyricsMatch(
            document.document_id,
            LyricsMatchDecision.REJECTED,
            ContentProvenance.USER,
            self._now(),
            resolution.confidence or LyricsMatchConfidence.LOW,
            evidence,
        )
        self._matches.reject(track.source_identity, rejected)

    def choose_alternative(
        self, track: ResolvedTrack, alternative: LyricsAlternative
    ) -> None:
        """Build and approve one explicitly selected provider alternative."""

        self._require_durable(track)
        expected_id = self._provider_documents.document_id(alternative.candidate)
        if expected_id != alternative.document_id:
            raise ReviewCorrectionError("alternative document identity is inconsistent")
        document, diagnostics = self._provider_documents.build(
            alternative.candidate, self._now()
        )
        if document is None:
            detail = "; ".join(diagnostics) or "candidate has no usable lyric content"
            raise ReviewCorrectionError(f"selected alternative is invalid: {detail}")
        self._lyrics.put(document)
        self._matches.approve(
            track.source_identity,
            LyricsMatch(
                document.document_id,
                LyricsMatchDecision.APPROVED,
                ContentProvenance.USER,
                self._now(),
                LyricsMatchConfidence.APPROVED,
                tuple(
                    dict.fromkeys(
                        (*alternative.evidence, "explicitly selected by the user")
                    )
                ),
            ),
        )

    def reject_alternative(
        self, track: ResolvedTrack, alternative: LyricsAlternative
    ) -> None:
        """Reject one review candidate without replacing a usable current match."""

        self._require_durable(track)
        expected_id = self._provider_documents.document_id(alternative.candidate)
        if expected_id != alternative.document_id:
            raise ReviewCorrectionError("alternative document identity is inconsistent")
        document, diagnostics = self._provider_documents.build(
            alternative.candidate, self._now()
        )
        if document is None:
            detail = "; ".join(diagnostics) or "candidate has no usable lyric content"
            raise ReviewCorrectionError(f"selected alternative is invalid: {detail}")
        self._lyrics.put(document)
        self._matches.put_rejection(
            track.source_identity,
            LyricsMatch(
                document.document_id,
                LyricsMatchDecision.REJECTED,
                ContentProvenance.USER,
                self._now(),
                alternative.confidence,
                tuple(
                    dict.fromkeys(
                        (*alternative.evidence, "explicitly rejected by the user")
                    )
                ),
            ),
        )

    def reset_match(self, track: ResolvedTrack) -> bool:
        """Reset current and rejected recording-to-document preferences."""

        self._require_durable(track)
        return self._matches.reset(track.source_identity)

    def set_display_delay(
        self,
        track: ResolvedTrack,
        resolution: LyricsResolutionResult,
        delay_us: int,
    ) -> None:
        """Set one exact document delay without modifying provider timestamps."""

        self._require_durable(track)
        document = self._require_current_document(track, resolution)
        self._timing.put_document_timing(
            LyricDocumentTiming(document.document_id, delay_us)
        )

    def reset_display_delay(
        self, track: ResolvedTrack, resolution: LyricsResolutionResult
    ) -> bool:
        """Reset only one exact document delay."""

        self._require_durable(track)
        document = self._require_current_document(track, resolution)
        return self._timing.delete_document_timing(document.document_id)

    @staticmethod
    def _require_same_source(track: ResolvedTrack, source: SourceIdentity) -> None:
        if track.source_identity != source:
            raise ReviewCorrectionError("review result belongs to another source")

    @staticmethod
    def _require_durable(track: ResolvedTrack) -> None:
        if track.source_identity.persistence_scope is not PersistenceScope.PERMANENT:
            raise ReviewCorrectionError(
                "session-only sources cannot receive durable corrections"
            )

    @classmethod
    def _require_current_document(
        cls, track: ResolvedTrack, resolution: LyricsResolutionResult
    ) -> LyricDocument:
        cls._require_same_source(track, resolution.source_identity)
        if resolution.document is None:
            raise ReviewCorrectionError("there is no current lyric document to review")
        return resolution.document
