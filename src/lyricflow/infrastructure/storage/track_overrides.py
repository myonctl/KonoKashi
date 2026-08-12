"""Non-persistent Stage 2 track-override repository."""

from __future__ import annotations

from lyricflow.domain.identity import SourceIdentity
from lyricflow.domain.tracks import ApprovedTrackIdentity


class InMemoryTrackOverrideRepository:
    """Keep approved corrections in memory behind the persistence port."""

    def __init__(self) -> None:
        self._entries: dict[SourceIdentity, ApprovedTrackIdentity] = {}

    def get(self, source_identity: SourceIdentity) -> ApprovedTrackIdentity | None:
        """Return one approved correction."""

        return self._entries.get(source_identity)

    def put(
        self,
        source_identity: SourceIdentity,
        approved_identity: ApprovedTrackIdentity,
    ) -> None:
        """Store one approved correction for this process lifetime."""

        self._entries[source_identity] = approved_identity
