"""Immutable line indexes prepared once for a loaded frontend bundle."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from konokashi.application.representations import original_lines
from konokashi.domain.lyrics import LyricDocument, LyricLine, RepresentationKind
from konokashi.domain.representations import EffectiveRepresentationLine


@dataclass(frozen=True, slots=True)
class FrontendLineCache:
    """Original ordering and aligned representation lookup for one bundle."""

    document: LyricDocument
    originals: tuple[LyricLine, ...]
    original_indexes: Mapping[str, int]
    representations: Mapping[
        tuple[str, RepresentationKind], EffectiveRepresentationLine
    ]


def build_frontend_line_cache(
    document: LyricDocument,
    representations: tuple[EffectiveRepresentationLine, ...],
) -> FrontendLineCache:
    """Build read-only O(1) lookup maps once when frontend content is loaded."""

    originals = original_lines(document)
    indexes = {line.line_id: index for index, line in enumerate(originals)}
    if len(indexes) != len(originals):
        raise ValueError("duplicate original lyric line IDs are unsafe")
    by_line_kind = {
        (item.original_line.line_id, item.kind): item for item in representations
    }
    return FrontendLineCache(
        document,
        originals,
        MappingProxyType(indexes),
        MappingProxyType(by_line_kind),
    )
