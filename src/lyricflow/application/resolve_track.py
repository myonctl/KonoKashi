"""Resolve raw MPRIS metadata into an explainable track candidate."""

from __future__ import annotations

from lyricflow.application.ports import TrackOverrideRepositoryPort
from lyricflow.application.source_identity import SourceIdentityResolver
from lyricflow.domain.identity import LocalFileIdentity, YouTubeIdentity
from lyricflow.domain.models import PlayerSnapshot
from lyricflow.domain.normalization import (
    comparison_key,
    normalize_artist,
    normalize_text,
    parse_youtube_title,
)
from lyricflow.domain.tracks import Confidence, ResolvedTrack, TrackCandidate


class TrackResolver:
    """Apply source-aware candidate and confidence policy."""

    def __init__(
        self,
        sources: SourceIdentityResolver,
        overrides: TrackOverrideRepositoryPort,
    ) -> None:
        self._sources = sources
        self._overrides = overrides

    def resolve(self, snapshot: PlayerSnapshot) -> ResolvedTrack:
        """Resolve one snapshot while retaining the entire raw snapshot."""

        source = self._sources.resolve(snapshot)
        approved = self._overrides.get(source.identity)
        if approved is not None:
            candidate = TrackCandidate(
                title=approved.title,
                artists=approved.artists,
                album=(
                    approved.album
                    if approved.album is not None
                    else snapshot.metadata.album
                ),
                duration_us=snapshot.metadata.duration_us,
                evidence=("user-approved correction for stable source identity",),
            )
            return ResolvedTrack(
                snapshot,
                source.identity,
                candidate,
                Confidence.APPROVED,
                source.evidence + candidate.evidence,
                source.warnings,
                user_approved=True,
            )

        if isinstance(source.identity, YouTubeIdentity):
            candidate = self._youtube_candidate(snapshot)
        else:
            candidate = self._reported_candidate(
                snapshot, is_local=isinstance(source.identity, LocalFileIdentity)
            )
        confidence, warnings = self._confidence(snapshot, candidate, source.identity)
        evidence = source.evidence + candidate.evidence
        return ResolvedTrack(
            snapshot,
            source.identity,
            candidate,
            confidence,
            evidence,
            source.warnings + warnings,
        )

    def _reported_candidate(
        self, snapshot: PlayerSnapshot, *, is_local: bool = False
    ) -> TrackCandidate:
        metadata = snapshot.metadata
        transformations: list[str] = []
        title: str | None = None
        if metadata.title and metadata.title.strip():
            normalized_title = normalize_text(metadata.title)
            title = normalized_title.value
            transformations.extend(normalized_title.transformations)
        artists: list[str] = []
        for raw_artist in metadata.artists or ():
            if raw_artist.strip():
                normalized_artist = normalize_artist(raw_artist)
                artists.append(normalized_artist.value)
                transformations.extend(normalized_artist.transformations)
        evidence = ["used reported MPRIS metadata"]
        if is_local:
            evidence.append("preferred high-quality local MPRIS tags")
        return TrackCandidate(
            title=title,
            artists=tuple(artists),
            album=metadata.album,
            duration_us=metadata.duration_us,
            evidence=tuple(evidence),
            transformations=tuple(dict.fromkeys(transformations)),
        )

    def _youtube_candidate(self, snapshot: PlayerSnapshot) -> TrackCandidate:
        metadata = snapshot.metadata
        parsed = (
            parse_youtube_title(metadata.title, metadata.artists)
            if metadata.title
            else None
        )
        if parsed is None:
            reported = self._reported_candidate(snapshot)
            uploader_evidence = tuple(
                f"preserved reported artist as uploader evidence: {artist}"
                for artist in metadata.artists or ()
                if artist.strip()
            )
            return TrackCandidate(
                title=reported.title,
                artists=(),
                album=reported.album,
                duration_us=reported.duration_us,
                evidence=(
                    *uploader_evidence,
                    "video title did not contain unambiguous artist/title separator",
                ),
                transformations=reported.transformations,
            )
        uploader_evidence = tuple(
            f"preserved reported artist as uploader evidence: {artist}"
            for artist in metadata.artists or ()
            if artist.strip()
        )
        return TrackCandidate(
            title=parsed.title,
            artists=parsed.artists,
            album=metadata.album,
            duration_us=metadata.duration_us,
            evidence=parsed.evidence + uploader_evidence,
            transformations=parsed.transformations,
        )

    @staticmethod
    def _confidence(
        snapshot: PlayerSnapshot,
        candidate: TrackCandidate,
        source_identity: object,
    ) -> tuple[Confidence, tuple[str, ...]]:
        warnings: list[str] = []
        if not candidate.title:
            warnings.append("resolved title is missing")
        if not candidate.artists:
            warnings.append("resolved musical artist is missing")
        if not candidate.title or not candidate.artists:
            return Confidence.LOW, tuple(warnings)
        if isinstance(source_identity, LocalFileIdentity):
            return Confidence.HIGH, tuple(warnings)
        if isinstance(source_identity, YouTubeIdentity) and any(
            evidence == "artist/title parsed from video title"
            for evidence in candidate.evidence
        ):
            return Confidence.HIGH, tuple(warnings)
        if " - " in candidate.title:
            possible_artist = candidate.title.split(" - ", 1)[0]
            reported_artist_keys = {
                comparison_key(artist) for artist in candidate.artists
            }
            if comparison_key(possible_artist) not in reported_artist_keys:
                warnings.append("title-contained artist conflicts with reported artist")
                return Confidence.LOW, tuple(warnings)
        if snapshot.metadata.duration_us is None:
            warnings.append("duration is missing")
        return Confidence.MEDIUM, tuple(warnings)
