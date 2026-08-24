"""Canonical provider candidate to original-only lyric document mapping."""

from __future__ import annotations

from datetime import datetime

from lyriflux.domain.lyrics import (
    ContentProvenance,
    LyricDocument,
    LyricsProviderCandidate,
    LyricsTextParseStatus,
)
from lyriflux.infrastructure.lyrics.documents import (
    build_instrumental_document,
    build_lyric_document,
    document_id_for,
)
from lyriflux.infrastructure.lyrics.lrc import parse_lyrics_text


class ProviderLyricDocumentBuilder:
    """Map provider candidates into the Stage 3 provider-neutral schema."""

    def document_id(self, candidate: LyricsProviderCandidate) -> str:
        """Deduplicate the same provider record across query/cache paths."""

        return document_id_for(candidate.provider, candidate.record_id, None)

    def build(
        self, candidate: LyricsProviderCandidate, retrieved_at: datetime
    ) -> tuple[LyricDocument | None, tuple[str, ...]]:
        """Prefer valid synced content, then valid plain content, or instrumental."""

        if candidate.instrumental:
            return (
                build_instrumental_document(
                    source_name=candidate.provider,
                    provider_record_id=candidate.record_id,
                    retrieved_at=retrieved_at,
                    duration_ms=candidate.duration_ms,
                    source_title=candidate.track_name,
                    source_artist=candidate.artist_name,
                    source_album=candidate.album_name,
                ),
                (),
            )
        diagnostics: list[str] = []
        parsed = None
        if candidate.synced_lyrics is not None:
            parsed = parse_lyrics_text(
                candidate.synced_lyrics, duration_ms=candidate.duration_ms
            )
            diagnostics.extend(parsed.diagnostics)
            if parsed.status is LyricsTextParseStatus.INVALID:
                diagnostics.append("provider synchronized lyrics were invalid")
                parsed = None
        if parsed is None and candidate.plain_lyrics is not None:
            parsed = parse_lyrics_text(candidate.plain_lyrics)
            diagnostics.extend(parsed.diagnostics)
            if parsed.status is LyricsTextParseStatus.INVALID:
                diagnostics.append("provider plain lyrics were invalid")
                parsed = None
            elif candidate.synced_lyrics is not None:
                diagnostics.append("used provider plain lyrics fallback")
        if parsed is None:
            return None, tuple(dict.fromkeys(diagnostics))
        return (
            build_lyric_document(
                parsed,
                source_name=candidate.provider,
                provenance=ContentProvenance.PROVIDER,
                retrieved_at=retrieved_at,
                provider_record_id=candidate.record_id,
                duration_ms=candidate.duration_ms,
                source_title=candidate.track_name,
                source_artist=candidate.artist_name,
                source_album=candidate.album_name,
            ),
            tuple(dict.fromkeys(diagnostics)),
        )
