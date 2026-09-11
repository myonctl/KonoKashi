"""Read-only embedded Vorbis-comment lyrics behind a Mutagen boundary."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Protocol, cast, runtime_checkable

from mutagen import MutagenError

from konokashi.domain.identity import LocalFileIdentity
from konokashi.domain.lyrics import (
    ContentProvenance,
    LocalLyricsResult,
    LocalLyricsStatus,
    LyricsTextParseStatus,
    TimingProvenance,
)
from konokashi.domain.tracks import ResolvedTrack
from konokashi.infrastructure.lyrics.documents import build_lyric_document
from konokashi.infrastructure.lyrics.local_sidecar import _duration_ms
from konokashi.infrastructure.lyrics.lrc import MAX_LYRICS_TEXT_CHARS, parse_lyrics_text

EmbeddedLoader = Callable[[BinaryIO], object | None]
_SUPPORTED_FIELDS = ("syncedlyrics", "lyrics", "unsyncedlyrics")


@runtime_checkable
class _TagItems(Protocol):
    def items(self) -> Iterable[tuple[object, object]]:
        """Return tag key/value pairs without requiring a concrete mapping base."""


def _mutagen_load(stream: BinaryIO) -> object | None:
    from mutagen import File

    return cast(object | None, File(stream, easy=False))


class EmbeddedLyricsProvider:
    """Extract a bounded FLAC/Vorbis-style lyrics field without writing tags."""

    def __init__(
        self,
        loader: EmbeddedLoader = _mutagen_load,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._loader = loader
        self._now = now or (lambda: datetime.now(UTC))

    def load(self, track: ResolvedTrack) -> LocalLyricsResult:
        """Return embedded original lyrics or a controlled normal miss."""

        if not isinstance(track.source_identity, LocalFileIdentity):
            return LocalLyricsResult(LocalLyricsStatus.MISS, "Embedded audio metadata")
        path = Path(track.source_identity.canonical_path)
        descriptor: int | None = None
        try:
            if path.is_symlink():
                return LocalLyricsResult(
                    LocalLyricsStatus.INVALID,
                    "Embedded audio metadata",
                    diagnostics=("embedded audio path must not be a symbolic link",),
                )
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                os.close(descriptor)
                descriptor = None
                return LocalLyricsResult(
                    LocalLyricsStatus.INVALID,
                    "Embedded audio metadata",
                    diagnostics=("embedded audio path is not a regular file",),
                )
            stream = os.fdopen(descriptor, "rb")
            descriptor = None
            with stream:
                audio = self._loader(stream)
                if audio is None:
                    return LocalLyricsResult(
                        LocalLyricsStatus.MISS, "Embedded audio metadata"
                    )
                tags = getattr(audio, "tags", None)
                if not isinstance(tags, _TagItems):
                    return LocalLyricsResult(
                        LocalLyricsStatus.MISS, "Embedded audio metadata"
                    )
                normalized = {str(key).casefold(): value for key, value in tags.items()}
        except (OSError, ValueError, MutagenError) as error:
            if descriptor is not None:
                os.close(descriptor)
            return LocalLyricsResult(
                LocalLyricsStatus.MISS,
                "Embedded audio metadata",
                diagnostics=(f"embedded lyrics could not be read: {error}",),
            )
        except (TypeError, RuntimeError) as error:
            return LocalLyricsResult(
                LocalLyricsStatus.MISS,
                "Embedded audio metadata",
                diagnostics=(f"embedded lyrics tags could not be read: {error}",),
            )
        value: object | None = None
        field: str | None = None
        for supported in _SUPPORTED_FIELDS:
            if supported in normalized:
                value = normalized[supported]
                field = supported
                break
        if field is None:
            return LocalLyricsResult(LocalLyricsStatus.MISS, "Embedded audio metadata")
        text = _first_text(value)
        if text is None or not text.strip():
            return LocalLyricsResult(
                LocalLyricsStatus.INVALID,
                "Embedded audio metadata",
                diagnostics=(f"embedded {field} field is empty or malformed",),
            )
        if len(text) > MAX_LYRICS_TEXT_CHARS:
            return LocalLyricsResult(
                LocalLyricsStatus.INVALID,
                "Embedded audio metadata",
                diagnostics=("embedded lyrics exceed the safe size limit",),
            )
        duration_ms = _duration_ms(track.candidate.duration_us)
        parsed = parse_lyrics_text(
            text,
            duration_ms=duration_ms,
            timing_provenance=TimingProvenance.IMPORTED,
        )
        if parsed.status is LyricsTextParseStatus.INVALID:
            return LocalLyricsResult(
                LocalLyricsStatus.INVALID,
                "Embedded audio metadata",
                diagnostics=parsed.diagnostics,
            )
        document = build_lyric_document(
            parsed,
            source_name="embedded-vorbis",
            provenance=ContentProvenance.LOCAL,
            retrieved_at=self._now(),
            duration_ms=duration_ms,
            source_title=track.candidate.title,
            source_artist=" & ".join(track.candidate.artists) or None,
            source_album=track.candidate.album,
        )
        return LocalLyricsResult(
            LocalLyricsStatus.FOUND,
            "Embedded audio metadata",
            document,
            parsed.diagnostics,
        )


def _first_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        for item in value:
            if isinstance(item, str):
                return item
    return None
