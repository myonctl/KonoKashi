"""Conservative, explainable metadata normalization policy."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from konokashi.domain.tracks import TrackCandidate

_DASH_TRANSLATION = str.maketrans(
    {"\N{EN DASH}": "-", "\N{EM DASH}": "-", "\N{MINUS SIGN}": "-"}
)
_QUOTE_TRANSLATION = str.maketrans(
    {
        "\N{LEFT DOUBLE QUOTATION MARK}": '"',
        "\N{RIGHT DOUBLE QUOTATION MARK}": '"',
        "\N{DOUBLE LOW-9 QUOTATION MARK}": '"',
        "\N{DOUBLE HIGH-REVERSED-9 QUOTATION MARK}": '"',
        "\N{LEFT SINGLE QUOTATION MARK}": "'",
        "\N{RIGHT SINGLE QUOTATION MARK}": "'",
    }
)
_FEATURING = re.compile(r"\b(?:feat(?:uring)?|ft)\.?\s+", re.IGNORECASE)
_RECORDING_DECORATION = re.compile(
    r"\b(?:live|remix|cover|instrumental|acoustic|nightcore|version|edit|mix)\b",
    re.IGNORECASE,
)
_LEADING_BROWSER_COUNT = re.compile(r"^\(\d+\)\s+")
_BROWSER_YOUTUBE_SUFFIX = re.compile(r"\s+-\s+youtube\s*$", re.IGNORECASE)
_PRESENTATION_GROUP = re.compile(
    r"\s*[\[(](?:official\s+(?:music\s+)?video|official\s+audio|lyrics?|"
    r"lyric\s+video|music\s+video|hd|4k|full\s+mtv)[\])]\s*$",
    re.IGNORECASE,
)
_PRESENTATION_DASH = re.compile(
    r"\s+-\s+(?:official\s+(?:music\s+)?video|official\s+audio|lyrics?|"
    r"lyric\s+video|music\s+video|hd|4k|full\s+mtv|youtube)"
    r"(?:\s*[\[(].*?[\])])?\s*$",
    re.IGNORECASE,
)
_PRESENTATION_BARE = re.compile(
    r"\s+(?:official\s+(?:music\s+)?video|official\s+audio|lyrics?|"
    r"lyric\s+video|music\s+video|hd|4k|full\s+mtv)\s*$",
    re.IGNORECASE,
)
_TOPIC_SUFFIX = re.compile(r"\s+-\s+.+?\s+-\s+topic\s*$", re.IGNORECASE)
_VERSION_GROUP = re.compile(r"^(?P<base>.+?)\s*[\[(](?P<qualifier>[^\[\]()]+)[\])]\s*$")
_VERSION_QUALIFIER = re.compile(
    r"^(?:radio\s+(?:edit|version)|edit|extended\s+mix|original\s+mix|"
    r"(?:.+?\s+)?remix|(?:\d{4}\s+)?remaster(?:ed)?|live|acoustic|"
    r"instrumental|demo|vip|cover|club\s+mix|single\s+version|album\s+version)$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class NormalizedValue:
    """Text plus every transformation applied to derive it."""

    value: str
    transformations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TitleVersion:
    """Raw title and an optional conservatively parsed recording qualifier."""

    raw_title: str
    base_title: str
    qualifier: str | None


def parse_title_version(value: str) -> TitleVersion:
    """Split only a trailing group confidently describing a recording version."""

    normalized = normalize_text(value).value
    match = _VERSION_GROUP.fullmatch(normalized)
    if match is None:
        return TitleVersion(value, normalized, None)
    qualifier = " ".join(match.group("qualifier").split())
    if _VERSION_QUALIFIER.fullmatch(qualifier) is None:
        return TitleVersion(value, normalized, None)
    return TitleVersion(value, match.group("base").strip(), qualifier)


def normalize_text(value: str) -> NormalizedValue:
    """Normalize Unicode, quotes, dashes, and whitespace without losing versions."""

    transformations: list[str] = []
    normalized = unicodedata.normalize("NFKC", value)
    if normalized != value:
        transformations.append("normalized Unicode with NFKC")
    translated = normalized.translate(_DASH_TRANSLATION).translate(_QUOTE_TRANSLATION)
    if translated != normalized:
        transformations.append("normalized quote/dash variants")
    compact = " ".join(translated.split())
    if compact != translated:
        transformations.append("collapsed whitespace")
    return NormalizedValue(compact.strip(), tuple(transformations))


def normalize_artist(value: str) -> NormalizedValue:
    """Normalize featuring notation while retaining every named artist."""

    normalized = normalize_text(value)
    transformations = list(normalized.transformations)
    artist = _FEATURING.sub("feat. ", normalized.value)
    if artist != normalized.value:
        transformations.append("normalized feat./ft./featuring notation")
    return NormalizedValue(artist, tuple(transformations))


def comparison_key(value: str) -> str:
    """Build a punctuation-tolerant key without erasing version words."""

    normalized = normalize_text(value).value.casefold()
    normalized = _FEATURING.sub(" feat ", normalized)
    normalized = normalized.replace("&", " and ")
    return " ".join(re.findall(r"\w+", normalized, flags=re.UNICODE))


def _remove_presentation_suffix(value: str) -> NormalizedValue:
    transformations: list[str] = []
    current = value
    while True:
        updated = _PRESENTATION_GROUP.sub("", current)
        if updated == current:
            updated = _PRESENTATION_DASH.sub("", current)
        if updated == current:
            updated = _PRESENTATION_BARE.sub("", current)
        if updated == current:
            updated = _TOPIC_SUFFIX.sub("", current)
        if updated == current:
            break
        removed = current[len(updated) :].strip()
        transformations.append(f"removed presentation suffix {removed!r}")
        current = updated.rstrip()
    return NormalizedValue(current, tuple(transformations))


def parse_youtube_title(
    raw_title: str,
    reported_artists: tuple[str, ...] | None,
) -> TrackCandidate | None:
    """Parse artist/title evidence from a YouTube video title conservatively."""

    normalized = normalize_text(raw_title)
    transformations = list(normalized.transformations)
    title_text = normalized.value
    without_count = _LEADING_BROWSER_COUNT.sub("", title_text)
    if without_count != title_text:
        transformations.append("removed browser notification-count prefix")
        title_text = without_count
    without_youtube = _BROWSER_YOUTUBE_SUFFIX.sub("", title_text)
    if without_youtube != title_text:
        transformations.append("removed browser presentation suffix '- YouTube'")
        title_text = without_youtube.rstrip()

    parts = re.split(r"\s+-\s+", title_text, maxsplit=1)
    reported_keys = {
        comparison_key(artist) for artist in reported_artists or () if artist.strip()
    }
    quotation = re.fullmatch(r"(.+?)\s*[「『](.+?)[」』](.*)", title_text)
    if quotation is not None:
        suffix = quotation.group(3).strip()
        if suffix and not _FEATURING.match(suffix):
            return None
        if _RECORDING_DECORATION.search(suffix):
            return None
        parts = [quotation.group(1).strip(), quotation.group(2).strip()]
        transformations.append("parsed Japanese artist/title quotation")
        if suffix:
            transformations.append(f"preserved featured performer credit: {suffix}")
    elif len(parts) == 2:
        # Only reverse the conventional order when the reported artist
        # corroborates the right-hand side. A title's internal hyphen is not
        # evidence of a different artist.
        if (
            comparison_key(parts[1]) in reported_keys
            and comparison_key(parts[0]) not in reported_keys
        ):
            parts.reverse()
            transformations.append("reported artist corroborates title/artist order")
    elif len(reported_keys) == 1 and _FEATURING.search(title_text):
        # A credit-bearing browser title plus one reported artist supplies a
        # bounded alternative to a separator; ordinary unstructured videos
        # continue to have no inferred musical artist.
        parts = [
            next(artist for artist in reported_artists or () if artist.strip()),
            title_text,
        ]
        transformations.append(
            "used reported artist with explicit featured performer credit"
        )
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return None
    artist_value = normalize_artist(parts[0].strip())
    transformations.extend(artist_value.transformations)
    transformations.append("split artist/title separator")
    candidate_title = parts[1].strip()

    if candidate_title.startswith('"'):
        closing_quote = candidate_title.find('"', 1)
        if closing_quote > 1:
            quoted = candidate_title[1:closing_quote].strip()
            remainder = candidate_title[closing_quote + 1 :].strip()
            if remainder.startswith("-"):
                transformations.append(f"removed uploader/channel suffix {remainder!r}")
                candidate_title = quoted

    if candidate_title == parts[1].strip():
        uploader_keys = {
            comparison_key(artist)
            for artist in reported_artists or ()
            if artist.strip()
        }
        title_segments = re.split(r"\s+-\s+", candidate_title)
        if len(title_segments) > 1:
            suffix_key = comparison_key(title_segments[-1])
            if any(suffix_key.startswith(key) for key in uploader_keys if key):
                removed = f"- {title_segments[-1]}"
                transformations.append(f"removed uploader/channel suffix {removed!r}")
                candidate_title = " - ".join(title_segments[:-1]).strip()

    cleaned_title = _remove_presentation_suffix(candidate_title)
    transformations.extend(cleaned_title.transformations)
    final_title = cleaned_title.value.strip().strip('"').strip()
    credit = _FEATURING.search(final_title)
    if credit is not None and credit.start() > 0:
        base = final_title[: credit.start()].rstrip()
        # Do not strip parenthesized recording/version descriptions; only a
        # trailing, unparenthesized performer credit is a title decoration.
        if not any(char in base for char in "([") and not _RECORDING_DECORATION.search(
            final_title[credit.start() :]
        ):
            transformations.append(
                f"preserved featured performer credit: {final_title[credit.start() :]}"
            )
            final_title = base
    if not artist_value.value or not final_title:
        return None
    return TrackCandidate(
        title=final_title,
        artists=(artist_value.value,),
        album=None,
        duration_us=None,
        evidence=("artist/title parsed from video title",),
        transformations=tuple(dict.fromkeys(transformations)),
    )
