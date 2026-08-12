"""Derive typed source identities without modifying raw MPRIS evidence."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit, urlunsplit

from lyricflow.application.ports import LocalPathCanonicalizerPort
from lyricflow.domain.identity import (
    GenericMprisIdentity,
    LocalFileIdentity,
    SourceIdentity,
    YouTubeIdentity,
)
from lyricflow.domain.models import PlayerSnapshot

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
_YOUTUBE_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


@dataclass(frozen=True, slots=True)
class SourceResolution:
    """Identity extraction result and its diagnostic evidence."""

    identity: SourceIdentity
    evidence: tuple[str, ...]
    warnings: tuple[str, ...]


def _valid_youtube_id(value: str) -> bool:
    return len(value) == 11 and all(
        character in _YOUTUBE_ID_CHARS for character in value
    )


def extract_youtube_video_id(url: str) -> str | None:
    """Extract a video ID from common official YouTube URL forms."""

    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/", 1)[0]
        return candidate if _valid_youtube_id(candidate) else None
    if host not in _YOUTUBE_HOSTS:
        return None
    path_parts = tuple(part for part in parsed.path.split("/") if part)
    if parsed.path.rstrip("/") == "/watch":
        query_items = parsed.query.split("&")
        values = [unquote(item[2:]) for item in query_items if item.startswith("v=")]
        candidate = values[0] if values else ""
    elif len(path_parts) >= 2 and path_parts[0] in {"embed", "shorts", "live"}:
        candidate = path_parts[1]
    else:
        return None
    return candidate if _valid_youtube_id(candidate) else None


def _generic_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, "")
    )


class SourceIdentityResolver:
    """Resolve source identity through pure URL parsing and a filesystem port."""

    def __init__(self, local_paths: LocalPathCanonicalizerPort) -> None:
        self._local_paths = local_paths

    def resolve(self, snapshot: PlayerSnapshot) -> SourceResolution:
        """Derive the strongest safe identity available for a raw snapshot."""

        url = snapshot.metadata.url
        if url:
            video_id = extract_youtube_video_id(url)
            if video_id is not None:
                return SourceResolution(
                    YouTubeIdentity(video_id),
                    ("extracted stable YouTube video ID from media URL",),
                    (),
                )

            try:
                parsed = urlsplit(url)
            except ValueError:
                parsed = None
            if parsed is not None and parsed.scheme.lower() == "file":
                return self._resolve_local_file(snapshot, parsed.netloc, parsed.path)
            if parsed is not None and (parsed.hostname or "").lower() in (
                _YOUTUBE_HOSTS | {"youtu.be"}
            ):
                warning = "YouTube URL did not contain a valid 11-character video ID"
            else:
                warning = "generic MPRIS identity is session-only and not approval-safe"
        else:
            warning = "no media URL; generic MPRIS identity is session-only"

        return SourceResolution(
            GenericMprisIdentity(
                service_name=snapshot.service_name,
                track_id=snapshot.metadata.track_id,
                media_url=_generic_url(url),
            ),
            ("used limited generic MPRIS fallback identity",),
            (warning,),
        )

    def _resolve_local_file(
        self,
        snapshot: PlayerSnapshot,
        authority: str,
        encoded_path: str,
    ) -> SourceResolution:
        warnings = ["local path identity does not survive a file rename or move"]
        if authority not in {"", "localhost"}:
            return SourceResolution(
                GenericMprisIdentity(
                    snapshot.service_name,
                    snapshot.metadata.track_id,
                    snapshot.metadata.url,
                ),
                ("rejected non-local file URL authority",),
                (
                    f"file URL authority {authority!r} is not local; identity is "
                    "session-only",
                ),
            )

        decoded_path = unquote(encoded_path)
        resolution = self._local_paths.canonicalize(decoded_path)
        evidence = [
            "decoded file URL",
            "canonicalized local path without hashing audio",
        ]
        if resolution.symlink_resolved:
            evidence.append("resolved existing symlink components")
        if resolution.exists is False:
            warnings.append("local file is currently missing or inaccessible")
        elif resolution.exists is None:
            warnings.append("local file accessibility could not be determined")
        if resolution.warning:
            warnings.append(resolution.warning)
        return SourceResolution(
            LocalFileIdentity(resolution.canonical_path),
            tuple(evidence),
            tuple(warnings),
        )
