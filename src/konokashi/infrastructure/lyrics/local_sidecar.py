"""Read-only adjacent LRC/plain lyric discovery for exact local recordings."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from konokashi.domain.identity import LocalFileIdentity
from konokashi.domain.lyrics import (
    ContentProvenance,
    LocalLyricsResult,
    LocalLyricsStatus,
    LyricsTextParseStatus,
    TimingProvenance,
)
from konokashi.domain.tracks import ResolvedTrack, semantic_duration_us
from konokashi.infrastructure.lyrics.documents import build_lyric_document
from konokashi.infrastructure.lyrics.lrc import MAX_LYRICS_TEXT_CHARS, parse_lyrics_text

MAX_LOCAL_LYRICS_BYTES = MAX_LYRICS_TEXT_CHARS * 4


class LocalSidecarLyricsProvider:
    """Load only ``media.with_suffix('.lrc')`` without directory scanning."""

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def load(self, track: ResolvedTrack) -> LocalLyricsResult:
        """Return an exact-path sidecar result without modifying either file."""

        if not isinstance(track.source_identity, LocalFileIdentity):
            return LocalLyricsResult(LocalLyricsStatus.MISS, "Local sidecar LRC")
        sidecar = Path(track.source_identity.canonical_path).with_suffix(".lrc")
        try:
            descriptor = os.open(
                sidecar,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    return LocalLyricsResult(
                        LocalLyricsStatus.INVALID,
                        "Local sidecar LRC",
                        diagnostics=("adjacent lyrics path is not a regular file",),
                    )
                raw = stream.read(MAX_LOCAL_LYRICS_BYTES + 1)
            if len(raw) > MAX_LOCAL_LYRICS_BYTES:
                return LocalLyricsResult(
                    LocalLyricsStatus.INVALID,
                    "Local sidecar LRC",
                    diagnostics=("adjacent lyrics file exceeds the safe size limit",),
                )
        except FileNotFoundError:
            return LocalLyricsResult(LocalLyricsStatus.MISS, "Local sidecar LRC")
        except OSError as error:
            return LocalLyricsResult(
                LocalLyricsStatus.MISS,
                "Local sidecar LRC",
                diagnostics=(f"adjacent lyrics file is inaccessible: {error}",),
            )
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return LocalLyricsResult(
                LocalLyricsStatus.INVALID,
                "Local sidecar LRC",
                diagnostics=("adjacent lyrics file is not valid UTF-8",),
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
                "Local sidecar LRC",
                diagnostics=parsed.diagnostics,
            )
        document = build_lyric_document(
            parsed,
            source_name="local-sidecar",
            provenance=ContentProvenance.LOCAL,
            retrieved_at=self._now(),
            duration_ms=duration_ms,
            source_title=track.candidate.title,
            source_artist=" & ".join(track.candidate.artists) or None,
            source_album=track.candidate.album,
        )
        return LocalLyricsResult(
            LocalLyricsStatus.FOUND,
            "Local sidecar LRC",
            document,
            parsed.diagnostics,
        )


def _duration_ms(duration_us: int | None) -> int | None:
    duration_us = semantic_duration_us(duration_us)
    if duration_us is None:
        return None
    return (duration_us + 500) // 1000
