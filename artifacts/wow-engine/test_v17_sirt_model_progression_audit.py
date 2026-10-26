"""Adversarial SIRT proof tests; no external data or model execution required."""
from datetime import datetime, timedelta, timezone

import pytest

from v17.sirt_model_progression_audit import audit_challenger_replay, audit_confirmed_recurrence


def pair(i, role, outcome=1, probability=None, **kw):
    t = datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(days=i)
    row = {
        "sport": "NFL", "market_family": "MONEYLINE", "cohort_id": "2026_OCT_TEST",
        "event_id": f"game-{i}", "market_id": "home-win",
        "model_artifact_id": f"fitted-{role.lower()}",
        "prediction_receipt_ref": f"model:{role}:{i}",
        "source_snapshot_ref": f"snapshot:{i}",
        "calibration_evidence_ref": f"calibration:{role}:{i}",
        "out_of_sample_split_ref": "holdout:oct2026",
        "model_owner_principal": "model-owner",
        "settlement_verifier_principal": "independent-settlement",
        "settlement_evidence_ref": f"settlement:{i}",
        "role": role, "regime": "PREGAME", "out_of_sample_verified": True,
        "settlement_verified": True, "point_in_time_snapshot_verified": True,
        "calibrated_probability": probability if probability is not None else (.65 if role == "CHAMPION" else .75),
        "outcome": outcome,
        "predicted_at": (t - timedelta(hours=4)).isoformat(),
        "event_start_at": t.isoformat(),
        "settled_at": (t + timedelta(hours=4)).isoformat(),
    }
    row.update(kw)
    return row


def full_sample(n=30):
    return [pair(i, role) for i in range(n) for role in ("CHAMPION", "CHALLENGER")]


def test_matched_replay_is_still_research_only_even_when_challenger_better():
    result = audit_challenger_replay(full_sample())
    assert result["status"] == "RESEARCH_EVALUATION_ONLY"
    assert result["comparisons"][0]["matched_pairs"] == 30
    assert result["comparisons"][0]["challenger_minus_champion_brier"] < 0
    assert result["comparisons"][0]["assessment"] == "RESEARCH_ONLY_NO_PROMOTION"
    assert result["model_promotion_authorized"] is False
    assert result["can_execute"] is False
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"


@pytest.mark.parametrize("change", [
    {"predicted_at": "2026-10-02T01:00:00+00:00"},
    {"predicted_at": "2026-10-01T00:00:00"},
    {"regime": "LIVE"},
    {"outcome": True},
    {"out_of_sample_verified": None},
    {"point_in_time_snapshot_verified": False},
    {"calibrated_probability": float("nan")},
    {"calibration_evidence_ref": ""},
    {"prediction_receipt_ref": ""},
    {"settlement_evidence_ref": ""},
    {"out_of_sample_split_ref": ""},
    {"settlement_verifier_principal": "model-owner"},
])
def test_bad_evidence_blocks_entire_cohort(change):
    obs = full_sample()
    obs[0] = {**obs[0], **change}
    result = audit_challenger_replay(obs)
    assert result["status"] == "EVIDENCE_HOLD"
    assert result["comparisons"][0]["challenger_brier"] is None


def test_missing_or_duplicate_challenger_blocks_full_cohort():
    obs = full_sample()
    obs.pop()
    assert "UNMATCHED_EVENT_MARKET" in audit_challenger_replay(obs)["comparisons"][0]["blockers"]
    obs = full_sample()
    obs.append(dict(obs[0]))
    assert "DUPLICATE_PREDICTION" in audit_challenger_replay(obs)["findings"][-2]


def test_unverified_outcome_disagreement_blocks_cohort():
    obs = full_sample()
    obs[1] = {**obs[1], "outcome": 0}
    assert "SETTLEMENT_DISAGREEMENT" in audit_challenger_replay(obs)["comparisons"][0]["blockers"]


