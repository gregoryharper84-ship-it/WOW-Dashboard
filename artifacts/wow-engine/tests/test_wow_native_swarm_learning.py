"""Regression contracts for bounded, non-fitted WOW Swarm learning."""
import json
from pathlib import Path
import pytest
from tools.wow_native_swarm_learning import _receipt, _history, learn, main


def receipt(run="101", event="2026020385", facts=0, missing=21):
    return {
        "schema_version": "wow.v17.native_swarm_production_readback.v1",
        "github_run_id": run, "source_revision": "d" * 40,
        "pilot": {"status": "VERIFIED_EVENT_OPTED_IN", "requested_count": 1,
                  "sport_key": "icehockey_nhl", "official_event_id": event,
                  "can_execute": False, "may_publish_or_qualify": False},
        "summary": {"status": "RESEARCH_ONLY_HOLD", "workers_completed": 5,
                    "receipt_sha256": "a" * 64,
                    "research_facts_screened": facts, "missing_evidence_count": missing,
                    "conflicts_count": 0, "can_execute": False,
                    "may_publish_or_qualify": False,
                    "prediction_authority": False,
                    "specialist_rescore_verified": False,
                    "independent_source_verification": False,
                    "external_llm_calls": 0, "paid_provider_calls": 0},
        "can_execute": False, "may_publish_or_qualify": False,
    }


def test_no_history_is_a_baseline_not_fake_improvement():
    result = learn(receipt(), [], missing_features=["confirmed_starting_goalies"])
    assert result["status"] == "BASELINE_ESTABLISHED"
    assert result["prior_unique_runs"] == 0
    assert result["next_research_action"] == "ACQUIRE_OR_VERIFY_AUTHORIZED_INPUT:confirmed_starting_goalies"
    assert result["can_execute"] is False
    assert result["may_fit_probability"] is False
    assert result["predictive_accuracy_improvement_verified"] is False


def test_repeated_identical_event_is_a_hold_not_learning():
    prior = [receipt("10"), receipt("20")]
    report = learn(receipt("30"), prior, missing_features=["five_on_five_xg_and_shots"])
    assert report["status"] == "REPEATED_RESEARCH_HOLD"
    assert report["same_event_prior_runs"] == 2
    assert report["prior_distinct_events"] == 1
    assert report["current_screened_facts"] == 0


def test_improvement_really_requires_more_evidence_and_no_new_gaps():
    result = learn(receipt("25", facts=2, missing=19), [receipt("24", facts=0, missing=21)])
    assert result["status"] == "RESEARCH_EVIDENCE_COVERAGE_IMPROVED"
    assert result["predictive_accuracy_improvement_verified"] is False


def test_regression_when_gap_widens_or_facts_disappear():
    assert learn(receipt("30", missing=22), [receipt("20")])["status"] == "RESEARCH_EVIDENCE_COVERAGE_REGRESSED"
    assert learn(receipt("30", facts=0), [receipt("20", facts=3)])["status"] == "RESEARCH_EVIDENCE_COVERAGE_REGRESSED"


@pytest.mark.parametrize("mutation", [
    lambda x: x["summary"].update({"workers_completed": 4}),
    lambda x: x["summary"].update({"paid_provider_calls": 1}),
    lambda x: x["summary"].update({"prediction_authority": True}),
    lambda x: x["summary"].update({"receipt_sha256": "invalid"}),
    lambda x: x["pilot"].update({"requested_count": 2}),
    lambda x: x.update({"can_execute": True}),
    lambda x: x["summary"].update({"independent_source_verification": True}),
    lambda x: x["summary"].update({"research_facts_screened": -1}),
])
def test_invalid_current_receipt_fails_closed(mutation):
    value = receipt()
    mutation(value)
    with pytest.raises(ValueError):
        _receipt(value)


def test_duplicate_prior_runs_do_not_inflate_evidence():
    result = learn(receipt("30"), [receipt("20"), receipt("20")])
    assert result["prior_unique_runs"] == 1
    assert result["same_event_prior_runs"] == 1


def test_invalid_prior_skipped_and_measured(tmp_path):
    good = tmp_path / "a" / "native-swarm-receipt.json"
    bad = tmp_path / "b" / "native-swarm-receipt.json"
    good.parent.mkdir()
    bad.parent.mkdir()
    good.write_text(json.dumps(receipt("10")))
    bad.write_text('{"schema_version":"evil","can_execute":true}')
    rows, rejected = _history([good, bad], "101")
    assert rejected == 1
    assert len(rows) == 1


def test_distinct_event_no_fake_cross_event_improvement():
    result = learn(receipt("30", event="game-B"),
                   [receipt("20", event="game-A", facts=100, missing=0)])
    assert result["status"] == "BASELINE_ESTABLISHED"
    assert result["prior_distinct_events"] == 1
    assert result["same_event_prior_runs"] == 0


def test_history_contains_only_canonical_safe_research_keys():
    result = learn(receipt("30"), [], missing_features=[
        "confirmed_starting_goalies", "confirmed_starting_goalies",
        "api_key", "predicted_winner", "x/../secrets"])
    assert result["missing_features"] == ["confirmed_starting_goalies"]
    assert "x/../secrets" not in str(result)


def test_cli_writes_report_from_real_enriched_shape(tmp_path, monkeypatch):
    current = tmp_path / "current.json"
    out = tmp_path / "learning.json"
    history = tmp_path / "history"
    history.mkdir()
    current.write_text(json.dumps(receipt()))
    source = receipt()
    source["canonical_missing_features"] = [
        "confirmed_starting_goalies", "exact_current_quote", "api_key"]
    current.write_text(json.dumps(source))
    monkeypatch.setattr("sys.argv", ["learner", "--current", str(current),
                                    "--history-dir", str(history), "--output", str(out)])
    assert main() == 0
    report = json.loads(out.read_text())
    assert report["source_run_id"] == "101"
    assert report["status"] == "BASELINE_ESTABLISHED"
    assert "confirmed_starting_goalies" in report["missing_features"]
