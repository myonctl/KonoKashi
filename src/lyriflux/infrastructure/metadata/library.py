"""Read-only filesystem and Mutagen adapters for Stage 9 library scans."""

from __future__ import annotations

import os
import re
import stat as stat_module
from hashlib import sha256
from pathlib import Path

import mutagen

from lyriflux.domain.library import (
    LibraryFile,
    LibraryMetadata,
    LibraryMetadataSource,
)
from lyriflux.domain.tracks import Confidence, TrackCandidate

_AUDIO_SUFFIXES = frozenset(
    {
        ".aac",
        ".aiff",
        ".alac",
        ".ape",
        ".flac",
        ".m4a",
        ".mp3",
        ".ogg",
        ".opus",
        ".wav",
        ".wma",
    }
)
_TRACK_PREFIX = re.compile(r"^\s*(?:\d{1,3}[ ._-]+)")


def _file_key(stat: os.stat_result) -> str:
    """Hash filesystem identity so diagnostics never print device/inode values."""

    return sha256(f"{stat.st_dev}:{stat.st_ino}".encode()).hexdigest()


def _raise_walk_error(error: OSError) -> None:
    """Abort reconciliation when any subtree could not be enumerated safely."""

    raise error


class MusicDirectoryFilesystem:
    """Walk configured roots deterministically without following directory links."""

    def files(self, roots: tuple[str, ...]):  # type: ignore[no-untyped-def]
        for root_text in roots:
            root = Path(root_text)
            if not root.is_dir():
                raise OSError(f"configured library root is unavailable: {root}")
            for directory, names, filenames in os.walk(
                root, followlinks=False, onerror=_raise_walk_error
            ):
                names.sort()
                filenames.sort()
                for name in filenames:
                    path = Path(directory, name)
                    if path.suffix.lower() not in _AUDIO_SUFFIXES:
                        continue
                    try:
                        stat = path.stat(follow_symlinks=False)
                    except OSError as error:
                        raise OSError(
                            f"library file could not be inspected: {path}"
                        ) from error
                    if not stat_module.S_ISREG(stat.st_mode):
                        continue
                    yield LibraryFile(
                        _file_key(stat),
                        str(path.absolute()),
                        str(root.absolute()),
                        stat.st_size,
                        stat.st_mtime_ns,
                    )


def _first(tags: object, key: str) -> str | None:
    if tags is None or not hasattr(tags, "get"):
        return None
    values = tags.get(key)
    if not values:
        return None
    value = str(values[0]).strip()
    return value or None


def _filename_candidate(path: str) -> tuple[str | None, tuple[str, ...]]:
    stem = _TRACK_PREFIX.sub("", Path(path).stem).strip()
    if " - " in stem:
        artist, title = (part.strip() for part in stem.split(" - ", 1))
        if artist and title:
            return title, (artist,)
    return (stem or None), ()


class MutagenLibraryMetadataReader:
    """Read easy tags and duration; never invoke Mutagen's save methods."""

    def read(self, file: LibraryFile) -> LibraryMetadata:
        fallback_title, fallback_artists = _filename_candidate(file.path)
        try:
            audio = mutagen.File(file.path, easy=True)
        except (OSError, mutagen.MutagenError) as error:
            return LibraryMetadata(
                TrackCandidate(
                    fallback_title,
                    fallback_artists,
                    None,
                    None,
                    evidence=("derived metadata from the filename",),
                ),
                Confidence.MEDIUM if fallback_artists else Confidence.LOW,
                LibraryMetadataSource.FILENAME,
                f"tags could not be read: {error}",
            )
        tags = None if audio is None else audio.tags
        title = _first(tags, "title")
        artists: tuple[str, ...] = ()
        if tags is not None and hasattr(tags, "get"):
            raw_artists = tags.get("artist") or ()
            artists = tuple(
                value for item in raw_artists if (value := str(item).strip())
            )
        album = _first(tags, "album")
        duration_us = None
        info = None if audio is None else getattr(audio, "info", None)
        length = None if info is None else getattr(info, "length", None)
        if isinstance(length, (int, float)) and length > 0:
            duration_us = round(length * 1_000_000)
        used_fallback = False
        if title is None:
            title = fallback_title
            used_fallback = title is not None
        if not artists:
            artists = fallback_artists
            used_fallback = bool(artists) or used_fallback
        tag_complete = _first(tags, "title") is not None and bool(
            tags is not None and hasattr(tags, "get") and tags.get("artist")
        )
        confidence = (
            Confidence.HIGH
            if tag_complete
            else (Confidence.MEDIUM if title and artists else Confidence.LOW)
        )
        source = (
            LibraryMetadataSource.TAGS_AND_FILENAME
            if used_fallback and tags is not None
            else LibraryMetadataSource.FILENAME
            if tags is None
            else LibraryMetadataSource.TAGS
        )
        evidence = ["read local audio tags with Mutagen"] if tags is not None else []
        if used_fallback or tags is None:
            evidence.append("used a conservative filename fallback")
        diagnostic = None
        if confidence is not Confidence.HIGH:
            diagnostic = "complete title and artist tags were not available"
        return LibraryMetadata(
            TrackCandidate(
                title,
                artists,
                album,
                duration_us,
                evidence=tuple(evidence),
            ),
            confidence,
            source,
            diagnostic,
        )
