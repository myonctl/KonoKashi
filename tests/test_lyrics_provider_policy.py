"""Explicit source construction and preference-order boundaries."""

from __future__ import annotations

from konokashi.domain.lyrics import (
    LyricsProviderResult,
    LyricsProviderStatus,
    LyricsQuery,
)
from konokashi.infrastructure.lyrics.providers import (
    create_lyrics_providers,
    order_lyrics_providers,
)


class _InjectedProvider:
    name = "fixture-provider"

    def exact(self, query: LyricsQuery) -> LyricsProviderResult:
        del query
        return LyricsProviderResult(LyricsProviderStatus.NO_RESULT)

    def search(self, query: LyricsQuery) -> LyricsProviderResult:
        del query
        return LyricsProviderResult(LyricsProviderStatus.NO_RESULT)

    def parse_cached(self, payload: bytes, *, search: bool) -> LyricsProviderResult:
        del payload, search
        return LyricsProviderResult(LyricsProviderStatus.NO_RESULT)


def test_default_and_reversed_product_source_order_is_explicit() -> None:
    defaults = create_lyrics_providers()
    reversed_sources = create_lyrics_providers(("Unison", "LRCLIB"))

    assert tuple(item.name for item in defaults) == ("LRCLIB", "Unison")
    assert tuple(item.name for item in reversed_sources) == ("Unison", "LRCLIB")
    assert create_lyrics_providers(()) == ()


def test_preconstructed_sources_are_filtered_without_breaking_test_injection() -> None:
    defaults = create_lyrics_providers()
    assert tuple(
        item.name for item in order_lyrics_providers(defaults, ("Unison",))
    ) == ("Unison",)
    injected = _InjectedProvider()
    assert order_lyrics_providers((injected,), ("LRCLIB", "Unison")) == (injected,)