def test_no_sample_or_insufficient_denominator_is_held():
    assert audit_challenger_replay([])["status"] == "EVIDENCE_HOLD"
    small = audit_challenger_replay(full_sample(2))
    assert "INSUFFICIENT_MATCHED_SAMPLE" in small["comparisons"][0]["blockers"]
    with pytest.raises(ValueError):
        audit_challenger_replay(full_sample(), minimum_pairs=0)


def test_different_model_artifact_within_cohort_cannot_be_merged():
    obs = full_sample()
    obs[0] = {**obs[0], "model_artifact_id": "another-version"}
    assert "MULTIPLE_ARTIFACTS_IN_COHORT" in audit_challenger_replay(obs)["comparisons"][0]["blockers"]


def test_confirmed_recurrence_requires_regression_proof_and_does_not_self_fix():
    incidents = [
        {"incident_id": "1", "root_cause_status": "CONFIRMED", "confirmed_root_cause_id": "identity-key", "root_cause_evidence_ref": "issue:1", "prevention_regression_ref": "test:one"},
        {"incident_id": "2", "root_cause_status": "CONFIRMED", "confirmed_root_cause_id": "identity-key", "root_cause_evidence_ref": "issue:2"},
        {"incident_id": "3", "root_cause_status": "SUSPECTED", "confirmed_root_cause_id": "identity-key"},
    ]
    result = audit_confirmed_recurrence(incidents)
    fam = result["families"][0]
    assert fam["occurrences"] == 2
    assert fam["prevention_test_evidence_missing_for"] == ["2"]
    assert fam["recommendation"] == "ENGINEERING_PREVENTION_HANDOFF"
    assert result["can_execute"] is False
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_duplicate_incident_is_not_inflated():
    rows = [{"incident_id": "1", "root_cause_status": "CONFIRMED", "root_cause_code": "x", "root_cause_evidence_ref": "issue:1"}] * 2
    result = audit_confirmed_recurrence(rows)
    assert result["status"] == "EVIDENCE_HOLD"
    assert result["families"][0]["occurrences"] == 1


def test_root_cause_without_evidence_is_not_confirmed_recurrence():
    rows = [{"incident_id": "1", "root_cause_status": "CONFIRMED", "root_cause_code": "x"},
            {"incident_id": "2", "root_cause_status": "CONFIRMED", "root_cause_code": "x",
             "root_cause_evidence_ref": "issue:2"}]
    result = audit_confirmed_recurrence(rows)
    assert result["families"][0]["occurrences"] == 1
    assert result["families"][0]["recurring"] is False


def test_snapshot_requires_both_collections_and_never_claims_production():
    from v17.sirt_model_progression_audit import audit_evidence_snapshot
    assert audit_evidence_snapshot({"observations": []})["status"] == "EVIDENCE_HOLD"
    result = audit_evidence_snapshot({"observations": full_sample(), "incidents": []})
    assert result["status"] == "RESEARCH_EVALUATION_ONLY"
    assert result["production_source_verified"] is False
    assert result["model_promotion_authorized"] is False


def test_offline_cli_cannot_turn_invalid_input_into_pass(tmp_path):
    import subprocess
    import sys
    import json
    from pathlib import Path
    target = Path(__file__).resolve().parent / "v17" / "sirt_model_progression_audit.py"
    inp = tmp_path / "bad.json"
    inp.write_text(json.dumps({"observations": [], "incidents": []}))
    result = subprocess.run([sys.executable, str(target), "--evidence-json", str(inp)],
                            capture_output=True, text=True)
    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["status"] == "EVIDENCE_HOLD"
    assert body["can_execute"] is False


def test_different_holdout_splits_cannot_be_compared():
    obs = full_sample()
    obs[1] = {**obs[1], "out_of_sample_split_ref": "different_holdout"}
    result = audit_challenger_replay(obs)
    assert result["status"] == "EVIDENCE_HOLD"
    assert "HOLDOUT_SPLIT_DISAGREEMENT" in result["comparisons"][0]["blockers"]
    assert result["comparisons"][0]["challenger_minus_champion_brier"] is None


