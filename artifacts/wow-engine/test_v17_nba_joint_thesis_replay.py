"""Synthetic-only regressions for research NBA joint prop replay; zero model authority."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parent / "v17" / "research" / "nba_joint_thesis_replay.py"
spec = importlib.util.spec_from_file_location("nba_joint_thesis_replay", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(*, event="NBA-test-1", player="nba-p-1", when="2026-10-29T00:00:00+00:00",
            left="WIN", right="WIN", joint=0.52, cohort="MINUTES_STABLE"):
    return {
        "sport": "NBA", "official_event_id": event, "official_player_id": player,
        "prediction_id": event + "/" + player,
        "joint_model_artifact_id": "SYNTHETIC-RESEARCH-ONLY",
        "joint_research_receipt_id": event + "/" + player + "/joint",
        "matchup_cohort": cohort,
        "matchup_cohort_asof_utc": "2026-10-28T11:00:00+00:00",
        "joint_fit_data_cutoff_utc": "2026-10-28T10:00:00+00:00",
        "official_settlement_source": "SYNTHETIC_FIXTURE_NOT_REAL_DATA",
        "official_settlement_timestamp_utc": "2026-10-30T01:00:00+00:00",
        "event_start_utc": when,
        "prediction_timestamp_utc": "2026-10-28T13:00:00+00:00",
        "joint_model_timestamp_utc": "2026-10-28T12:00:00+00:00",
        "research_joint_probability": joint,
        "legs": [
            {"receipt_id": event + "/" + player + "/points",
             "stat_type": "POINTS", "period": "FULL_GAME", "exact_line": "24.5",
             "direction": "MORE", "settlement_identity": "EXACT_PRIZEPICKS_POINTS",
             "calibration_status": "PASS", "calibrated_probability": 0.7,
             "model_timestamp_utc": "2026-10-28T11:00:00+00:00", "result": left},
            {"receipt_id": event + "/" + player + "/assists",
             "stat_type": "ASSISTS", "period": "FULL_GAME", "exact_line": "6.5",
             "direction": "LESS", "settlement_identity": "EXACT_PRIZEPICKS_ASSISTS",
             "calibration_status": "PASS", "calibrated_probability": 0.6,
             "model_timestamp_utc": "2026-10-28T11:05:00+00:00", "result": right},
        ],
    }


def run(rows, *, cutoff="2026-10-28T00:00:00+00:00", n=30):
    return module.evaluate_joint_replay(rows, holdout_start_utc=cutoff, min_cohort_n=n)


def test_valid_joint_probability_compared_with_naive_baseline_only_for_research():
    r = run([fixture()], n=1)
    assert r["row_reconciliation_pass"] is True
    assert r["holdout_settled_rows"] == 1
    assert r["metrics"]["brier_joint"] == pytest.approx((1 - .52) ** 2)
    assert r["metrics"]["brier_independent_baseline"] == pytest.approx((1 - .42) ** 2)
    assert r["metrics"]["brier_gain_positive_is_better"] > 0
    assert r["matchup_cohorts"]["MINUTES_STABLE"]["n"] == 1
    assert r["claim_of_outperformance"] is False
    assert r["probability_publishable"] is False
    assert r["rank_eligible"] is False
    assert r["can_execute"] is False


def test_two_hits_not_guaranteed_correlation():
    r = run([fixture(left="WIN", right="LOSS")], n=1)
    assert r["metrics"]["observed_joint_rate"] == 0
    assert r["metrics"]["brier_joint"] == pytest.approx(0.52**2)


def test_frechet_invalid_high_and_low_fail_closed():
    a = fixture(joint=0.9)
    b = fixture(event="NBA-test-2", player="p2", joint=0.2)
    r = run([a, b])
    assert r["holdout_settled_rows"] == 0
    assert len(r["rejected_rows"]) == 2
    assert all("Fréchet" in item["reason"] for item in r["rejected_rows"])
    assert r["row_reconciliation_pass"]


def test_inexact_or_missing_immutable_leg_identity_rejected():
    a = fixture()
    a["legs"][0].pop("exact_line")
    b = fixture(event="NBA-test-3", player="nba-p-3")
    b["legs"][0]["receipt_id"] = b["legs"][1]["receipt_id"]
    r = run([a, b])
    assert len(r["rejected_rows"]) == 2
    assert r["row_reconciliation_pass"] is True


def test_started_before_prediction_is_rejected():
    row = fixture()
    row["prediction_timestamp_utc"] = "2026-10-29T00:00:00+00:00"
    r = run([row])
    assert "pregame" not in r["rejected_rows"][0]["reason"] or "before start" in r["rejected_rows"][0]["reason"]
    assert r["holdout_settled_rows"] == 0


def test_leg_model_timestamp_not_pregame_is_rejected():
    row = fixture()
    row["legs"][0]["model_timestamp_utc"] = "2026-10-30T00:00:00+00:00"
    assert "not pregame" in run([row])["rejected_rows"][0]["reason"]


def test_leg_uncalibrated_and_joint_no_receipt_rejected():
    a = fixture()
    a["legs"][0]["calibration_status"] = "MISSING"
    b = fixture(player="other")
    b.pop("joint_research_receipt_id")
    r = run([a, b])
    assert len(r["rejected_rows"]) == 2
    assert r["row_reconciliation_pass"]


def test_void_dnp_push_and_unresolved_are_excluded_not_losses():
    rows = [
        fixture(event=f"g-{i}", player=f"p-{i}", left=state)
        for i, state in enumerate(("VOID", "DNP", "PUSH", "UNRESOLVED"))
    ]
    r = run(rows, n=1)
    assert r["holdout_rows"] == 4
    assert r["holdout_settled_rows"] == 0
    assert r["holdout_unsettled_or_void_rows"] == 4
    assert r["metrics"]["brier_joint"] is None


def test_duplicate_exact_thesis_does_not_multiply_grading():
    row = fixture()
    r = run([row, deepcopy(row)])
    assert r["holdout_settled_rows"] == 1
    assert len(r["duplicate_rows"]) == 1
    assert r["row_reconciliation_pass"]


def test_different_player_same_event_is_not_identity_duplicate():
    r = run([fixture(), fixture(player="nba-p-2")], n=2)
    assert r["holdout_settled_rows"] == 2
    assert not r["duplicate_rows"]


def test_prior_rows_never_graded_on_holdout_even_if_settled():
    prior = fixture(when="2026-10-27T23:00:00+00:00")
    prior["prediction_timestamp_utc"] = "2026-10-27T13:00:00+00:00"
    prior["joint_model_timestamp_utc"] = "2026-10-27T12:00:00+00:00"
    prior["joint_fit_data_cutoff_utc"] = "2026-10-27T10:00:00+00:00"
    prior["matchup_cohort_asof_utc"] = "2026-10-27T11:00:00+00:00"
    for leg in prior["legs"]:
        leg["model_timestamp_utc"] = "2026-10-27T11:00:00+00:00"
    r = run([prior, fixture(player="new-player")])
    assert r["prior_rows_not_graded"] == 1
    assert r["holdout_settled_rows"] == 1
    assert r["row_reconciliation_pass"]


def test_missing_joint_candidate_probability_never_replaced_by_product():
    row = fixture()
    row.pop("research_joint_probability")
    r = run([row])
    assert r["metrics"]["n"] == 0
    assert len(r["rejected_rows"]) == 1


def test_literal_at_least_threshold_is_preserved_and_not_converted():
    row = fixture()
    row["legs"][0]["exact_line"] = "7+"
    r = run([row], n=1)
    assert r["holdout_settled_rows"] == 1
    assert r["can_execute"] is False


def test_non_nba_or_direction_invalid_rejected():
    a = fixture()
    a["sport"] = "WNBA"
    b = fixture(player="pB")
    b["legs"][0]["direction"] = "OVER"
    r = run([a, b])
    assert len(r["rejected_rows"]) == 2


def test_nonfinite_probability_and_false_boolean_rejected():
    a = fixture(joint=float("nan"))
    b = fixture(player="pb")
    b["legs"][1]["calibrated_probability"] = True
    r = run([a, b])
    assert len(r["rejected_rows"]) == 2
    assert r["row_reconciliation_pass"]


def test_sample_size_gate_never_certifies_even_with_many_rows():
    rows = [fixture(event=f"nba-{i:03d}", player=f"nba-p-{i:03d}",
                    left="WIN" if i % 2 else "LOSS") for i in range(32)]
    r = run(rows, n=30)
    assert r["sample_sufficient_for_descriptive_review"]
    assert r["matchup_cohorts"]["MINUTES_STABLE"]["sample_sufficient_for_descriptive_review"]
    assert r["claim_of_outperformance"] is False
    assert r["candidate_certified"] is False
    assert r["model_promoted"] is False


def test_unanchored_timezone_or_bad_cutoff_rejected():
    row = fixture()
    row["prediction_timestamp_utc"] = "2026-10-28T13:00:00"
    assert "timezone" in run([row])["rejected_rows"][0]["reason"]
    with pytest.raises(ValueError):
        run([fixture()], cutoff="2026-10-28")
    with pytest.raises(ValueError):
        run([fixture()], n=0)


def test_post_forecast_matchup_cohort_and_fit_leakage_rejected():
    a = fixture()
    a["matchup_cohort_asof_utc"] = "2026-10-28T14:00:00+00:00"
    b = fixture(player="pb")
    b["joint_fit_data_cutoff_utc"] = "2026-10-28T12:00:00+00:00"
    r = run([a, b])
    assert len(r["rejected_rows"]) == 2
    assert "post-forecast" in r["rejected_rows"][0]["reason"]
    assert "strictly prior" in r["rejected_rows"][1]["reason"]


def test_unsourced_or_pregame_settlement_rejected():
    a = fixture()
    a.pop("official_settlement_source")
    b = fixture(player="pb")
    b["official_settlement_timestamp_utc"] = "2026-10-28T20:00:00+00:00"
    r = run([a, b])
    assert len(r["rejected_rows"]) == 2
    assert r["holdout_settled_rows"] == 0
