"""Typed source identities derived from raw player observations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias


class SourceKind(Enum):
    """Supported recording-source categories."""

    LOCAL_FILE = "local-file"
    YOUTUBE = "youtube"
    GENERIC_MPRIS = "generic-mpris"


class PersistenceScope(Enum):
    """How safely an identity can be reused after the current session."""

    PERMANENT = "permanent"
    SESSION_ONLY = "session-only"


@dataclass(frozen=True, slots=True)
class LocalFileIdentity:
    """A canonical local path, without synchronously hashing file contents."""

    canonical_path: str
    kind: SourceKind = SourceKind.LOCAL_FILE
    persistence_scope: PersistenceScope = PersistenceScope.PERMANENT


@dataclass(frozen=True, slots=True)
class YouTubeIdentity:
    """A stable YouTube video identifier independent of playlist parameters."""

    video_id: str
    kind: SourceKind = SourceKind.YOUTUBE
    persistence_scope: PersistenceScope = PersistenceScope.PERMANENT


@dataclass(frozen=True, slots=True)
class GenericMprisIdentity:
    """A deliberately session-scoped fallback for streams and unknown media."""

    service_name: str
    track_id: str | None
    media_url: str | None
    kind: SourceKind = SourceKind.GENERIC_MPRIS
    persistence_scope: PersistenceScope = PersistenceScope.SESSION_ONLY


SourceIdentity: TypeAlias = LocalFileIdentity | YouTubeIdentity | GenericMprisIdentity


def source_identity_value(identity: SourceIdentity) -> str:
    """Return the typed identity's human-readable value for diagnostics."""

    if isinstance(identity, LocalFileIdentity):
        return identity.canonical_path
    if isinstance(identity, YouTubeIdentity):
        return identity.video_id
    details = [identity.service_name]
    if identity.track_id:
        details.append(f"track={identity.track_id}")
    if identity.media_url:
        details.append(f"url={identity.media_url}")
    return "; ".join(details)
