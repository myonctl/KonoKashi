"""Bounded Stage 5 CLI diagnostics and fresh-process correction workflow."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from konokashi import cli
from konokashi.domain.lyrics import (
    LyricsProviderCandidate,
    LyricsProviderResult,
    LyricsProviderStatus,
)
from tests.test_lyrics_cli import _CliProvider, _runtime


def _japanese_result() -> LyricsProviderResult:
    return LyricsProviderResult(
        LyricsProviderStatus.RESULTS,
        (
            LyricsProviderCandidate(
                "LRCLIB",
                "stage5-cli",
                "Every Single Day",
                "S3RL ft. JessKah",
                None,
                221_400,
                False,
                "君の声が聞こえる\nきみのこえ\nカタカナ\n君と dance tonight",
                (
                    "[00:01.00]君の声が聞こえる\n"
                    "[00:02.00]きみのこえ\n"
                    "[00:03.00]カタカナ\n"
                    "[00:04.00]君と dance tonight"
                ),
            ),
        ),
        raw_payload=b"stage5-cli-provider-result",
    )


def _ambiguous_han_result() -> LyricsProviderResult:
    return LyricsProviderResult(
        LyricsProviderStatus.RESULTS,
        (
            LyricsProviderCandidate(
                "LRCLIB",
                "language-cli",
                "Every Single Day",
                "S3RL ft. JessKah",
                None,
                221_400,
                False,
                "東京",
                "[00:01.00]東京",
            ),
        ),
        raw_payload=b"language-cli-provider-result",
    )


def test_romanize_diagnostics_are_bounded_and_corrections_persist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "stage5-cli.sqlite3"
    provider = _CliProvider(_japanese_result())

    assert (
        cli.main(
            ["lyrics", "romanize", "current", "--language", "ja"],
            runtime_factory=_runtime,
            lyrics_provider_factory=lambda: provider,
            database_path=path,
        )
        == 0
    )
    generated = capsys.readouterr().out
    line_id_match = re.search(r"original \[([^]]+)]", generated)
    assert line_id_match is not None
    line_id = line_id_match.group(1)
    assert "generation: generated 4" in generated
    assert "romanized [generated" in generated
    assert "Kimi no koe ga kikoeru" in generated
    assert "... 1 more lines; use --full" in generated
    assert "Kimi to dance tonight" not in generated
    assert "network:" not in generated
    assert "Traceback" not in generated

    offline = _CliProvider(
        LyricsProviderResult(
            LyricsProviderStatus.UNAVAILABLE, diagnostics=("must not be called",)
        )
    )
    common = {
        "runtime_factory": _runtime,
        "lyrics_provider_factory": lambda: offline,
        "database_path": path,
    }
    assert (
        cli.main(
            [
                "lyrics",
                "representations",
                "set",
                "current",
                "--offline",
                "--line-id",
                line_id,
                "--kind",
                "romanized",
                "--text",
                "Kyō no approved voice",
            ],
            **common,
        )
        == 0
    )
    assert "state: unreviewed" in capsys.readouterr().out
    assert (
        cli.main(
            [
                "lyrics",
                "representations",
                "approve",
                "current",
                "--offline",
                "--line-id",
                line_id,
                "--kind",
                "romanized",
            ],
            **common,
        )
        == 0
    )
    capsys.readouterr()
    assert (
        cli.main(["lyrics", "representations", "current", "--offline"], **common) == 0
    )
    approved = capsys.readouterr().out
    assert "romanized [approved]: Kyō no approved voice" in approved
    assert offline.calls == 0

    assert (
        cli.main(
            [
                "lyrics",
                "representations",
                "reset",
                "current",
                "--offline",
                "--line-id",
                line_id,
                "--kind",
                "romanized",
            ],
            **common,
        )
        == 0
    )
    assert "Removed representation decision" in capsys.readouterr().out
    assert (
        cli.main(["lyrics", "representations", "current", "--offline"], **common) == 0
    )
    assert "romanized [generated" in capsys.readouterr().out

    assert (
        cli.main(
            [
                "lyrics",
                "representations",
                "reject",
                "current",
                "--offline",
                "--line-id",
                line_id,
                "--kind",
                "romanized",
            ],
            **common,
        )
        == 0
    )
    assert "state: rejected" in capsys.readouterr().out
    assert (
        cli.main(["lyrics", "representations", "current", "--offline"], **common) == 0
    )
    assert "romanized/transliterated [rejected]: unavailable" in capsys.readouterr().out


def test_unsupported_or_latin_generation_has_no_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "latin-cli.sqlite3"
    provider = _CliProvider(
        LyricsProviderResult(
            LyricsProviderStatus.RESULTS,
            (
                LyricsProviderCandidate(
                    "LRCLIB",
                    "latin-cli",
                    "Every Single Day",
                    "S3RL ft. JessKah",
                    None,
                    221_400,
                    False,
                    "I love you\n123 ♪",
                    None,
                ),
            ),
            raw_payload=b"latin-cli",
        )
    )

    assert (
        cli.main(
            ["lyrics", "romanize", "current"],
            runtime_factory=_runtime,
            lyrics_provider_factory=lambda: provider,
            database_path=path,
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "generated 0" in output
    assert "unavailable 2" in output
    assert "representation candidates: 0" in output
    assert "Traceback" not in output


def test_language_override_cli_persists_routes_and_resets_exact_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "language-cli.sqlite3"
    provider = _CliProvider(_ambiguous_han_result())
    common = {
        "runtime_factory": _runtime,
        "lyrics_provider_factory": lambda: provider,
        "database_path": path,
    }

    assert cli.main(["lyrics", "romanize", "current"], **common) == 0
    unavailable = capsys.readouterr().out
    assert "generated 0" in unavailable
    assert "unavailable 1" in unavailable
    assert "too short" in unavailable

    offline = _CliProvider(
        LyricsProviderResult(
            LyricsProviderStatus.UNAVAILABLE, diagnostics=("must not be called",)
        )
    )
    common["lyrics_provider_factory"] = lambda: offline
    assert cli.main(["lyrics", "language", "set", "zh", "--offline"], **common) == 0
    saved = capsys.readouterr().out
    assert "Saved user-approved lyric language" in saved
    assert "language: zh" in saved
    assert "representations generated: 1" in saved
    assert "original lyrics: unchanged" in saved

    assert cli.main(["lyrics", "language", "current", "--offline"], **common) == 0
    current = capsys.readouterr().out
    assert "user-approved language: zh" in current
    assert "effective routing language: zh" in current
    assert "user-approved document language override" in current
    assert offline.calls == 0

    assert cli.main(["lyrics", "language", "reset", "--offline"], **common) == 0
    reset = capsys.readouterr().out
    assert "Removed user-approved lyric language" in reset
    assert "representations generated automatically: 0" in reset
    assert (
        cli.main(["lyrics", "representations", "current", "--offline"], **common) == 0
    )
    final = capsys.readouterr().out
    assert "user-approved language: none" in final
    assert "effective routing language: ambiguous" in final
    assert "romanized/transliterated [unavailable]" in final
