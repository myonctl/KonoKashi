"""Conservative, explainable metadata normalization policy."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from konokashi.domain.tracks import ArtistCredit, TrackCandidate

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
_CREDIT_SEPARATOR = re.compile(r"\s*(?:,|[•·]|&|\band\b)\s*", re.IGNORECASE)
_AMBIGUOUS_TITLE_SEPARATOR = re.compile(r"\s+(?P<separator>[/|:·])\s+")
_SPACED_DASH_SEPARATOR = re.compile(r"\s+-\s+")
_TRAILING_GROUP = re.compile(
    r"^(?P<base>.+?)\s*(?P<group>\[[^\[\]()]+\]|\([^\[\]()]+\))\s*$"
)
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


def parse_artist_credits(values: tuple[str, ...]) -> ArtistCredit:
    """Parse ordered main/featured names without flattening separator evidence."""

    main: list[str] = []
    contributors: list[str] = []

    def append_unique(target: list[str], value: str) -> None:
        normalized = normalize_artist(value).value.strip()
        if normalized and comparison_key(normalized) not in {
            comparison_key(item) for item in (*main, *contributors)
        }:
            target.append(normalized)

    for raw in values:
        normalized = normalize_artist(raw).value
        featured = _FEATURING.split(normalized, maxsplit=1)
        for name in _CREDIT_SEPARATOR.split(featured[0]):
            append_unique(main, name)
        if len(featured) == 2:
            for name in _CREDIT_SEPARATOR.split(featured[1]):
                append_unique(contributors, name)
    return ArtistCredit(tuple(main), tuple(contributors))


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


def parse_youtube_title_candidates(
    raw_title: str,
    reported_artists: tuple[str, ...] | None,
) -> tuple[TrackCandidate, ...]:
    """Return bounded artist/title interpretations with inspectable provenance."""

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

    parts: list[str] = []
    separator = ""
    ambiguous_separator_seen = False
    reported_keys = {
        comparison_key(artist) for artist in reported_artists or () if artist.strip()
    }
    quotation = re.fullmatch(r"(.+?)\s*[「『](.+?)[」』](.*)", title_text)
    if quotation is not None:
        suffix = quotation.group(3).strip()
        if suffix and not _FEATURING.match(suffix):
            return ()
        if _RECORDING_DECORATION.search(suffix):
            return ()
        quoted_title = quotation.group(2).strip()
        parts = [
            quotation.group(1).strip(),
            f"{quoted_title} {suffix}".strip(),
        ]
        separator = "Japanese quotation"
        transformations.append("parsed Japanese artist/title quotation")
        if suffix:
            transformations.append(f"preserved featured performer credit: {suffix}")
    else:
        dash_parts = _SPACED_DASH_SEPARATOR.split(title_text, maxsplit=1)
        if len(dash_parts) == 2:
            parts = dash_parts
            separator = "spaced dash"
            # Only reverse the conventional order when the reported artist
            # corroborates the right-hand side. A title's internal hyphen is not
            # evidence of a different artist.
            if (
                comparison_key(parts[1]) in reported_keys
                and comparison_key(parts[0]) not in reported_keys
            ):
                parts.reverse()
                transformations.append(
                    "reported artist corroborates title/artist order"
                )
        else:
            ambiguous = _AMBIGUOUS_TITLE_SEPARATOR.split(title_text, maxsplit=1)
            # re.split includes the named separator group: left, separator, right.
            if len(ambiguous) == 3:
                ambiguous_separator_seen = True
                left, separator_value, right = ambiguous
                left_key = comparison_key(left)
                right_key = comparison_key(right)
                left_matches = any(
                    left_key == key or left_key.startswith(f"{key} ")
                    for key in reported_keys
                )
                right_matches = any(
                    right_key == key or right_key.startswith(f"{key} ")
                    for key in reported_keys
                )
                if left_matches != right_matches:
                    parts = [left.strip(), right.strip()]
                    if right_matches:
                        parts.reverse()
                        transformations.append(
                            "reported artist corroborates title/artist order"
                        )
                    separator = {
                        "/": "slash",
                        "|": "pipe",
                        ":": "colon",
                        "·": "middle dot",
                    }[separator_value]
                    transformations.append(
                        f"reported artist corroborates ambiguous {separator} separator"
                    )
    if (
        not parts
        and not ambiguous_separator_seen
        and len(reported_keys) == 1
        and _FEATURING.search(title_text)
    ):
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
        separator = "reported-artist evidence"
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return ()
    artist_value = normalize_artist(parts[0].strip())
    artist_credit = parse_artist_credits((artist_value.value,))
    transformations.extend(artist_value.transformations)
    transformations.append(f"split artist/title {separator} separator")
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
    contributors = list(artist_credit.contributors)
    if credit is not None and credit.start() > 0:
        base = final_title[: credit.start()].rstrip()
        suffix = final_title[credit.end() :].strip()
        suffix_version = _TRAILING_GROUP.fullmatch(suffix)
        contributor_text = suffix
        version_group = ""
        if suffix_version is not None:
            contributor_text = suffix_version.group("base").strip()
            version_group = suffix_version.group("group")
        parsed_contributors = parse_artist_credits((contributor_text,)).main_artists
        if parsed_contributors:
            contributors.extend(parsed_contributors)
            transformations.append(
                f"preserved featured performer credit: {final_title[credit.start() :]}"
            )
            final_title = f"{base} {version_group}".strip()
    if not artist_value.value or not final_title:
        return ()
    artist_credit = ArtistCredit(
        artist_credit.main_artists,
        tuple(dict.fromkeys(contributors)),
    )
    primary = TrackCandidate(
        title=final_title,
        artists=(artist_value.value,),
        album=None,
        duration_us=None,
        evidence=("artist/title parsed from video title",),
        transformations=tuple(dict.fromkeys(transformations)),
        strategy=f"youtube-title:{separator}",
        artist_credit=artist_credit,
        field_provenance=(
            ("title", "mpris-title"),
            ("artists", "mpris-title"),
            *((("contributors", "mpris-title"),) if contributors else ()),
        ),
    )
    candidates = [primary]
    trailing = _TRAILING_GROUP.fullmatch(final_title)
    if trailing is not None and parse_title_version(final_title).qualifier is None:
        base_title = trailing.group("base").strip()
        if base_title:
            candidates.append(
                TrackCandidate(
                    title=base_title,
                    artists=primary.artists,
                    album=None,
                    duration_us=None,
                    evidence=(
                        *primary.evidence,
                        "retained optional title variant without "
                        "trailing bracket group",
                    ),
                    transformations=(
                        *primary.transformations,
                        f"optional candidate removed trailing bracket group "
                        f"{trailing.group('group')!r}",
                    ),
                    strategy=f"{primary.strategy}:optional-trailing-group",
                    artist_credit=primary.artist_credit,
                    field_provenance=primary.field_provenance,
                )
            )
    return tuple(candidates)


def parse_youtube_title(
    raw_title: str,
    reported_artists: tuple[str, ...] | None,
) -> TrackCandidate | None:
    """Return the primary conservative candidate for compatibility callers."""

    candidates = parse_youtube_title_candidates(raw_title, reported_artists)
    return candidates[0] if candidates else None
