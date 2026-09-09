"""Benchmark-style coverage for immutable frontend line lookup caches."""

from __future__ import annotations

from dataclasses import replace
from time import perf_counter

import pytest

from konokashi.application.frontend_lines import build_frontend_line_cache
from konokashi.domain.lyrics import LyricLine, RepresentationKind
from konokashi.domain.representations import EffectiveRepresentationLine
from tests.test_lyrics_sync import document


def test_large_frontend_line_maps_build_once_and_lookup_with_generous_ceiling() -> None:
    base = document()
    originals = tuple(
        LyricLine(f"line-{index}", f"line {index}", index * 20)
        for index in range(20_000)
    )
    expanded = replace(
        base,
        document_id="large-frontend-document",
        representations=(replace(base.representations[0], lines=originals),),
    )
    effective = tuple(
        EffectiveRepresentationLine(
            line,
            RepresentationKind.TRANSLATED,
            f"translation {index}",
            None,
            None,
            None,
            None,
            None,
        )
        for index, line in enumerate(originals)
    )

    started = perf_counter()
    cache = build_frontend_line_cache(expanded, effective)
    for index in range(200_000):
        line_index = index * 97 % len(originals)
        line_id = f"line-{line_index}"
        assert cache.original_indexes[line_id] == line_index
        assert (
            cache.representations[(line_id, RepresentationKind.TRANSLATED)].text
            == f"translation {line_index}"
        )
    elapsed = perf_counter() - started

    assert cache.document is expanded
    assert cache.originals is originals
    assert elapsed < 5.0
    with pytest.raises(TypeError):
        cache.original_indexes["new"] = 1  # type: ignore[index]
