"""Differential and adversarial validation for the native LRC parser."""

from __future__ import annotations

import random
import unicodedata

import pytest

from konokashi import _lrc_native
from konokashi.domain.lyrics import TimingProvenance
from konokashi.infrastructure.lyrics.lrc import (
    MAX_LYRIC_TIMESTAMP_MS,
    MAX_LYRICS_TEXT_CHARS,
    parse_lyrics_text,
)
from konokashi.infrastructure.lyrics.lrc_reference import parse_lyrics_text_python


def _assert_parity(
    text: str,
    *,
    duration_ms: int | None = None,
    provenance: TimingProvenance | None = TimingProvenance.PROVIDER,
) -> None:
    assert parse_lyrics_text(
        text,
        duration_ms=duration_ms,
        timing_provenance=provenance,
    ) == parse_lyrics_text_python(
        text,
        duration_ms=duration_ms,
        timing_provenance=provenance,
    )


def test_public_parser_selects_native_implementation_and_retains_oracle() -> None:
    assert parse_lyrics_text is not parse_lyrics_text_python
    parsed = _lrc_native.parse("[00:01.00]Native", None)

    assert type(parsed).__module__ == "konokashi._lrc_native"
    assert parsed.status == "synced"
    assert parsed.lines[0].text == "Native"


def test_native_bytes_transport_is_bounded_and_rejects_invalid_utf8() -> None:
    parsed = _lrc_native.parse("君\r\n声".encode())

    assert parsed.status == "plain"
    assert parsed.normalized_text == "君\n声"
    for malformed in (
        b"\xff",
        b"\xc0\x80",
        b"\xed\xa0\x80",
        b"\xf4\x90\x80\x80",
    ):
        with pytest.raises(ValueError, match="invalid UTF-8"):
            _lrc_native.parse(malformed)

    oversized = _lrc_native.parse(b"x" * (MAX_LYRICS_TEXT_CHARS + 1))
    assert oversized.status == "invalid"
    assert oversized.diagnostics == ["lyrics text exceeds the bounded parser size"]


@pytest.mark.parametrize(
    ("text", "duration_ms"),
    (
        ("\ufeff[ar:歌手]\r\n[ti:題名]\r[00:17.12]君の声", None),
        ("[00:01.00][00:02.000]Chorus\n[00:03.00]Chorus", None),
        ("[offset:125]\n[00:01.25]Line", None),
        ("[offset:-2000]\n[00:01.00]Line", None),
        ("[00:17.1]Convincing but malformed\nordinary line", None),
        ("[offset:later]\n[00:01.00]Line", None),
        (f"[offset:{10**100}]\n[00:01.00]Line", None),
        ("[00:03.00]Third\n[00:01.00]First\n[00:01.00]Also first", None),
        ("[03:30.00]Late", 180_000),
        ("君の声\n\n君の声", None),
        ("[ar:Artist]\n[ti:Title]", None),
        ("\n\n", None),
        ("[ar Artist]\nActual lyric", None),
        ("[foo:bar]\n[00:01.00]<00:01.10>君の <00:01.80>声", None),
        ("[offset:25]\n[00:01.00][00:03.00]<00:01.10>A <00:01.50>B", None),
        ("[00:01.00]<00:01.80>later <00:01.20>earlier", None),
        (
            "[\uff10\uff10:\uff10\uff11.\uff12\uff15]Fullwidth timestamp-like input",
            None,
        ),
        ("[\uff10\uff10:0\uff11.\uff12\uff15]Mixed Unicode decimal digits", None),
        ("[offset:1_0_0]\n[00:01.00]Underscored offset", None),
        ("[offset:\u00a0125\u3000]\n[00:01.00]Unicode trim", None),
        ("[ar\u00a0Artist]\nActual lyric", None),
        ("[AR Artist]\nActual lyric", None),
        ("\ufeff\ufeff[00:01.00]One BOM only", None),
        ("[ar:x]y]\nActual lyric", None),
        ("[00:01.00]<00:01.00>A<00:01.00>B", None),
        ("[00:00.00]Zero", 10**100),
        ("[00:00.00]Zero", -(10**100)),
        ("[999:59.999]Maximum syntax", MAX_LYRIC_TIMESTAMP_MS),
        ("[999:59.999]Maximum syntax", -2_000),
    ),
)
@pytest.mark.parametrize(
    "provenance",
    (TimingProvenance.PROVIDER, TimingProvenance.IMPORTED, None),
)
def test_native_matches_python_oracle_on_frozen_semantic_cases(
    text: str,
    duration_ms: int | None,
    provenance: TimingProvenance | None,
) -> None:
    _assert_parity(text, duration_ms=duration_ms, provenance=provenance)


