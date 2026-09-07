"""Deterministic Unicode script composition without pretending script is language."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum


class UnicodeScript(Enum):
    """Scripts relevant to Stage 5 routing and diagnostics."""

    LATIN = "Latin"
    HAN = "Han"
    HIRAGANA = "Hiragana"
    KATAKANA = "Katakana"
    HANGUL = "Hangul"
    CYRILLIC = "Cyrillic"
    GREEK = "Greek"
    ARABIC = "Arabic"
    THAI = "Thai"
    OTHER = "Other"


@dataclass(frozen=True, slots=True)
class ScriptCount:
    """Count one significant script without retaining lyric text."""

    script: UnicodeScript
    count: int


@dataclass(frozen=True, slots=True)
class ScriptAnalysis:
    """Composition used for conservative routing and bounded diagnostics."""

    counts: tuple[ScriptCount, ...]
    punctuation_or_numbers: int

    @property
    def scripts(self) -> tuple[UnicodeScript, ...]:
        """Return significant scripts in stable display order."""

        return tuple(item.script for item in self.counts)

    @property
    def has_non_latin(self) -> bool:
        """Whether any meaningful character needs a Latin representation."""

        return any(item.script is not UnicodeScript.LATIN for item in self.counts)

    @property
    def label(self) -> str:
        """Return a concise composition label without a language claim."""

        if not self.counts:
            return "Punctuation/numbers only"
        return " + ".join(item.script.value for item in self.counts)


_ORDER = tuple(UnicodeScript)


def _in_ranges(value: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(start <= value <= end for start, end in ranges)


def character_script(character: str) -> UnicodeScript | None:
    """Classify one meaningful character; punctuation/numbers return ``None``."""

    category = unicodedata.category(character)
    if category[0] in {"P", "S", "N", "Z", "C"}:
        return None
    value = ord(character)
    if _in_ranges(value, ((0x3040, 0x309F),)):
        return UnicodeScript.HIRAGANA
    if _in_ranges(value, ((0x30A0, 0x30FF), (0x31F0, 0x31FF), (0xFF66, 0xFF9F))):
        return UnicodeScript.KATAKANA
    if _in_ranges(
        value,
        (
            (0x3400, 0x4DBF),
            (0x4E00, 0x9FFF),
            (0xF900, 0xFAFF),
            (0x20000, 0x323AF),
        ),
    ):
        return UnicodeScript.HAN
    if _in_ranges(
        value,
        ((0x1100, 0x11FF), (0x3130, 0x318F), (0xA960, 0xA97F), (0xAC00, 0xD7FF)),
    ):
        return UnicodeScript.HANGUL
    name = unicodedata.name(character, "")
    for marker, script in (
        ("LATIN", UnicodeScript.LATIN),
        ("CYRILLIC", UnicodeScript.CYRILLIC),
        ("GREEK", UnicodeScript.GREEK),
        ("ARABIC", UnicodeScript.ARABIC),
        ("THAI", UnicodeScript.THAI),
    ):
        if marker in name:
            return script
    if category.startswith(("L", "M")):
        return UnicodeScript.OTHER
    return None


def analyze_scripts(text: str) -> ScriptAnalysis:
    """Count scripts without normalizing or changing the supplied original text."""

    counts = {script: 0 for script in _ORDER}
    punctuation_or_numbers = 0
    previous: UnicodeScript | None = None
    for character in text:
        if unicodedata.category(character).startswith("M") and previous is not None:
            counts[previous] += 1
            continue
        script = character_script(character)
        if script is None:
            punctuation_or_numbers += 1
            continue
        counts[script] += 1
        previous = script
    return ScriptAnalysis(
        tuple(
            ScriptCount(script, counts[script]) for script in _ORDER if counts[script]
        ),
        punctuation_or_numbers,
    )
