"""Resolve raw MPRIS metadata into an explainable track candidate."""

from __future__ import annotations

from konokashi.application.ports import TrackOverrideRepositoryPort
from konokashi.application.source_identity import SourceIdentityResolver
from konokashi.domain.identity import LocalFileIdentity, YouTubeIdentity
from konokashi.domain.models import PlayerSnapshot
from konokashi.domain.normalization import (
    comparison_key,
    normalize_artist,
    normalize_text,
    parse_artist_credits,
    parse_youtube_title_candidates,
)
from konokashi.domain.tracks import Confidence, ResolvedTrack, TrackCandidate


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
        if isinstance(source.identity, YouTubeIdentity):
            interpretations = self._youtube_candidates(snapshot)
        else:
            interpretations = (
                self._reported_candidate(
                    snapshot, is_local=isinstance(source.identity, LocalFileIdentity)
                ),
            )
        automatic_candidate = interpretations[0]
        automatic_confidence, automatic_warnings = self._confidence(
            snapshot, automatic_candidate, source.identity
        )
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
                strategy="user-correction",
                artist_credit=parse_artist_credits(approved.artists),
                field_provenance=(
                    ("title", "user-correction"),
                    ("artists", "user-correction"),
                ),
            )
            return ResolvedTrack(
                snapshot,
                source.identity,
                candidate,
                Confidence.APPROVED,
                source.evidence + automatic_candidate.evidence + candidate.evidence,
                source.warnings + automatic_warnings,
                user_approved=True,
                automatic_candidate=automatic_candidate,
                automatic_confidence=automatic_confidence,
                interpretation_candidates=(candidate, *interpretations),
            )

        evidence = source.evidence + automatic_candidate.evidence
        return ResolvedTrack(
            snapshot,
            source.identity,
            automatic_candidate,
            automatic_confidence,
            evidence,
            source.warnings + automatic_warnings,
            interpretation_candidates=interpretations,
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
            strategy="reported-mpris",
            artist_credit=parse_artist_credits(tuple(artists)),
            field_provenance=tuple(
                item
                for item, present in (
                    (("title", "mpris-title"), title is not None),
                    (("artists", "mpris-artists"), bool(artists)),
                    (("album", "mpris-album"), metadata.album is not None),
                    (("duration", "mpris-duration"), metadata.duration_us is not None),
                )
                if present
            ),
        )

    def _youtube_candidates(
        self, snapshot: PlayerSnapshot
    ) -> tuple[TrackCandidate, ...]:
        metadata = snapshot.metadata
        parsed_candidates = (
            parse_youtube_title_candidates(metadata.title, metadata.artists)
            if metadata.title
            else ()
        )
        if not parsed_candidates:
            reported = self._reported_candidate(snapshot)
            uploader_evidence = tuple(
                f"preserved reported artist as uploader evidence: {artist}"
                for artist in metadata.artists or ()
                if artist.strip()
            )
            return (
                TrackCandidate(
                    title=reported.title,
                    artists=(),
                    album=reported.album,
                    duration_us=reported.duration_us,
                    evidence=(
                        *uploader_evidence,
                        "video title did not contain unambiguous "
                        "artist/title separator",
                    ),
                    transformations=reported.transformations,
                    strategy="youtube-unstructured-title",
                    field_provenance=(
                        ("title", "mpris-title"),
                        ("uploader", "mpris-artists"),
                        *(
                            (("duration", "mpris-duration"),)
                            if reported.duration_us
                            else ()
                        ),
                    ),
                ),
            )
        uploader_evidence = tuple(
            f"preserved reported artist as uploader evidence: {artist}"
            for artist in metadata.artists or ()
            if artist.strip()
        )
        return tuple(
            TrackCandidate(
                title=parsed.title,
                artists=parsed.artists,
                album=metadata.album,
                duration_us=metadata.duration_us,
                evidence=parsed.evidence + uploader_evidence,
                transformations=parsed.transformations,
                strategy=parsed.strategy,
                artist_credit=parsed.artist_credit,
                field_provenance=(
                    *parsed.field_provenance,
                    *((("album", "mpris-album"),) if metadata.album else ()),
                    *(
                        (("duration", "mpris-duration"),)
                        if metadata.duration_us
                        else ()
                    ),
                    *((("uploader", "mpris-artists"),) if metadata.artists else ()),
                ),
            )
            for parsed in parsed_candidates
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
