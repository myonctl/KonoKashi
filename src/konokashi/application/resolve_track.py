"""Resolve raw MPRIS metadata into an explainable track candidate."""

from __future__ import annotations

from dataclasses import replace

from konokashi.application.ports import TrackOverrideRepositoryPort
from konokashi.application.source_identity import SourceIdentityResolver
from konokashi.domain.identity import (
    GenericMprisIdentity,
    LocalFileIdentity,
    YouTubeIdentity,
)
from konokashi.domain.models import PlayerSnapshot
from konokashi.domain.normalization import (
    comparison_key,
    normalize_artist,
    normalize_text,
    parse_artist_credits,
    parse_topic_channel_label,
    parse_youtube_title_candidates,
    parse_youtube_title_hypotheses,
)
from konokashi.domain.tracks import (
    ApprovedTrackIdentity,
    Confidence,
    ResolvedTrack,
    TrackCandidate,
    semantic_duration_us,
)

MAX_YOUTUBE_INTERPRETATIONS = 12
_BROWSER_DESKTOP_ENTRIES = frozenset(
    {
        "chromium",
        "chromium-browser",
        "firefox",
        "org.mozilla.firefox",
        "google-chrome",
        "brave-browser",
        "vivaldi-stable",
    }
)
_BROWSER_SERVICE_NAMES = frozenset(
    {*_BROWSER_DESKTOP_ENTRIES, "plasma-browser-integration"}
)


