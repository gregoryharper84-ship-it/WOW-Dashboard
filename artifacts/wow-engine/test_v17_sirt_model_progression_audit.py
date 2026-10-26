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