def test_phase3_quality_metrics_and_chronological_shift_remain_descriptive():
    result = audit_challenger_replay(full_sample())
    data = result["comparisons"][0]
    assert data["champion_quality"]["sample_count"] == 30
    assert data["challenger_quality"]["brier"] < data["champion_quality"]["brier"]
    assert data["challenger_quality"]["log_loss"] < data["champion_quality"]["log_loss"]
    assert data["champion_quality"]["expected_calibration_error_10_bins"] > 0
    assert data["metrics_are_descriptive_not_statistical_certification"]
    assert data["temporal_quality_shift"]["status"] == "DESCRIPTIVE_ONLY_NO_DRIFT_CERTIFICATION"
    assert data["temporal_quality_shift"]["earlier_pairs"] == 15
    assert data["temporal_quality_shift"]["recent_pairs"] == 15
    assert result["model_promotion_authorized"] is False

def test_same_model_artifact_cannot_claim_challenger_improvement():
    obs = full_sample()
    for row in obs:
        if row["role"] == "CHALLENGER":
            row["model_artifact_id"] = "fitted-champion"
    verdict = audit_challenger_replay(obs)["comparisons"][0]
    assert "CHAMPION_CHALLENGER_NOT_DISTINCT" in verdict["blockers"]
    assert verdict["challenger_quality"] is None

def test_prediction_receipt_reused_across_events_blocks_cohort():
    obs = full_sample()
    obs[2]["prediction_receipt_ref"] = obs[0]["prediction_receipt_ref"]
    result = audit_challenger_replay(obs)
    assert result["status"] == "EVIDENCE_HOLD"
    assert any("PREDICTION_RECEIPT_REUSED" in x for x in result["findings"])
    assert result["comparisons"][0]["champion_quality"] is None

def test_simultaneous_kickoffs_do_not_claim_temporal_drift():
    obs = full_sample()
    for row in obs:
        row["predicted_at"] = "2026-09-30T20:00:00Z"
        row["event_start_at"] = "2026-10-01T00:00:00Z"
        row["settled_at"] = "2026-10-01T04:00:00Z"
    result = audit_challenger_replay(obs)["comparisons"][0]
    assert result["temporal_quality_shift"]["status"] == "TEMPORAL_COMPARISON_UNAVAILABLE"
    assert result["champion_quality"] is not None

def test_held_cohort_never_reports_quality_metrics():
    obs = full_sample()
    obs[0]["out_of_sample_verified"] = False
    result = audit_challenger_replay(obs)["comparisons"][0]
    assert result["champion_quality"] is None
    assert result["temporal_quality_shift"] is None


def test_reused_event_market_role_across_cohorts_blocks_both():
    original = full_sample()
    second = full_sample()
    for index, row in enumerate(second):
        row["cohort_id"] = "NOV_TEST"
        row["event_id"] = f"nov-game-{index // 2}"
        row["prediction_receipt_ref"] = f"other-receipt:{index}"
    second[0]["event_id"] = original[0]["event_id"]
    result = audit_challenger_replay(original + second)
    assert result["status"] == "EVIDENCE_HOLD"
    assert any("CROSS_COHORT_EVENT_REUSED" in x for x in result["findings"])
    assert all("INVALID_SOURCE_ROW" in item["blockers"] for item in result["comparisons"])


def test_unattributed_invalid_record_invalidates_otherwise_verified_cohorts():
    obs = full_sample()
    obs.append({"sport": ["NFL"], "market_family": "MONEYLINE", "cohort_id": "2026_OCT_TEST"})
    result = audit_challenger_replay(obs)
    assert result["status"] == "EVIDENCE_HOLD"
    assert "UNATTRIBUTED_ROW_BLOCKS_ALL_COHORTS" in result["findings"]
    assert "SOURCE_ROW_COHORT_UNATTRIBUTABLE" in result["comparisons"][0]["blockers"]
    assert result["comparisons"][0]["champion_quality"] is None

