"""Cutlet/UniDic Japanese and Unicode ICU transliteration adapters."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from lyricflow.domain.lyrics import RepresentationKind
from lyricflow.domain.representations import (
    GenerationStatus,
    RepresentationUncertainty,
    RomanizationProviderResult,
    RomanizationRequest,
    RomanizationRoute,
)
from lyricflow.domain.scripts import UnicodeScript, analyze_scripts


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
            import icu  # type: ignore[import-untyped]

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
        icu_adapter: IcuTransliterationAdapter | None = None,
    ) -> None:
        self._japanese = japanese or CutletJapaneseAdapter()
        self._icu = icu_adapter or IcuTransliterationAdapter()

    def generate(self, request: RomanizationRequest) -> RomanizationProviderResult:
        """Dispatch without embedding routing policy in the adapter."""

        if request.route is RomanizationRoute.JAPANESE:
            return self._japanese.generate(request)
        return self._icu.generate(request)
