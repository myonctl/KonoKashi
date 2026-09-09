"""Bounded, lyric-free metadata enrichment values for YouTube sources."""

from __future__ import annotations

from dataclasses import dataclass, field

from konokashi.domain.identity import YouTubeIdentity
from konokashi.domain.tracks import TrackCandidate


@dataclass(frozen=True, slots=True)
class YouTubeMetadataEnrichmentResult:
    """Sanitized recording candidates extracted for one exact public video."""

    source_identity: YouTubeIdentity
    candidates: tuple[TrackCandidate, ...] = field(default_factory=tuple)
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    cache_hit: bool = False
    network_used: bool = False
