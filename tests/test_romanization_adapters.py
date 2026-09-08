"""Real offline adapter behavior with tiny sanitized multilingual fixtures."""

from __future__ import annotations

import socket

import icu
import pytest

from konokashi.domain.representations import (
    GenerationStatus,
    RomanizationRequest,
    RomanizationRoute,
)
from konokashi.infrastructure.romanization import offline
from konokashi.infrastructure.romanization.offline import (
    IcuHanLanguageEvidenceAdapter,
    OfflineRomanizationProvider,
)
from konokashi.infrastructure.romanization.title_aliases import (
    OfflineTitleAliasProvider,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("きみのこえ", "Kimi no koe"),
        ("カタカナ", "Katakana"),
        ("君の声が聞こえる", "Kimi no koe ga kikoeru"),
        ("今日も君を想ってる", "Kyou mo kimi wo omotteru"),
        ("君の声、聞こえる？", "Kimi no koe, kikoeru?"),  # noqa: RUF001
        ("君と dance tonight", "Kimi to dance tonight"),
        ("山田太郎", "Yamada Tarou"),
        ("１２３、声？", "123, koe?"),  # noqa: RUF001
    ),
)
def test_japanese_modified_hepburn_fixtures(text: str, expected: str) -> None:
    result = OfflineRomanizationProvider().generate(
        RomanizationRequest("line", text, RomanizationRoute.JAPANESE, "ja")
    )

    assert result.status is GenerationStatus.AVAILABLE
    assert result.text == expected
    assert "cutlet-" in result.provider_version


def test_korean_chinese_and_generic_icu_fixtures() -> None:
    provider = OfflineRomanizationProvider()
    fixtures = (
        (RomanizationRoute.KOREAN, "너의 목소리가 들려", "neoui mogsoliga deullyeo"),
        (RomanizationRoute.CHINESE, "我听见你的声音", "Wǒ tīng jiàn nǐ de shēng yīn"),
        (RomanizationRoute.CYRILLIC, "Я слышу твой голос", "Â slyšu tvoj golos"),
        (
            RomanizationRoute.GREEK,
            "Ακούω τη φωνή σου",  # noqa: RUF001
            "Akoúō tē phōnḗ sou",
        ),
        (RomanizationRoute.ARABIC, "أسمع صوتك", "ạ̉smʿ ṣwtk"),
        (RomanizationRoute.THAI, "ฉันได้ยิน", "c̄hạn dị̂yin"),
    )

    for route, text, expected in fixtures:
        result = provider.generate(RomanizationRequest("line", text, route))
        assert result.status is GenerationStatus.AVAILABLE
        assert result.text == expected
        if route is RomanizationRoute.CHINESE:
            assert result.provider_version == "pypinyin-0.55.0"
        else:
            assert f"ICU-{icu.ICU_VERSION}" in result.provider_version


@pytest.mark.parametrize(
    ("route", "text", "expected"),
    (
        (RomanizationRoute.KOREAN, "사랑해 baby!", "salanghae baby!"),
        (RomanizationRoute.KOREAN, "홍길동", "hong-gildong"),
        (
            RomanizationRoute.KOREAN,
            "너의 목소리, 들려?",
            "neoui mogsoli, deullyeo?",
        ),
        (
            RomanizationRoute.CHINESE,
            "我听见你的声音，真的？",  # noqa: RUF001
            "Wǒ tīng jiàn nǐ de shēng yīn， zhēn de？",  # noqa: RUF001
        ),
        (RomanizationRoute.CHINESE, "我爱 music 123", "Wǒ ài music 123"),
        (RomanizationRoute.CHINESE, "music 我爱", "music Wǒ ài"),
        (RomanizationRoute.CHINESE, "重庆音乐", "Chóng qìng yīn yuè"),
        (RomanizationRoute.CHINESE, "長樂", "Zhǎng lè"),
    ),
)
def test_korean_and_chinese_spacing_names_mixed_text_and_polyphony(
    route: RomanizationRoute, text: str, expected: str
) -> None:
    original = text
    result = OfflineRomanizationProvider().generate(
        RomanizationRequest("line", text, route)
    )

    assert result.status is GenerationStatus.AVAILABLE
    assert result.text == expected
    assert text == original


def test_japanese_nfc_and_nfd_are_deterministic() -> None:
    import unicodedata

    provider = OfflineRomanizationProvider()
    original = "がくせい"
    results = {
        provider.generate(
            RomanizationRequest(
                "line",
                unicodedata.normalize(form, original),
                RomanizationRoute.JAPANESE,
            )
        ).text
        for form in ("NFC", "NFD")
    }

    assert results == {"Gaku sei"}


def test_generation_is_offline_and_preserves_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = "我聽見你的聲音 mixed 123"

    def network_forbidden(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("romanization attempted a network call")

    monkeypatch.setattr(socket, "socket", network_forbidden)
    result = OfflineRomanizationProvider().generate(
        RomanizationRequest("line", original, RomanizationRoute.CHINESE, "zh-Hant")
    )

    assert result.status is GenerationStatus.AVAILABLE
    assert "mixed 123" in (result.text or "")
    assert original == "我聽見你的聲音 mixed 123"


def test_wowkie_shape_uses_tone_marks_phrase_context_and_explicit_provenance() -> None:
    result = OfflineRomanizationProvider().generate(
        RomanizationRequest(
            "line-0042", "阳光彩虹小白马", RomanizationRoute.CHINESE, "zh"
        )
    )

    assert result.status is GenerationStatus.AVAILABLE
    assert result.text == "Yáng guāng cǎi hóng xiǎo bái mǎ"
    assert result.language == "zh-Latn-pinyin"
    assert result.provider_name == "pypinyin Hanyu Pinyin"
    assert result.provider_version == "pypinyin-0.55.0"
    assert "polyphonic" in result.diagnostics[0]


@pytest.mark.parametrize(
    ("text", "language"),
    (
        ("阳光彩虹小白马", "zh"),
        ("我聽見你的聲音", "zh"),
        ("東京", None),
        ("日本国", None),
    ),
)
def test_icu_han_language_evidence_is_bounded_and_conservative(
    text: str, language: str | None
) -> None:
    evidence = IcuHanLanguageEvidenceAdapter().classify_han(text)

    assert evidence.language == language
    assert evidence.diagnostic


def test_blank_adapter_input_is_controlled() -> None:
    result = OfflineRomanizationProvider().generate(
        RomanizationRequest("blank", "", RomanizationRoute.JAPANESE, "ja")
    )

    assert result.status is GenerationStatus.UNAVAILABLE


def test_missing_chinese_adapter_dependency_is_an_explained_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_text: str) -> str:
        raise ImportError("pypinyin unavailable")

    monkeypatch.setattr(offline, "_pinyin_text", unavailable)
    result = OfflineRomanizationProvider().generate(
        RomanizationRequest("line", "阳光彩虹小白马", RomanizationRoute.CHINESE)
    )

    assert result.status is GenerationStatus.FAILED
    assert result.text is None
    assert result.diagnostics == ("Chinese Pinyin adapter failed: ImportError",)


def test_title_alias_provider_exposes_phonetics_without_claiming_translation() -> None:
    aliases = OfflineTitleAliasProvider().aliases("アンドロイドガール")

    assert aliases == ("andoroidogaru",)
    assert OfflineTitleAliasProvider().aliases("Android Girl") == ()