def _digits(value: str, alphabet: str) -> str:
    return "".join(alphabet[int(character)] for character in value)


def _timestamp(generator: random.Random, *, enhanced: bool = False) -> str:
    minute = str(generator.randint(0, 999))
    second = f"{generator.randint(0, 59):02d}"
    fraction_width = generator.choice((2, 3))
    fraction = f"{generator.randrange(10**fraction_width):0{fraction_width}d}"
    alphabet = generator.choice(
        (
            "0123456789",
            "\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19",
            "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669",
        )
    )
    # Python's parser intentionally requires the seconds tens digit to be ASCII
    # while its \d groups accept Unicode decimal digits.
    minute = _digits(minute, alphabet)
    second = second[0] + _digits(second[1], alphabet)
    fraction = _digits(fraction, alphabet)
    opening, closing = ("<", ">") if enhanced else ("[", "]")
    return (
        f"{opening}{minute}:{second}{generator.choice(('.', ':'))}{fraction}{closing}"
    )


def _random_document(generator: random.Random) -> tuple[str, int | None]:
    lines: list[str] = []
    for _ in range(generator.randint(0, 12)):
        kind = generator.randrange(8)
        if kind == 0:
            lines.append(generator.choice(("君の声", "hello", "🙂", "", "　")))
        elif kind == 1:
            key = generator.choice(("ar", "TI", "album-id", "foo"))
            value = generator.choice(("Artist", " 歌手 ", "\u00a0value\u3000", ""))
            lines.append(f"[{key}:{value}]")
        elif kind == 2:
            offset = generator.choice(
                (
                    str(generator.randint(-5_000, 5_000)),
                    "1_000",
                    "later",
                    str(10**30),
                    "\u00a0-25\u3000",
                )
            )
            lines.append(f"[offset:{offset}]")
        elif kind == 3:
            stamps = "".join(
                _timestamp(generator) for _ in range(generator.randint(1, 3))
            )
            lines.append(stamps + generator.choice(("Line", "君の声", "", "A B")))
        elif kind == 4:
            lines.append(
                _timestamp(generator)
                + _timestamp(generator, enhanced=True)
                + "君の "
                + _timestamp(generator, enhanced=True)
                + "声"
            )
        elif kind == 5:
            lines.append(
                generator.choice(
                    (
                        "[00:1.00]bad",
                        "[00:61.00]bad",
                        "[1:anything]",
                        "[\uff11\uff12:x]",
                    )
                )
            )
        elif kind == 6:
            lines.append(generator.choice(("[ar Artist]", "[OFFSET value]", "[ti]")))
        else:
            alphabet = "[]<>:.abcXYZ012\uff19\u0660 君🙂_-"
            lines.append(
                "".join(
                    generator.choice(alphabet) for _ in range(generator.randint(0, 35))
                )
            )
    separator = generator.choice(("\n", "\r\n", "\r"))
    text = separator.join(lines)
    if generator.randrange(5) == 0:
        text = "\ufeff" + text
    duration = generator.choice(
        (None, -(10**30), -2_001, -2_000, 0, 180_000, MAX_LYRIC_TIMESTAMP_MS, 10**30)
    )
    return text, duration


@pytest.mark.parametrize("seed", range(64))
def test_seeded_randomized_native_python_parity(seed: int) -> None:
    generator = random.Random(seed)

    for _ in range(80):
        text, duration = _random_document(generator)
        _assert_parity(text, duration_ms=duration)


def test_parser_size_boundary_has_exact_native_reference_parity() -> None:
    _assert_parity("界" * MAX_LYRICS_TEXT_CHARS)
    _assert_parity("界" * (MAX_LYRICS_TEXT_CHARS + 1))


def test_every_runtime_unicode_decimal_digit_has_native_reference_parity() -> None:
    for codepoint in range(0x110000):
        digit = chr(codepoint)
        if unicodedata.category(digit) != "Nd":
            continue
        # The seconds-tens position is intentionally ASCII in the frozen regex;
        # every other numeric position uses Python's Unicode-aware \d semantics.
        _assert_parity(f"[{digit}:0{digit}.{digit}{digit}]Digit")
