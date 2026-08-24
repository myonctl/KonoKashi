"""Bounded CLI rendering for provider-neutral lyrics resolution results."""

from __future__ import annotations

from lyriflux.domain.identity import source_identity_value
from lyriflux.domain.lyrics import LyricLine, LyricsResolutionResult
from lyriflux.domain.tracks import ResolvedTrack

DEFAULT_PREVIEW_LINES = 3


def render_lyrics_resolution(
    track: ResolvedTrack,
    result: LyricsResolutionResult,
    *,
    full: bool = False,
) -> str:
    """Explain track, provenance, confidence, cache/network use, and a preview."""

    candidate = track.candidate
    output = [
        f"selected player: {track.raw_snapshot.service_name}",
        f"track: {candidate.title or '<unknown>'}",
        f"artist: {', '.join(candidate.artists) or '<unknown>'}",
        f"source identity: {source_identity_value(track.source_identity)}",
        f"lyrics status: {result.status.value}",
        f"lyrics source: {result.source_label or 'none'}",
    ]
    document = result.document
    if document is not None:
        output.append(f"provider record: {document.provider_record_id or 'none'}")
    else:
        output.append("provider record: none")
    output.append(
        f"match confidence: {result.confidence.value if result.confidence else 'none'}"
    )
    if result.evidence:
        output.append("match evidence:")
        output.extend(f"  - {item}" for item in result.evidence)

    lines: tuple[LyricLine, ...] = ()
    if document is not None and document.representations:
        original = next(
            (
                representation
                for representation in document.representations
                if representation.kind.value == "original"
            ),
            document.representations[0],
        )
        lines = original.lines
    output.extend(
        (
            f"timed lines: {sum(line.start_ms is not None for line in lines)}",
            f"plain lines: {len(lines)}",
            f"cache: {'hit' if result.cache_hit else 'miss'}",
            f"network: {'used' if result.network_used else 'not used'}",
        )
    )
    if result.retry_after_seconds is not None:
        output.append(f"retry after: {result.retry_after_seconds} seconds")
    if result.alternatives:
        output.append("unsafe provider candidates:")
        output.extend(
            f"  - {item.artist_name} / {item.track_name} (record {item.record_id})"
            for item in result.alternatives
        )
    if lines:
        shown = lines if full else lines[:DEFAULT_PREVIEW_LINES]
        output.append("lyrics:" if full else "lyrics preview:")
        for line in shown:
            prefix = ""
            if line.start_ms is not None:
                minutes, remainder = divmod(line.start_ms, 60_000)
                seconds, milliseconds = divmod(remainder, 1000)
                prefix = f"[{minutes:02d}:{seconds:02d}.{milliseconds:03d}] "
            output.append(f"  {prefix}{line.text}")
        if not full and len(lines) > len(shown):
            output.append(f"  ... {len(lines) - len(shown)} more lines; use --full")
    if result.diagnostics:
        output.append("diagnostics:")
        output.extend(f"  - {item}" for item in result.diagnostics)
    return "\n".join(output)
