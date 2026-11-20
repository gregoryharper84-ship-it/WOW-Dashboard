"""Adversarial, research-only regression for the screenshot-reported NBA combo."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from v17.kalshi_combo_win_case_study import (
    audit_user_reported_case,
    diagnostic_condition_from_final_scores,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "v17" / "fixtures" / "kalshi_nba_five_market_2026_11_11.json"


def case():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_five_market_ticket_arithmetic_without_false_certification():
    result = audit_user_reported_case(case())
    assert result["reported_winning_legs"] == 5
    assert result["cost_usd"] == "99.96"
    assert result["payout_usd"] == "519.48"
    assert result["net_profit_usd"] == "419.52"
    assert result["payout_multiple"] == "5.196879"
    assert result["profit_percent"] == "419.69"
    assert result["combo_break_even_percent"] == "19.24"
    assert result["combo_true_probability"] is None
    assert result["fitted_model_probability"] is None
    assert result["net_expected_value"] is None
    assert result["model_calibration_eligible"] is False
    assert result["independent_settlement_verified"] is False
    assert result["promotion_allowed"] is False
    assert result["can_execute"] is False
    assert result["dry_run_only"] is True
    assert len(result["missing_evidence"]) == 6


def test_reported_leg_classification_four_yes_winner_one_no_spread():
    legs = case()["legs"]
    assert [(x["market_type"], x["side"]) for x in legs] == [
        ("MONEYLINE", "YES"),
        ("MONEYLINE", "YES"),
        ("MONEYLINE", "YES"),
        ("MONEYLINE", "YES"),
        ("WIN_MARGIN_GREATER_THAN_NO", "NO"),
    ]


@pytest.mark.parametrize("scores,expected", [
    ({"DEN": 123, "MEM": 118}, True),  # actual screenshot's NO condition
    ({"DEN": 100, "MEM": 107}, True),  # Memphis wins by seven: NO still wins
    ({"DEN": 100, "MEM": 108}, False), # Memphis wins by eight: NO loses
    ({"DEN": 123, "MEM": 100}, True),  # Denver win also satisfies NO
])
def test_no_memphis_margin_contract_is_not_memphis_to_lose(scores, expected):
    assert diagnostic_condition_from_final_scores(case()["legs"][-1], scores) is expected


@pytest.mark.parametrize("idx,scores", [
    (0, {"BOS": 98, "TOR": 93}),
    (1, {"LAL": 123, "MIL": 112}),
    (2, {"ORL": 123, "UTA": 108}),
    (3, {"SAS": 124, "CHI": 119}),
])
def test_reported_moneyline_winner_semantics(idx, scores):
    leg = case()["legs"][idx]
    assert diagnostic_condition_from_final_scores(leg, scores) is True
    reverse = dict(zip(leg["teams"], reversed(list(scores.values()))))
    assert diagnostic_condition_from_final_scores(leg, reverse) is False


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-1.00", "0.001", None, True])
def test_invalid_money_never_passes(invalid):
    fixture = case()
    fixture["economics"]["cost_usd"] = invalid
    with pytest.raises(ValueError):
        audit_user_reported_case(fixture)


def test_inconsistent_profit_is_not_silently_reconciled():
    fixture = case()
    fixture["economics"]["profit_usd"] = "419.51"
    with pytest.raises(ValueError, match="payout minus cost"):
        audit_user_reported_case(fixture)


def test_fake_source_and_missing_market_fail_closed():
    fixture = case()
    fixture["evidence_type"] = "OFFICIAL_MODEL_VERIFIED"
    with pytest.raises(ValueError, match="provenance"):
        audit_user_reported_case(fixture)
    fixture = case()
    fixture["legs"][-1]["side"] = "YES"
    with pytest.raises(ValueError, match="margin NO"):
        audit_user_reported_case(fixture)


def test_missing_or_incorrect_score_identity_rejected():
    leg = case()["legs"][-1]
    for scores in ({"DEN": 123}, {"DEN": 123, "UTA": 118},
                   {"DEN": -1, "MEM": 0}, {"DEN": 111, "MEM": 111}):
        with pytest.raises(ValueError):
            diagnostic_condition_from_final_scores(leg, scores)


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_existing_live_combo_recovery_guards_remain_in_force():
    # Regression against actual existing production admission modules, not
    # assertions that this postmortem helper itself is a safety gate.
    artifacts = ROOT.parent
    combo = _load_module(artifacts / "flask-scoring-api" / "gate_engine" / "combo_gate.py", "existing_combo_gate")
    gov = _load_module(artifacts / "flask-scoring-api" / "kalshi_engine" / "portfolio_governor.py", "existing_portfolio_governor")
    verdict = combo.validate_combo_size(case()["legs"])
    assert verdict["allowed"] is False
    assert verdict["reject_code"] == "HARD_REJECT_COMBO_MULTIPLICATION"
    assert verdict["can_execute"] is False
    assert gov.check_single({"is_multi_leg": True}, []) == {
        "passed": False, "rejection_reason": "RECOVERY_CAP_SINGLES_ONLY"
    }
