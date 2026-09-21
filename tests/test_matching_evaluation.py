"""Development-only matching corpus replay and metric regressions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from scripts.evaluate_matching import (
    EvaluationInputError,
    _human,
    _provider,
    evaluate,
    load_cases,
    main,
)

from konokashi.domain.lyrics import LyricsProviderStatus, LyricsQuery

REPOSITORY_ROOT = Path(__file__).parents[1]
FIXTURE = (
    REPOSITORY_ROOT
    / "tests"
    / "fixtures"
    / "matching_evaluation"
    / "synthetic_cases.json"
)
YOUTUBE_FIXTURE = FIXTURE.with_name("youtube_automatic_cases.json")
ALBUM_FREE_GET_FIXTURE = FIXTURE.with_name("album_free_get_cases.json")
BROWSER_URL_LESS_FIXTURE = FIXTURE.with_name("browser_url_less_cases.json")


def _payload() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_synthetic_corpus_covers_every_phase_one_outcome_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_socket(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("matching evaluation attempted network access")

    monkeypatch.setattr("socket.socket", forbidden_socket)

    report = evaluate((FIXTURE,))

    assert report.metrics.total_cases == 10
    assert report.metrics.passed_cases == 10
    assert report.metrics.expected_automatic_accepts == 4
    assert report.metrics.correct_automatic_accepts == 4
    assert report.metrics.wrong_automatic_accepts == 0
    assert report.metrics.unnecessary_ambiguities == 0
    assert report.metrics.missed_matches == 0
    assert report.metrics.wrong_timing_trust == 0
    assert report.metrics.fallback_cases == 6
    assert report.metrics.fallback_failures == 0
    assert report.metrics.provider_search_fallback_cases == 1
    assert report.metrics.provider_search_fallback_failures == 0
    assert {case.expected.category for case in report.cases} == {
        "automatic-accept",
        "ambiguous",
        "reject",
        "plain-only-fallback",
        "synced-trusted",
        "synced-timing-rejected",
        "local-override-wins",
        "approved-user-match-wins",
        "rejected-result-remains-rejected",
        "provider-search-fallback",
    }


def test_report_retains_structured_real_match_factors() -> None:
    report = evaluate((FIXTURE,))
    accepted = next(
        case for case in report.cases if case.case_id == "synthetic-auto-accept"
    )

    assert accepted.actual.factors[0].confidence == "High"
    assert accepted.actual.factors[0].text_confidence == "High"
    assert accepted.actual.factors[0].timing_confidence == "High"
    assert accepted.actual.track.title == "Glass Horizon"
    assert accepted.actual.track.artists == ("Aster Vale",)
    assert accepted.actual.track.confidence == "High"
    assert ("title", "mpris-title") in accepted.actual.track.field_provenance
    assert "ordered main-artist credits match" in accepted.actual.factors[0].evidence
    assert "normalized title matches" in accepted.actual.factors[0].evidence
    assert "duration differs by 0 ms" in accepted.actual.factors[0].evidence


def test_automatic_youtube_corpus_replays_the_real_frontend_retry_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_socket(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("automatic YouTube evaluation attempted network access")

    monkeypatch.setattr("socket.socket", forbidden_socket)

    report = evaluate((YOUTUBE_FIXTURE,))

    assert report.metrics.passed_cases == 2
    assert report.metrics.expected_automatic_accepts == 2
    assert report.metrics.correct_automatic_accepts == 2
    assert report.metrics.wrong_automatic_accepts == 0
    assert report.metrics.youtube_enrichment_attempts == 1
    assert report.metrics.youtube_metadata_network_calls == 1
    assert report.metrics.youtube_enrichment_fixed_failures == 1
    assert report.metrics.known_supported_cases == 0
    assert report.metrics.unclassified_support_cases == 2
    assert report.metrics.correct_automatic_supported == 0
    assert report.metrics.ambiguous_supported == 0
    assert report.metrics.missed_supported == 0
    recovered, immediate = report.cases
    assert recovered.actual.enrichment_used is True
    assert recovered.actual.enrichment_network_used is True
    assert immediate.actual.enrichment_used is False
    assert immediate.actual.enrichment_network_used is False
    assert recovered.actual.track.artists == ()
    assert any(
        "automatic YouTube retry" in item for item in recovered.actual.diagnostics
    )
    assert "youtube-enrichment:structured-music-fields" in (
        recovered.actual.factors[0].strategy
    )


def test_replay_reports_per_case_decision_latency_and_nearest_rank_p95(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = iter((1.0, 1.005, 2.0, 2.025))
    monkeypatch.setattr("scripts.evaluate_matching.perf_counter", lambda: next(values))

    report = evaluate((YOUTUBE_FIXTURE,))

    assert [case.actual.decision_latency_ms for case in report.cases] == [5.0, 25.0]
    assert report.metrics.replay_decision_median_ms == 15.0
    assert report.metrics.replay_decision_p95_ms == 25.0
    assert "not live playback latency" in _human(report)


def test_youtube_enrichment_contribution_requires_a_correct_outcome(
    tmp_path: Path,
) -> None:
    payload = cast(
        dict[str, Any], json.loads(YOUTUBE_FIXTURE.read_text(encoding="utf-8"))
    )
    payload["cases"] = [payload["cases"][0]]
    payload["cases"][0]["expected"]["record_id"] = "different-record"
    corpus = tmp_path / "wrong-youtube-oracle.json"
    corpus.write_text(json.dumps(payload), encoding="utf-8")

    report = evaluate((corpus,))

    assert report.metrics.wrong_automatic_accepts == 1
    assert report.metrics.youtube_enrichment_attempts == 1
    assert report.metrics.youtube_enrichment_fixed_failures == 0
    assert report.metrics.correct_automatic_supported == 0


def test_known_supported_denominator_requires_an_explicit_oracle_label(
    tmp_path: Path,
) -> None:
    payload = cast(
        dict[str, Any], json.loads(YOUTUBE_FIXTURE.read_text(encoding="utf-8"))
    )
    payload["cases"] = [payload["cases"][0]]
    payload["cases"][0]["expected"]["known_supported"] = True
    corpus = tmp_path / "labeled-known-supported.json"
    corpus.write_text(json.dumps(payload), encoding="utf-8")

    report = evaluate((corpus,))

    assert report.metrics.known_supported_cases == 1
    assert report.metrics.unclassified_support_cases == 0
    assert report.metrics.correct_automatic_supported == 1
    assert report.metrics.timing_unverified_supported_cases == 1
    assert report.metrics.correct_automatic_supported_timing_unverified == 1


def test_known_supported_record_oracle_is_separate_from_replay_expectation(
    tmp_path: Path,
) -> None:
    payload = cast(
        dict[str, Any], json.loads(YOUTUBE_FIXTURE.read_text(encoding="utf-8"))
    )
    payload["cases"] = [payload["cases"][0]]
    expected = payload["cases"][0]["expected"]
    expected["known_supported"] = True
    expected["record_id"] = "another-record"
    expected["supported_record_ids"] = ["invented-italian-provider-record"]
    corpus = tmp_path / "separate-record-oracle.json"
    corpus.write_text(json.dumps(payload), encoding="utf-8")

    report = evaluate((corpus,))

    assert report.metrics.passed_cases == 0
    assert report.metrics.correct_automatic_accepts == 0
    assert report.metrics.correct_automatic_supported == 1
    assert report.metrics.wrong_automatic_accepts == 0


def test_unverified_timing_does_not_claim_wrong_timing_trust(
    tmp_path: Path,
) -> None:
    payload = cast(
        dict[str, Any], json.loads(YOUTUBE_FIXTURE.read_text(encoding="utf-8"))
    )
    payload["cases"] = [payload["cases"][0]]
    expected = payload["cases"][0]["expected"]
    expected["known_supported"] = True
    expected["supported_record_ids"] = ["invented-italian-provider-record"]
    expected["timing_verified"] = False
    expected["timing"] = "plain"
    expected["status"] = "Untimed"
    corpus = tmp_path / "text-supported-timing-unknown.json"
    corpus.write_text(json.dumps(payload), encoding="utf-8")

    report = evaluate((corpus,))

    assert report.metrics.passed_cases == 0
    assert report.metrics.correct_automatic_supported == 1
    assert report.metrics.correct_automatic_supported_timing_unverified == 1
    assert report.metrics.wrong_timing_trust == 0


def test_oracle_labels_wrong_recording_version_separately(tmp_path: Path) -> None:
    payload = cast(
        dict[str, Any], json.loads(YOUTUBE_FIXTURE.read_text(encoding="utf-8"))
    )
    payload["cases"] = [payload["cases"][0]]
    expected = payload["cases"][0]["expected"]
    expected["record_id"] = "correct-other-version-record"
    expected["wrong_version_record_ids"] = ["invented-italian-provider-record"]
    corpus = tmp_path / "wrong-version-oracle.json"
    corpus.write_text(json.dumps(payload), encoding="utf-8")

    report = evaluate((corpus,))

    assert report.metrics.wrong_automatic_accepts == 1
    assert report.metrics.wrong_version_automatic_accepts == 1
    assert report.metrics.correct_automatic_supported == 0


def test_query_specific_provider_replay_does_not_leak_a_candidate_to_wrong_artist(
    tmp_path: Path,
) -> None:
    payload = cast(
        dict[str, Any], json.loads(YOUTUBE_FIXTURE.read_text(encoding="utf-8"))
    )
    payload["cases"] = [payload["cases"][0]]
    case = payload["cases"][0]
    case["youtube_metadata"]["artist"] = "Another Performer"
    case["youtube_metadata"]["description"] = ""
    case["expected"].update(
        {
            "category": "reject",
            "status": "No result",
            "record_id": None,
            "timing": "none",
            "origin": "none",
        }
    )
    corpus = tmp_path / "wrong-artist.json"
    corpus.write_text(json.dumps(payload), encoding="utf-8")

    report = evaluate((corpus,))

    assert report.metrics.passed_cases == 1
    assert report.metrics.youtube_enrichment_attempts == 1
    assert report.metrics.youtube_enrichment_fixed_failures == 0
    assert report.metrics.wrong_automatic_accepts == 0
    assert report.cases[0].actual.factors == ()


def test_query_rule_can_match_album_duration_and_broad_mode() -> None:
    def candidate(record_id: str) -> dict[str, object]:
        return {
            "record_id": record_id,
            "track_name": "Paper Signal",
            "artist_name": "Copper Avenue",
            "duration_ms": 180_000,
            "plain_lyrics": "An invented paper signal.",
        }

    provider = _provider(
        {
            "query_results": [
                {
                    "title": "Paper Signal",
                    "artists": ["Copper Avenue"],
                    "album": "Wrong Album",
                    "duration_ms": 180_000,
                    "broad": False,
                    "result": {"candidates": [candidate("wrong-album")]},
                },
                {
                    "title": "Paper Signal",
                    "artists": ["Copper Avenue"],
                    "album": "Correct Album",
                    "duration_ms": 180_000,
                    "broad": False,
                    "result": {"candidates": [candidate("correct-album")]},
                },
                {
                    "title": "Paper Signal",
                    "artists": ["Copper Avenue"],
                    "album": None,
                    "duration_ms": None,
                    "broad": True,
                    "result": {"candidates": [candidate("no-album")]},
                },
            ]
        },
        "synthetic.provider",
    )

    correct = provider.search(
        LyricsQuery("Paper Signal", ("Copper Avenue",), "Correct Album", 180_000)
    )
    broad = provider.search(
        LyricsQuery(
            "Paper Signal", ("Copper Avenue",), "Correct Album", 180_000, broad=True
        )
    )
    wrong_duration = provider.search(
        LyricsQuery("Paper Signal", ("Copper Avenue",), "Correct Album", 181_000)
    )
    no_album = provider.search(
        LyricsQuery("Paper Signal", ("Copper Avenue",), None, None, broad=True)
    )

    assert correct.candidates[0].record_id == "correct-album"
    assert broad.status is LyricsProviderStatus.NO_RESULT
    assert wrong_duration.status is LyricsProviderStatus.NO_RESULT
    assert no_album.candidates[0].record_id == "no-album"


def test_album_free_get_replay_preserves_recovery_and_conflict() -> None:
    report = evaluate((ALBUM_FREE_GET_FIXTURE,))

    assert report.metrics.total_cases == 2
    assert report.metrics.passed_cases == 2
    assert report.metrics.correct_automatic_accepts == 1
    assert report.metrics.wrong_automatic_accepts == 0
    assert report.cases[0].actual.status == "Timed"
    assert report.cases[1].actual.status == "Ambiguous"


def test_url_less_browser_replay_recovers_clear_title_without_uploader_trust() -> None:
    report = evaluate((BROWSER_URL_LESS_FIXTURE,))

    assert report.metrics.total_cases == 5
    assert report.metrics.passed_cases == 5
    assert report.metrics.correct_automatic_accepts == 2
    assert report.metrics.wrong_automatic_accepts == 0
    assert report.metrics.youtube_enrichment_attempts == 2
    assert report.cases[1].actual.enrichment_used is True
    assert report.cases[1].actual.enrichment_network_used is True
    assert any(
        item.startswith("automatic URL-less browser retry:")
        for item in report.cases[1].actual.diagnostics
    )
    assert [case.actual.status for case in report.cases] == [
        "Ambiguous",
        "Timed",
        "Timed",
        "Ambiguous",
        "Ambiguous",
    ]


def test_wrong_oracle_document_counts_a_dangerous_automatic_accept(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["cases"] = [payload["cases"][0]]
    payload["cases"][0]["expected"]["record_id"] = "known-correct-other-record"
    corpus = tmp_path / "wrong-auto-oracle.json"
    corpus.write_text(json.dumps(payload), encoding="utf-8")

    report = evaluate((corpus,))

    assert report.metrics.passed_cases == 0
    assert report.metrics.wrong_automatic_accepts == 1
    assert report.metrics.correct_automatic_accepts == 0
    assert report.metrics.unnecessary_ambiguities == 0
    assert report.metrics.missed_matches == 0
    assert report.cases[0].mismatches == (
        "record_id: expected 'known-correct-other-record', observed "
        "'auto-accepted-record'",
    )


def test_expected_accept_becoming_ambiguous_is_not_mislabeled_as_a_miss(
    tmp_path: Path,
) -> None:
    payload = _payload()
    ambiguous = payload["cases"][1]
    ambiguous["expected"] = {
        "category": "automatic-accept",
        "status": "Timed",
        "record_id": "twin-record-a",
        "timing": "synced",
        "origin": "provider-auto",
        "network_used": True,
    }
    payload["cases"] = [ambiguous]
    corpus = tmp_path / "unnecessary-ambiguity.json"
    corpus.write_text(json.dumps(payload), encoding="utf-8")

    report = evaluate((corpus,))

    assert report.metrics.unnecessary_ambiguities == 1
    assert report.metrics.missed_matches == 0
    assert report.metrics.wrong_automatic_accepts == 0


def test_expected_accept_becoming_no_result_counts_as_a_miss(tmp_path: Path) -> None:
    payload = _payload()
    no_result = payload["cases"][2]
    no_result["expected"] = {
        "category": "automatic-accept",
        "status": "Timed",
        "record_id": "missing-correct-record",
        "timing": "synced",
        "origin": "provider-auto",
        "network_used": True,
    }
    payload["cases"] = [no_result]
    corpus = tmp_path / "missed-match.json"
    corpus.write_text(json.dumps(payload), encoding="utf-8")

    report = evaluate((corpus,))

    assert report.metrics.missed_matches == 1
    assert report.metrics.unnecessary_ambiguities == 0
    assert report.metrics.wrong_automatic_accepts == 0


def test_synced_result_against_plain_oracle_counts_wrong_timing_trust(
    tmp_path: Path,
) -> None:
    payload = _payload()
    case = payload["cases"][0]
    case["expected"]["timing"] = "plain"
    case["expected"]["status"] = "Untimed"
    payload["cases"] = [case]
    corpus = tmp_path / "wrong-timing.json"
    corpus.write_text(json.dumps(payload), encoding="utf-8")

    report = evaluate((corpus,))

    assert report.metrics.wrong_timing_trust == 1


def test_duplicate_case_ids_are_rejected_even_within_one_file(tmp_path: Path) -> None:
    payload = _payload()
    payload["cases"] = [payload["cases"][0], payload["cases"][0]]
    corpus = tmp_path / "duplicates.json"
    corpus.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvaluationInputError, match="duplicate case id"):
        load_cases((corpus,))


def test_cli_emits_machine_readable_report_and_meaningful_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main((str(FIXTURE), "--json")) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["schema_version"] == 1
    assert output["metrics"]["wrong_automatic_accepts"] == 0
    assert output["cases"][0]["actual"]["factors"]

    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema_version": 999, "cases": []}', encoding="utf-8")
    assert main((str(invalid),)) == 2
    assert "evaluator requires 1" in capsys.readouterr().err

    payload = _payload()
    payload["cases"] = [payload["cases"][0]]
    payload["cases"][0]["expected"]["record_id"] = "different-record"
    mismatch = tmp_path / "mismatch.json"
    mismatch.write_text(json.dumps(payload), encoding="utf-8")
    assert main((str(mismatch),)) == 1
    assert main((str(mismatch), "--allow-mismatches")) == 0


def test_private_corpus_location_is_ignored_and_documented() -> None:
    ignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")
    fixture_readme = FIXTURE.with_name("README.md").read_text(encoding="utf-8")

    assert "/.matching-evaluation-private/" in ignore
    assert "Do not add that directory to Git" in fixture_readme
    assert "Never retain a playlist" in fixture_readme
    assert "/home/" not in FIXTURE.read_text(encoding="utf-8")
