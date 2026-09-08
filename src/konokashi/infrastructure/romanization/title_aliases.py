"""Offline phonetic aliases used only to score provider title candidates."""

from __future__ import annotations

from typing import Any


class OfflineTitleAliasProvider:
    """Transliterate scripts to Latin without claiming semantic translation."""

    def __init__(self) -> None:
        self._transliterator: Any | None = None

    def aliases(self, title: str) -> tuple[str, ...]:
        try:
            import icu  # type: ignore[import-untyped]

            if self._transliterator is None:
                self._transliterator = icu.Transliterator.createInstance(
                    "Any-Latin; Latin-ASCII"
                )
            transliterator = self._transliterator
            converted = str(transliterator.transliterate(title))
        except (ImportError, RuntimeError, ValueError):
            return ()
        if not converted.strip() or converted == title:
            return ()
        return (converted,)
