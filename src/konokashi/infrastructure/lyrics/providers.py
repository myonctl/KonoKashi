"""Composition helpers for the explicit read-only lyric source policy."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from konokashi.application.ports import LyricsProviderPort
from konokashi.infrastructure.lyrics.lrclib import LrclibLyricsProvider
from konokashi.infrastructure.lyrics.unison import UnisonLyricsProvider

DEFAULT_PROVIDER_NAMES = ("LRCLIB", "Unison")


def create_lyrics_providers(
    names: Sequence[str] = DEFAULT_PROVIDER_NAMES,
) -> tuple[LyricsProviderPort, ...]:
    """Build known adapters in user preference order without doing network I/O."""

    factories: dict[str, Callable[[], LyricsProviderPort]] = {
        "LRCLIB": LrclibLyricsProvider,
        "Unison": UnisonLyricsProvider,
    }
    return tuple(factories[name]() for name in names)


def order_lyrics_providers(
    providers: Sequence[LyricsProviderPort], names: Sequence[str]
) -> tuple[LyricsProviderPort, ...]:
    """Apply a validated source policy to preconstructed/injected adapters."""

    by_name = {provider.name: provider for provider in providers}
    selected = tuple(by_name[name] for name in names if name in by_name)
    # A custom test/embedder typically injects one provider that is intentionally
    # absent from the product schema. Preserve that explicit dependency injection.
    if (
        not selected
        and len(providers) == 1
        and providers[0].name not in factories_names()
    ):
        return tuple(providers)
    return selected


def factories_names() -> frozenset[str]:
    """Return stable source identifiers exposed by the canonical setting."""

    return frozenset(DEFAULT_PROVIDER_NAMES)
