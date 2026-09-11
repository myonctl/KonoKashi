"""Construct canonical original-only lyric documents from parsed source text."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256

from konokashi.domain.lyrics import (
    ApprovalState,
    ContentProvenance,
    LyricDocument,
    LyricDocumentKind,
    LyricRepresentation,
    LyricsTextParseStatus,
    LyricTimingLevel,
    ParsedLyricsText,
    RepresentationKind,
)


def document_id_for(
    source_name: str, provider_record_id: str | None, raw_text_checksum: str | None
) -> str:
    """Derive a stable non-path-revealing ID for one source record/content pair."""

    stable_record = provider_record_id or raw_text_checksum or "instrumental"
    digest = sha256(f"{source_name}\0{stable_record}".encode()).hexdigest()
    return f"lyrics-{digest}"


def build_lyric_document(
    parsed: ParsedLyricsText,
    *,
    source_name: str,
    provenance: ContentProvenance,
    retrieved_at: datetime,
    provider_record_id: str | None = None,
    duration_ms: int | None = None,
    source_title: str | None = None,
    source_artist: str | None = None,
    source_album: str | None = None,
) -> LyricDocument:
    """Build one original representation while preserving raw normalized text."""

    if parsed.status is LyricsTextParseStatus.INVALID:
        raise ValueError("invalid parsed lyrics cannot become a document")
    kind = (
        LyricDocumentKind.SYNCED
        if parsed.status is LyricsTextParseStatus.SYNCED
        else LyricDocumentKind.PLAIN
    )
    document_id = document_id_for(
        source_name, provider_record_id, parsed.raw_text_checksum
    )
    return LyricDocument(
        document_id=document_id,
        kind=kind,
        source_name=source_name,
        original_text=parsed.normalized_text,
        raw_text_checksum=parsed.raw_text_checksum,
        approval_state=ApprovalState.UNREVIEWED,
        retrieved_at=retrieved_at,
        timing_level=parsed.timing_level,
        representations=(
            LyricRepresentation(
                representation_id=f"{document_id}:original",
                kind=RepresentationKind.ORIGINAL,
                provenance=provenance,
                approval_state=ApprovalState.UNREVIEWED,
                lines=parsed.lines,
            ),
        ),
        provider_record_id=provider_record_id,
        duration_ms=duration_ms,
        source_title=source_title,
        source_artist=source_artist,
        source_album=source_album,
    )


def build_instrumental_document(
    *,
    source_name: str,
    provider_record_id: str,
    retrieved_at: datetime,
    duration_ms: int | None,
    source_title: str,
    source_artist: str,
    source_album: str | None,
) -> LyricDocument:
    """Represent an explicit provider instrumental state without fake lyric lines."""

    return LyricDocument(
        document_id=document_id_for(source_name, provider_record_id, None),
        kind=LyricDocumentKind.INSTRUMENTAL,
        source_name=source_name,
        original_text=None,
        raw_text_checksum=None,
        approval_state=ApprovalState.UNREVIEWED,
        retrieved_at=retrieved_at,
        timing_level=LyricTimingLevel.UNSYNCHRONIZED,
        provider_record_id=provider_record_id,
        duration_ms=duration_ms,
        source_title=source_title,
        source_artist=source_artist,
        source_album=source_album,
    )