def test_unhashable_role_in_otherwise_complete_row_is_a_hold_not_a_crash():
    obs = full_sample()
    obs[1] = {**obs[1], "role": ["CHALLENGER"]}
    result = audit_challenger_replay(obs)
    assert result["status"] == "EVIDENCE_HOLD"
    assert result["comparisons"][0]["champion_quality"] is None


def test_baseline_champion_without_challenger_is_descriptive_only():
    from v17.sirt_model_progression_audit import audit_champion_baseline
    champion = [row for row in full_sample() if row["role"] == "CHAMPION"]
    verdict = audit_champion_baseline(champion)
    assert verdict["status"] == "RESEARCH_BASELINE_ONLY"
    assert verdict["baselines"][0]["quality"]["sample_count"] == 30
    assert verdict["model_promotion_authorized"] is False
    assert verdict["production_source_verified"] is False
    assert verdict["can_execute"] is False


def test_baseline_missing_attested_rows_fails_closed():
    from v17.sirt_model_progression_audit import audit_champion_baseline
    verdict = audit_champion_baseline([])
    assert verdict["status"] == "EVIDENCE_HOLD"
    assert verdict["baselines"] == []


def test_baseline_invalid_or_unattributed_row_blocks_valid_cohort():
    from v17.sirt_model_progression_audit import audit_champion_baseline
    champion = [row for row in full_sample() if row["role"] == "CHAMPION"]
    for invalid in ({"role": "CHALLENGER", **{k: v for k, v in champion[0].items() if k != "role"}}, None):
        verdict = audit_champion_baseline(champion + [invalid])
        assert verdict["status"] == "EVIDENCE_HOLD"
        assert verdict["baselines"][0]["quality"] is None


def test_baseline_duplicate_receipt_and_inconsistent_split_hold():
    from v17.sirt_model_progression_audit import audit_champion_baseline
    champion = [row for row in full_sample() if row["role"] == "CHAMPION"]
    copy = [dict(row) for row in champion]
    copy[1]["prediction_receipt_ref"] = copy[0]["prediction_receipt_ref"]
    assert audit_champion_baseline(copy)["status"] == "EVIDENCE_HOLD"
    copy = [dict(row) for row in champion]
    copy[1]["out_of_sample_split_ref"] = "OTHER_SPLIT"
    report = audit_champion_baseline(copy)
    assert "INCOMPATIBLE_HOLDOUT_SPLITS" in report["baselines"][0]["blockers"]


def test_baseline_minimum_rows_validate_properly():
    from v17.sirt_model_progression_audit import audit_champion_baseline
    champion = [row for row in full_sample(29) if row["role"] == "CHAMPION"]
    report = audit_champion_baseline(champion)
    assert "INSUFFICIENT_HISTORICAL_SAMPLE" in report["baselines"][0]["blockers"]
    with pytest.raises(ValueError):
        audit_champion_baseline(champion, minimum_observations=0)


def test_baseline_cross_cohort_reuse_blocks_both_artifacts():
    from v17.sirt_model_progression_audit import audit_champion_baseline
    rows = [dict(x) for x in full_sample() if x["role"] == "CHAMPION"]
    others = [dict(x) for x in rows]
    for row in others:
        row["cohort_id"] = "NEXT_COHORT"
        row["event_id"] = "later-" + row["event_id"]
        row["prediction_receipt_ref"] = "later-" + row["prediction_receipt_ref"]
    others[0]["prediction_receipt_ref"] = rows[0]["prediction_receipt_ref"]
    result = audit_champion_baseline(rows + others)
    assert result["status"] == "EVIDENCE_HOLD"
    assert len(result["baselines"]) == 2
    assert all(x["quality"] is None for x in result["baselines"])
