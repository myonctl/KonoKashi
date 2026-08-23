"""Offline Cutlet, pypinyin, and Unicode ICU representation adapters."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from lyricflow.domain.lyrics import RepresentationKind
from lyricflow.domain.representations import (
    GenerationStatus,
    LanguageRoutingEvidence,
    RepresentationUncertainty,
    RomanizationProviderResult,
    RomanizationRequest,
    RomanizationRoute,
)
from lyricflow.domain.scripts import UnicodeScript, analyze_scripts, character_script


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


class CutletJapaneseAdapter:
    """Generate Modified Hepburn romaji with an offline lexical dictionary."""

    name = "Cutlet Modified Hepburn"

    def __init__(self) -> None:
        self._engine: Any | None = None

    @property
    def provider_version(self) -> str:
        """Retain both converter and dictionary versions."""

        cutlet_version = _package_version("cutlet")
        dictionary_version = _package_version("unidic-lite")
        return f"cutlet-{cutlet_version}/unidic-lite-{dictionary_version}"

    def generate(self, request: RomanizationRequest) -> RomanizationProviderResult:
        """Generate one line without allowing an engine failure to escape."""

        try:
            if self._engine is None:
                import cutlet  # type: ignore[import-untyped]

                engine = cutlet.Cutlet("hepburn")
                engine.use_foreign_spelling = False
                self._engine = engine
            text = str(self._engine.romaji(request.text))
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            return RomanizationProviderResult(
                GenerationStatus.FAILED,
                self.name,
                self.provider_version,
                RepresentationKind.ROMANIZED,
                diagnostics=(f"Japanese romanizer failed: {type(error).__name__}",),
            )
        if not text.strip():
            return RomanizationProviderResult(
                GenerationStatus.UNAVAILABLE,
                self.name,
                self.provider_version,
                RepresentationKind.ROMANIZED,
                diagnostics=("Japanese romanizer returned no usable text",),
            )
        analysis = analyze_scripts(request.text)
        ambiguous = UnicodeScript.HAN in analysis.scripts
        return RomanizationProviderResult(
            GenerationStatus.AVAILABLE,
            self.name,
            self.provider_version,
            RepresentationKind.ROMANIZED,
            text=text,
            language="ja-Latn",
            uncertainty=(
                RepresentationUncertainty.AMBIGUOUS
                if ambiguous
                else RepresentationUncertainty.NONE
            ),
            diagnostics=(
                ("kanji/name/song-specific readings may require user correction",)
                if ambiguous
                else ()
            ),
        )


def _capitalize_first_letter(text: str) -> str:
    for index, character in enumerate(text):
        if character.isalpha():
            return f"{text[:index]}{character.upper()}{text[index + 1 :]}"
    return text


def _pinyin_text(text: str) -> str:
    """Convert contiguous Han with phrase context while preserving every other run."""

    from pypinyin import Style, lazy_pinyin

    trailing_punctuation = frozenset(",.;:!?\uff0c\u3002\uff1b\uff1a\uff01\uff1f")

    runs: list[tuple[bool, str]] = []
    for character in text:
        is_han = character_script(character) is UnicodeScript.HAN
        if runs and runs[-1][0] is is_han:
            previous_han, previous_text = runs[-1]
            runs[-1] = (previous_han, previous_text + character)
        else:
            runs.append((is_han, character))
    output = ""
    has_converted_han = False
    for is_han, run in runs:
        if is_han:
            converted = " ".join(
                lazy_pinyin(
                    run,
                    style=Style.TONE,
                    errors="default",
                    strict=True,
                    v_to_u=True,
                )
            )
            if not has_converted_han:
                converted = _capitalize_first_letter(converted)
                has_converted_han = True
            if (
                output
                and not output[-1].isspace()
                and (output[-1].isalnum() or output[-1] in trailing_punctuation)
            ):
                output += " "
            output += converted
            continue
        if (
            output
            and run
            and output[-1].isalpha()
            and run[0].isalnum()
            and not run[0].isspace()
        ):
            output += " "
        output += run
    return output


class PypinyinChineseAdapter:
    """Generate phrase-aware Hanyu Pinyin with Unicode tone marks offline."""

    name = "pypinyin Hanyu Pinyin"

    @property
    def provider_version(self) -> str:
        return f"pypinyin-{_package_version('pypinyin')}"

    def generate(self, request: RomanizationRequest) -> RomanizationProviderResult:
        try:
            text = _pinyin_text(request.text)
        except (ImportError, RuntimeError, TypeError, ValueError) as error:
            return RomanizationProviderResult(
                GenerationStatus.FAILED,
                self.name,
                self.provider_version,
                RepresentationKind.ROMANIZED,
                diagnostics=(f"Chinese Pinyin adapter failed: {type(error).__name__}",),
            )
        if not text.strip() or text == request.text:
            return RomanizationProviderResult(
                GenerationStatus.UNAVAILABLE,
                self.name,
                self.provider_version,
                RepresentationKind.ROMANIZED,
                diagnostics=(
                    "Chinese Pinyin adapter produced no Latin representation",
                ),
            )
        return RomanizationProviderResult(
            GenerationStatus.AVAILABLE,
            self.name,
            self.provider_version,
            RepresentationKind.ROMANIZED,
            text=text,
            language="zh-Latn-pinyin",
            uncertainty=RepresentationUncertainty.AMBIGUOUS,
            diagnostics=(
                "phrase dictionaries reduce but cannot eliminate polyphonic ambiguity",
            ),
        )


class IcuHanLanguageEvidenceAdapter:
    """Infer only bounded Chinese variant evidence; never return converted text."""

    def __init__(self) -> None:
        self._simplified_to_traditional: Any | None = None
        self._traditional_to_simplified: Any | None = None

    def classify_han(self, text: str) -> LanguageRoutingEvidence:
        han = "".join(
            character
            for character in text
            if character_script(character) is UnicodeScript.HAN
        )
        if len(han) < 4:
            return LanguageRoutingEvidence(
                None,
                RepresentationUncertainty.AMBIGUOUS,
                "Han-only document is too short for conservative Chinese "
                "variant evidence",
            )
        try:
            import icu  # type: ignore[import-untyped]

            if self._simplified_to_traditional is None:
                self._simplified_to_traditional = icu.Transliterator.createInstance(
                    "Simplified-Traditional"
                )
                self._traditional_to_simplified = icu.Transliterator.createInstance(
                    "Traditional-Simplified"
                )
            traditional = str(self._simplified_to_traditional.transliterate(han))
            assert self._traditional_to_simplified is not None
            simplified = str(self._traditional_to_simplified.transliterate(han))
        except (ImportError, RuntimeError, ValueError) as error:
            return LanguageRoutingEvidence(
                None,
                RepresentationUncertainty.AMBIGUOUS,
                f"Chinese language-evidence adapter failed: {type(error).__name__}",
            )
        variant_changes = max(
            sum(left != right for left, right in zip(han, traditional, strict=False)),
            sum(left != right for left, right in zip(han, simplified, strict=False)),
        )
        if variant_changes < 2:
            return LanguageRoutingEvidence(
                None,
                RepresentationUncertainty.AMBIGUOUS,
                "Han-only document lacks sufficient Chinese-specific variant evidence",
            )
        return LanguageRoutingEvidence(
            "zh",
            RepresentationUncertainty.AMBIGUOUS,
            "document-level Simplified/Traditional variant evidence supports "
            "Chinese routing",
        )


_ICU_TRANSFORMS = {
    RomanizationRoute.KOREAN: "Hangul-Latin",
    RomanizationRoute.CHINESE: "Han-Latin",
    RomanizationRoute.CYRILLIC: "Cyrillic-Latin",
    RomanizationRoute.GREEK: "Greek-Latin",
    RomanizationRoute.ARABIC: "Arabic-Latin",
    RomanizationRoute.THAI: "Thai-Latin",
}

_ICU_LANGUAGES = {
    RomanizationRoute.KOREAN: "ko-Latn",
    RomanizationRoute.CHINESE: "zh-Latn",
}


class IcuTransliterationAdapter:
    """Use maintained ICU transforms while preserving original input separately."""

    name = "Unicode ICU"

    def __init__(self) -> None:
        self._transliterators: dict[RomanizationRoute, Any] = {}

    @property
    def provider_version(self) -> str:
        """Report wrapper and linked Unicode ICU versions."""

        try:
            import icu

            return f"PyICU-{_package_version('PyICU')}/ICU-{icu.ICU_VERSION}"
        except ImportError:
            return f"PyICU-{_package_version('PyICU')}/ICU-unavailable"

    def generate(self, request: RomanizationRequest) -> RomanizationProviderResult:
        """Apply exactly one route-specific ICU transform with controlled errors."""

        transform_id = _ICU_TRANSFORMS.get(request.route)
        kind = (
            RepresentationKind.ROMANIZED
            if request.route in {RomanizationRoute.KOREAN, RomanizationRoute.CHINESE}
            else RepresentationKind.TRANSLITERATED
        )
        if transform_id is None:
            return RomanizationProviderResult(
                GenerationStatus.UNAVAILABLE,
                self.name,
                self.provider_version,
                kind,
                diagnostics=(
                    f"no ICU transform is configured for {request.route.value}",
                ),
            )
        try:
            transliterator = self._transliterators.get(request.route)
            if transliterator is None:
                import icu

                transliterator = icu.Transliterator.createInstance(transform_id)
                self._transliterators[request.route] = transliterator
            text = str(transliterator.transliterate(request.text))
        except (ImportError, RuntimeError, ValueError) as error:
            return RomanizationProviderResult(
                GenerationStatus.FAILED,
                self.name,
                self.provider_version,
                kind,
                diagnostics=(f"ICU {transform_id} failed: {type(error).__name__}",),
            )
        if not text.strip() or text == request.text:
            return RomanizationProviderResult(
                GenerationStatus.UNAVAILABLE,
                self.name,
                self.provider_version,
                kind,
                diagnostics=(f"ICU {transform_id} produced no Latin representation",),
            )
        ambiguous_route = request.route in {
            RomanizationRoute.KOREAN,
            RomanizationRoute.CHINESE,
        }
        limitation = {
            RomanizationRoute.KOREAN: (
                "Korean pronunciation and names may require user correction"
            ),
            RomanizationRoute.CHINESE: (
                "polyphonic characters and names may require user correction"
            ),
        }.get(request.route)
        return RomanizationProviderResult(
            GenerationStatus.AVAILABLE,
            self.name,
            self.provider_version,
            kind,
            text=text,
            language=_ICU_LANGUAGES.get(request.route),
            uncertainty=(
                RepresentationUncertainty.AMBIGUOUS
                if ambiguous_route
                else RepresentationUncertainty.NONE
            ),
            diagnostics=(() if limitation is None else (limitation,)),
        )


class OfflineRomanizationProvider:
    """Composite port adapter dispatching only an already-decided application route."""

    name = "LyricFlow offline romanization"

    def __init__(
        self,
        japanese: CutletJapaneseAdapter | None = None,
        chinese: PypinyinChineseAdapter | None = None,
        icu_adapter: IcuTransliterationAdapter | None = None,
    ) -> None:
        self._japanese = japanese or CutletJapaneseAdapter()
        self._chinese = chinese or PypinyinChineseAdapter()
        self._icu = icu_adapter or IcuTransliterationAdapter()

    def generate(self, request: RomanizationRequest) -> RomanizationProviderResult:
        """Dispatch without embedding routing policy in the adapter."""

        if request.route is RomanizationRoute.JAPANESE:
            return self._japanese.generate(request)
        if request.route is RomanizationRoute.CHINESE:
            return self._chinese.generate(request)
        return self._icu.generate(request)
