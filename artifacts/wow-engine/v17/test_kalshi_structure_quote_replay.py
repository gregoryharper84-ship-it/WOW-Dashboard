"""Synthetic negative and positive evidence-gate tests; not real backtest evidence."""
from __future__ import annotations
import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).with_name("research") / "kalshi_structure_quote_replay.py"
spec = importlib.util.spec_from_file_location("kalshi_structure_quote_replay", PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def record(**updates):
    base = {
        "slip_id": "s1",
        "structure_type": "SINGLE_PLAYER_SAME_GAME_MULTI_MARKET",
        "official_result": "YES",
        "calibration_status": "PASS",
        "source_status": "OFFICIALLY_RECONCILED",
        "component_market_ids": ["m1", "m2"],
        "component_stat_families": ["POINTS", "ASSISTS"],
        "model_fit_cutoff_utc": "2026-11-10T00:00:00Z",
        "prediction_at_utc": "2026-11-14T00:00:00Z",
        "quote_at_utc": "2026-11-14T00:10:00Z",
        "event_start_utc": "2026-11-15T22:00:00Z",
        "settlement_at_utc": "2026-11-16T01:00:00Z",
        "pregame_joint_probability": 0.6,
        "pregame_joint_lower_bound": 0.52,
        "executable_quote_cents": 45,
        "fee_cents_per_contract": 2,
        "settlement_cents_per_contract": 100,
        "immutable_prediction_receipt_id": "source-receipt-1",
        "fitted_model_artifact_id": "fitted-model-1",
        "official_settlement_evidence_ref": "official-source-1",
        "quote_evidence_ref": "quote-source-1",
        "thesis_cluster_id": "cluster-1",
        "comparison_group_id": "same-pregame-context-1",
    }
    base.update(updates)
    return base


def audit(rows):
    return m.replay(rows, holdout_start_utc="2026-11-15T00:00:00Z")


def test_valid_synthetic_replay_has_only_descriptive_output():
    out = audit([record()])
    style = out["by_style"]["SINGLE_PLAYER_SAME_GAME_MULTI_MARKET"]
    assert out["rows_eligible"] == 1
    assert style["binary_graded"] == 1
    assert style["win_rate"] == 1
    assert style["brier"] == pytest.approx(.16)
    assert style["net_cents_per_contract_sum"] == 53
    assert out["comparative_lift_established"] is False
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False


@pytest.mark.parametrize("field,value,reason", [
    ("component_stat_families", ["UNKNOWN", "POINTS"], "MISSING_EXACT_STAT_FAMILIES"),
    ("component_market_ids", ["m1", "m1"], "MISSING_OR_DUPLICATE_MARKET_IDS"),
    ("calibration_status", "CANDIDATE_ONLY", "CALIBRATION_NOT_VERIFIED"),
    ("source_status", "UNVERIFIED_RESEARCH", "SOURCE_NOT_RECONCILED"),
    ("prediction_at_utc", "2026-11-16T00:00:00Z", "CHRONOLOGY_INVALID"),
    ("quote_at_utc", "2026-11-15T23:00:00Z", "CHRONOLOGY_INVALID"),
    ("event_start_utc", "2026-11-14T22:00:00Z", "OUTSIDE_FORWARD_HOLDOUT"),
    ("pregame_joint_probability", None, "INVALID_PREGAME_JOINT_PROBABILITY"),
    ("pregame_joint_lower_bound", None, "MISSING_JOINT_LOWER_BOUND"),
    ("immutable_prediction_receipt_id", None, "MISSING_IMMUTABLE_PREDICTION_RECEIPT_ID"),
    ("quote_evidence_ref", "", "MISSING_QUOTE_EVIDENCE_REF"),
    ("settlement_cents_per_contract", 0, "RESULT_PAYOUT_CONFLICT"),
])
def test_reject_missing_causality_and_market_evidence(field, value, reason):
    out = audit([record(**{field: value})])
    assert out["rows_eligible"] == 0
    assert out["reject_reasons"][reason] == 1


def test_scalar_settlement_keeps_economics_but_not_binary_calibration():
    out = audit([record(official_result="SCALAR", settlement_cents_per_contract=65)])
    g = out["by_style"]["SINGLE_PLAYER_SAME_GAME_MULTI_MARKET"]
    assert g["scalar_settled"] == 1
    assert g["binary_graded"] == 0
    assert g["brier"] is None
    assert g["net_cents_per_contract_sum"] == 18


def test_comparison_with_same_group_remains_not_certified():
    r2 = record(slip_id="s2", structure_type="MULTI_PLAYER_SAME_GAME",
                component_market_ids=["m3", "m4"], thesis_cluster_id="cluster-2",
                official_result="NO", settlement_cents_per_contract=0)
    out = audit([record(), r2])
    assert out["comparison_groups_with_multiple_styles"] == 1
    assert out["comparative_lift_established"] is False
    assert out["by_style"]["MULTI_PLAYER_SAME_GAME"]["net_cents_per_contract_sum"] == -47


def test_duplicate_and_unknown_timestamp_rejected():
    out = audit([record(), record(), record(
        slip_id="s3", prediction_at_utc="2026-11-15T17:00:00")])
    assert out["rows_eligible"] == 1
    assert out["reject_reasons"]["DUPLICATE_SLIP_ID"] == 1
    assert out["reject_reasons"]["NAIVE_TIMESTAMP"] == 1
