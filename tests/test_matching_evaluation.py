"""Development-only matching corpus replay and metric regressions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from scripts.evaluate_matching import (
    EvaluationInputError,
    evaluate,
    load_cases,
    main,
)

REPOSITORY_ROOT = Path(__file__).parents[1]
FIXTURE = (
    REPOSITORY_ROOT
    / "tests"
    / "fixtures"
    / "matching_evaluation"
    / "synthetic_cases.json"
)


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
