"""Compose durable and invocation-only lyric timing without changing documents."""

from __future__ import annotations

from konokashi.domain.synchronization import (
    LyricDocumentTiming,
    LyricTimingCalibration,
)


def effective_lyric_timing(
    document_timing: LyricDocumentTiming | None,
    *,
    invocation_offset_us: int = 0,
) -> LyricTimingCalibration:
    """Return the one effective display delay used by synchronization.

    Positive values follow the canonical delay convention and show provider
    transitions later. The invocation offset is added once to any durable
    document delay; neither input is mutated or persisted here.
    """

    durable_delay_us = (
        0 if document_timing is None else document_timing.lyrics_display_delay_us
    )
    if invocation_offset_us:
        source = (
            "durable per-document display delay plus temporary invocation offset"
            if document_timing is not None
            else "temporary invocation offset"
        )
    elif document_timing is not None:
        source = "durable per-document display delay"
    else:
        source = "provider timestamps; no user calibration"
    return LyricTimingCalibration(
        durable_delay_us + invocation_offset_us,
        source=source,
        limitations=("provider lyric timestamp error is unmeasured",),
    )
