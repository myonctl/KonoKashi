"""Deterministic Stage 5 script composition and conservative routing."""

from __future__ import annotations

import unicodedata

import pytest

from konokashi.application.representations import route_romanization
from konokashi.domain.representations import RomanizationRoute
from konokashi.domain.scripts import UnicodeScript, analyze_scripts


@pytest.mark.parametrize(
    ("text", "scripts"),
    (
        ("I love you", (UnicodeScript.LATIN,)),
        ("きみのこえ", (UnicodeScript.HIRAGANA,)),
        ("カタカナ", (UnicodeScript.KATAKANA,)),
        ("君の声", (UnicodeScript.HAN, UnicodeScript.HIRAGANA)),
        ("너의 목소리", (UnicodeScript.HANGUL,)),
        ("Я слышу", (UnicodeScript.CYRILLIC,)),
        ("Ακούω", (UnicodeScript.GREEK,)),
        ("أسمع", (UnicodeScript.ARABIC,)),
        ("ฉันได้ยิน", (UnicodeScript.THAI,)),
        (
            "君と dance",
            (UnicodeScript.LATIN, UnicodeScript.HAN, UnicodeScript.HIRAGANA),
        ),
        ("123 ... ♪", ()),
    ),
)
def test_script_composition(text: str, scripts: tuple[UnicodeScript, ...]) -> None:
    assert analyze_scripts(text).scripts == scripts


def test_unicode_normalization_does_not_change_script_decision() -> None:
    composed = "Ακούω"
    decomposed = unicodedata.normalize("NFD", composed)

    assert analyze_scripts(composed).scripts == analyze_scripts(decomposed).scripts


def test_han_only_requires_explicit_language_evidence() -> None:
    ambiguous = route_romanization("我听见你的声音")
    script_only = route_romanization("我听见你的声音", script_hint="Hans")
    chinese = route_romanization("我听见你的声音", language_hint="zh-Hans")
    japanese = route_romanization("東京", language_hint="ja")

    assert ambiguous.route is None
    assert script_only.route is None
    assert "ambiguous" in ambiguous.diagnostic
    assert chinese.route is RomanizationRoute.CHINESE
    assert japanese.route is RomanizationRoute.JAPANESE


@pytest.mark.parametrize(
    ("text", "route"),
    (
        ("君の声が聞こえる", RomanizationRoute.JAPANESE),
        ("너의 목소리가 들려", RomanizationRoute.KOREAN),
        ("Я слышу твой голос", RomanizationRoute.CYRILLIC),
        ("Ακούω τη φωνή σου", RomanizationRoute.GREEK),  # noqa: RUF001
        ("أسمع صوتك", RomanizationRoute.ARABIC),
        ("ฉันได้ยินเสียง", RomanizationRoute.THAI),
    ),
)
def test_supported_script_routes(text: str, route: RomanizationRoute) -> None:
    assert route_romanization(text).route is route


def test_latin_punctuation_and_mixed_non_latin_are_not_forced() -> None:
    assert route_romanization("I love you").route is None
    assert route_romanization("123 ♪").route is None
    mixed = route_romanization("Я α")  # noqa: RUF001
    assert mixed.route is None
    assert "mixed" in mixed.diagnostic

    unsupported = route_romanization("אני שומע")
    assert unsupported.analysis.scripts == (UnicodeScript.OTHER,)
    assert unsupported.route is None
    assert "unsupported" in unsupported.diagnostic
