"""Stage 4 current-lyrics CLI diagnostics without live players or network."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyriflux import cli
from lyriflux.domain.lyrics import (
    LyricsProviderCandidate,
    LyricsProviderResult,
    LyricsProviderStatus,
    LyricsQuery,
)
from lyriflux.domain.models import PlayerInspection, PlayerListResult
from tests.stage2_helpers import fixture_snapshot
from tests.test_players_cli import FakeClient, FakeRuntime


class _CliProvider:
    name = "LRCLIB"

    def __init__(self, result: LyricsProviderResult) -> None:
        self.result = result
        self.calls = 0

    def exact(self, _query: LyricsQuery) -> LyricsProviderResult:
        self.calls += 1
        return self.result

    def search(self, _query: LyricsQuery) -> LyricsProviderResult:
        self.calls += 1
        return self.result

    def parse_cached(self, payload: bytes, *, search: bool) -> LyricsProviderResult:
        del payload, search
        return self.result


def _runtime() -> FakeRuntime:
    snapshot = fixture_snapshot("stage2/youtube_jesskah.json")
    inspection = PlayerInspection(snapshot.service_name, snapshot.bus_name, snapshot)
    return FakeRuntime(FakeClient(PlayerListResult((inspection,)), inspection))


def _provider_result() -> LyricsProviderResult:
    return LyricsProviderResult(
        LyricsProviderStatus.RESULTS,
        (
            LyricsProviderCandidate(
                "LRCLIB",
                "4242",
                "Every Single Day",
                "S3RL ft. JessKah",
                None,
                221_400,
                False,
                "one\ntwo\nthree\nfour\nfive",
                (
                    "[00:01.00]one\n[00:02.00]two\n[00:03.00]three\n"
                    "[00:04.00]four\n[00:05.00]five"
                ),
            ),
        ),
        raw_payload=b"cli-provider-result",
    )


def test_lyrics_current_renders_bounded_preview_and_fresh_offline_cache(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "lyrics-cli.sqlite3"
    provider = _CliProvider(_provider_result())

    first_exit = cli.main(
        ["lyrics", "current"],
        runtime_factory=_runtime,
        lyrics_provider_factory=lambda: provider,
        database_path=database,
    )
    first = capsys.readouterr().out

    assert first_exit == 0
    assert "selected player: plasma-browser-integration" in first
    assert "track: Every Single Day" in first
    assert "artist: S3RL feat. JessKah" in first
    assert "lyrics status: Timed" in first
    assert "lyrics source: LRCLIB" in first
    assert "provider record: 4242" in first
    assert "match confidence: High" in first
    assert "timed lines: 5" in first
    assert "plain lines: 5" in first
    assert "network: used" in first
    assert "[00:01.000] one" in first
    assert "... 2 more lines; use --full" in first
    assert "[00:04.000] four" not in first

    offline_provider = _CliProvider(
        LyricsProviderResult(
            LyricsProviderStatus.UNAVAILABLE,
            diagnostics=("must not be called",),
        )
    )
    second_exit = cli.main(
        ["lyrics", "current", "--offline"],
        runtime_factory=_runtime,
        lyrics_provider_factory=lambda: offline_provider,
        database_path=database,
    )
    second = capsys.readouterr().out

    assert second_exit == 0
    assert "lyrics status: Timed" in second
    assert "lyrics source: LRCLIB (persistent match)" in second
    assert "cache: hit" in second
    assert "network: not used" in second
    assert offline_provider.calls == 0


def test_full_output_requires_explicit_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = _CliProvider(_provider_result())

    exit_code = cli.main(
        ["lyrics", "current", "--full"],
        runtime_factory=_runtime,
        lyrics_provider_factory=lambda: provider,
        database_path=tmp_path / "full.sqlite3",
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "lyrics:" in output
    assert "[00:05.000] five" in output
    assert "more lines" not in output


def test_no_result_is_valid_diagnostic_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = _CliProvider(
        LyricsProviderResult(
            LyricsProviderStatus.NO_RESULT,
            diagnostics=("LRCLIB reported no matching record",),
            raw_payload=b"",
        )
    )

    exit_code = cli.main(
        ["lyrics", "current"],
        runtime_factory=_runtime,
        lyrics_provider_factory=lambda: provider,
        database_path=tmp_path / "none.sqlite3",
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "lyrics status: No result" in output
    assert "network: used" in output
    assert "LRCLIB reported no matching record" in output
    assert "Traceback" not in output


def test_offline_and_refresh_are_mutually_exclusive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    called = False

    def unexpected_runtime() -> FakeRuntime:
        nonlocal called
        called = True
        return _runtime()

    exit_code = cli.main(
        ["lyrics", "current", "--offline", "--refresh"],
        runtime_factory=unexpected_runtime,
        database_path=tmp_path / "unused.sqlite3",
    )

    assert exit_code == 2
    assert called is False
    assert "cannot be used together" in capsys.readouterr().err


def test_no_selectable_player_is_controlled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = fixture_snapshot("stage2/youtube_jesskah.json")
    inspection = PlayerInspection(fixture.service_name, fixture.bus_name, fixture)
    runtime = FakeRuntime(FakeClient(PlayerListResult(), inspection))

    exit_code = cli.main(
        ["lyrics", "current"],
        runtime_factory=lambda: runtime,
        database_path=tmp_path / "no-player.sqlite3",
    )

    assert exit_code == 1
    assert "No selectable MPRIS track" in capsys.readouterr().out