def is_url_less_browser(snapshot: PlayerSnapshot) -> bool:
    """Recognize only browser MPRIS services when no source URL was reported."""

    if snapshot.metadata.url:
        return False
    desktop_entry = (snapshot.desktop_entry or "").casefold()
    service_name = snapshot.service_name.casefold()
    return desktop_entry in _BROWSER_DESKTOP_ENTRIES or any(
        service_name == name or service_name.startswith(f"{name}.")
        for name in _BROWSER_SERVICE_NAMES
    )


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
        elif isinstance(source.identity, GenericMprisIdentity) and is_url_less_browser(
            snapshot
        ):
            interpretations = self._url_less_browser_candidates(snapshot)
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
        evidence = source.evidence + automatic_candidate.evidence
        track = ResolvedTrack(
            snapshot,
            source.identity,
            automatic_candidate,
            automatic_confidence,
            evidence,
            source.warnings + automatic_warnings,
            interpretation_candidates=interpretations,
        )
        return (
            track if approved is None else with_approved_track_identity(track, approved)
        )

    def _reported_candidate(
        self, snapshot: PlayerSnapshot, *, is_local: bool = False
    ) -> TrackCandidate:
        metadata = snapshot.metadata
        duration_us = semantic_duration_us(metadata.duration_us)
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
            duration_us=duration_us,
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
                    (("duration", "mpris-duration"), duration_us is not None),
                )
                if present
            ),
        )

    def _url_less_browser_candidates(
        self, snapshot: PlayerSnapshot
    ) -> tuple[TrackCandidate, ...]:
        """Parse a clear browser title without claiming a YouTube video identity.

        Browser MPRIS can expose title, uploader, and duration without a media
        URL. Only the conventional artist-first title interpretation may support
        automatic matching; the reverse guess and raw uploader remain Low.
        """

        title = snapshot.metadata.title
        parsed = parse_youtube_title_candidates(title, ()) if title else ()
        reported = self._reported_candidate(snapshot)
        if not parsed:
            return (
                replace(
                    reported,
                    evidence=(
                        *reported.evidence,
                        "browser reported artist may be an uploader, "
                        "not a musical artist",
                    ),
                    identity_confidence=Confidence.LOW,
                ),
            )
        hypotheses = parse_youtube_title_hypotheses(title, ()) if title else ()
        interpreted = self._youtube_parsed_candidates(
            snapshot, (parsed[0], *hypotheses)
        )
        reported_artists = snapshot.metadata.artists or ()
        topic_label = (
            parse_topic_channel_label(reported_artists[0])
            if len(reported_artists) == 1
            else None
        )
        parsed_credit = interpreted[0].artist_credit
        parsed_artists = (
            parsed_credit.main_artists
            if parsed_credit is not None
            else interpreted[0].artists
        )
        topic_conflict = topic_label is not None and comparison_key(
            topic_label
        ) not in {comparison_key(artist) for artist in parsed_artists}
        candidates = tuple(
            replace(
                item,
                evidence=(
                    *(
                        "artist/title parsed from browser media title"
                        if evidence == "artist/title parsed from video title"
                        else evidence
                        for evidence in item.evidence
                    ),
                    "browser MPRIS omitted the media URL; source remains unconfirmed",
                    *(
                        (
                            "Topic channel conflicts with parsed artist; "
                            "await source confirmation",
                        )
                        if index == 0 and topic_conflict
                        else ()
                    ),
                ),
                strategy=item.strategy.replace("youtube-title:", "browser-title:", 1),
                identity_confidence=(
                    Confidence.MEDIUM
                    if index == 0 and not topic_conflict
                    else Confidence.LOW
                ),
            )
            for index, item in enumerate(interpreted)
        )
        return (
            *candidates,
            replace(
                reported,
                evidence=(
                    *reported.evidence,
                    "preserved raw browser MPRIS title and reported uploader",
                ),
                identity_confidence=Confidence.LOW,
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
        hypotheses = (
            parse_youtube_title_hypotheses(metadata.title, metadata.artists)
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
            unstructured = TrackCandidate(
                title=reported.title,
                artists=(),
                album=reported.album,
                duration_us=reported.duration_us,
                evidence=(
                    *uploader_evidence,
                    "video title did not contain unambiguous artist/title separator",
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
            )
            return (
                unstructured,
                *self._youtube_parsed_candidates(snapshot, hypotheses),
            )
        return self._youtube_parsed_candidates(
            snapshot, (*parsed_candidates, *hypotheses)
        )

    def _youtube_parsed_candidates(
        self, snapshot: PlayerSnapshot, parsed_candidates: tuple[TrackCandidate, ...]
    ) -> tuple[TrackCandidate, ...]:
        metadata = snapshot.metadata
        duration_us = semantic_duration_us(metadata.duration_us)
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
                duration_us=duration_us,
                evidence=parsed.evidence + uploader_evidence,
                transformations=parsed.transformations,
                strategy=parsed.strategy,
                artist_credit=parsed.artist_credit,
                identity_confidence=parsed.identity_confidence,
                field_provenance=(
                    *parsed.field_provenance,
                    *((("album", "mpris-album"),) if metadata.album else ()),
                    *(
                        (("duration", "mpris-duration"),)
                        if duration_us is not None
                        else ()
                    ),
                    *((("uploader", "mpris-artists"),) if metadata.artists else ()),
                ),
            )
            for parsed in parsed_candidates[:MAX_YOUTUBE_INTERPRETATIONS]
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
        if isinstance(source_identity, GenericMprisIdentity) and is_url_less_browser(
            snapshot
        ):
            if candidate.strategy == "reported-mpris":
                warnings.append("browser reported artist may be an uploader")
                return Confidence.LOW, tuple(warnings)
            if candidate.identity_confidence is Confidence.LOW:
                warnings.append("browser recording interpretation lacks corroboration")
                return Confidence.LOW, tuple(warnings)
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
        if candidate.duration_us is None:
            warnings.append("duration is missing")
        return Confidence.MEDIUM, tuple(warnings)


def with_youtube_metadata_candidates(
    track: ResolvedTrack, candidates: tuple[TrackCandidate, ...]
) -> ResolvedTrack:
    """Add bounded metadata interpretations without replacing playback evidence.

    Metadata extracted from a public video's title or description is useful
    enough to query providers, but is not a user-approved recording identity.
    Its confidence is independent of an inadequate first MPRIS interpretation.
    """

    if not isinstance(track.source_identity, YouTubeIdentity):
        raise ValueError("YouTube metadata requires a confirmed video identity")
    return _with_metadata_candidates(track, candidates)


def with_discovered_web_metadata_candidates(
    track: ResolvedTrack,
    candidates: tuple[TrackCandidate, ...],
    *,
    correction_identity_hint: YouTubeIdentity | None = None,
) -> ResolvedTrack:
    """Add search-discovered hypotheses without upgrading session-only identity."""

    if not isinstance(
        track.source_identity, GenericMprisIdentity
    ) or not is_url_less_browser(track.raw_snapshot):
        raise ValueError("web discovery requires a URL-less browser source")
    enriched = _with_metadata_candidates(track, candidates)
    if correction_identity_hint is None or not candidates:
        return enriched
    return replace(
        enriched,
        correction_identity_hint=correction_identity_hint,
        evidence=tuple(
            dict.fromkeys(
                (
                    *enriched.evidence,
                    "unique searched public-video ID may scope explicit user "
                    "corrections; playback source remains session-only",
                )
            )
        ),
    )


def with_approved_track_identity(
    track: ResolvedTrack, approved: ApprovedTrackIdentity
) -> ResolvedTrack:
    """Apply one explicit correction without replacing raw playback evidence."""

    automatic_candidate = track.automatic_candidate or track.candidate
    automatic_confidence = track.automatic_confidence or track.confidence
    candidate = TrackCandidate(
        title=approved.title,
        artists=approved.artists,
        album=(
            approved.album
            if approved.album is not None
            else track.raw_snapshot.metadata.album
        ),
        duration_us=semantic_duration_us(track.raw_snapshot.metadata.duration_us),
        evidence=("user-approved correction for durable correction identity",),
        strategy="user-correction",
        artist_credit=parse_artist_credits(approved.artists),
        field_provenance=(
            ("title", "user-correction"),
            ("artists", "user-correction"),
        ),
    )
    interpretations = tuple(
        item
        for item in (track.interpretation_candidates or (automatic_candidate,))
        if item != candidate
    )
    return replace(
        track,
        candidate=candidate,
        confidence=Confidence.APPROVED,
        evidence=tuple(dict.fromkeys((*track.evidence, *candidate.evidence))),
        user_approved=True,
        automatic_candidate=automatic_candidate,
        automatic_confidence=automatic_confidence,
        interpretation_candidates=(candidate, *interpretations),
    )


def _with_metadata_candidates(
    track: ResolvedTrack, candidates: tuple[TrackCandidate, ...]
) -> ResolvedTrack:
    interpretations = list(track.interpretation_candidates or (track.candidate,))
    positions = {
        (
            comparison_key(item.title or ""),
            tuple(comparison_key(artist) for artist in item.artists),
            item.duration_us,
        ): index
        for index, item in enumerate(interpretations)
    }
    for item in candidates:
        key = (
            comparison_key(item.title or ""),
            tuple(comparison_key(artist) for artist in item.artists),
            item.duration_us,
        )
        if not item.title or not item.artists:
            continue
        existing_index = positions.get(key)
        if existing_index is not None:
            existing = interpretations[existing_index]
            if existing.identity_confidence is Confidence.LOW:
                interpretations[existing_index] = replace(
                    item,
                    evidence=tuple(dict.fromkeys((*existing.evidence, *item.evidence))),
                    transformations=tuple(
                        dict.fromkeys(
                            (*existing.transformations, *item.transformations)
                        )
                    ),
                    field_provenance=tuple(
                        dict.fromkeys(
                            (*existing.field_provenance, *item.field_provenance)
                        )
                    ),
                    identity_confidence=Confidence.MEDIUM,
                )
            continue
        if len(interpretations) >= MAX_YOUTUBE_INTERPRETATIONS:
            continue
        positions[key] = len(interpretations)
        interpretations.append(replace(item, identity_confidence=Confidence.MEDIUM))
    return replace(track, interpretation_candidates=tuple(interpretations))
