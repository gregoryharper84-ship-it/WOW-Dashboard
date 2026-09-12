from copy import deepcopy
from pathlib import Path
import sys

WOW_ENGINE = Path(__file__).resolve().parents[2] / "wow-engine"
if str(WOW_ENGINE) not in sys.path:
    sys.path.insert(0, str(WOW_ENGINE))

from v17.llp_postmortem_recalibration import (  # noqa: E402
    LearningThresholds,
    build_llp_learning_report,
    calibration_report,
    failure_path_diagnostics,
    final_refresh_diagnostics,
    market_prior_diagnostics,
    ranking_regret_diagnostics,
)


def _row(
    prediction_id,
    *,
    p,
    result,
    role="FAVORITE",
    lower=None,
    market_weight=0.10,
    immutable=True,
    snapshot_stage="FINAL_IMMUTABLE_PREGAME",
    failure_tags=None,
    realized_failure_tags=None,
):
    return {
        "prediction_id": prediction_id,
        "date": "2026-09-07",
        "sport": "MLB",
        "league": "MLB",
        "event_id": prediction_id,
        "market_role": role,
        "selection": prediction_id,
        "independent_probability": p,
        "market_prior_weight": market_weight,
        "unconditional_probability": p,
        "calibrated_probability": p,
        "calibrated_lower_bound": p - 0.03 if lower is None else lower,
        "calibrated_upper_bound": p + 0.03,
        "immutable_model_timestamp": "2026-09-07T18:00:00Z",
        "model_version": "llp-postmortem-test-v17",
        "calibration_method": "TEST_CALIBRATION",
        "calibration_version": "v17-test-20260909",
        "source_snapshot_id": f"snapshot-{prediction_id}",
        "source_snapshot_timestamp": "2026-09-07T17:59:00Z",
        "outcome_space": "BINARY_WIN_LOSS",
        "failure_tags": failure_tags or [],
        "realized_failure_tags": realized_failure_tags or [],
        "snapshot_stage": snapshot_stage,
        "immutable_pregame": immutable,
        "final_refresh_status": "PASS",
        "result": result,
    }


def test_llp_learning_preserves_favorite_upset_lane_separation():
    rows = [
        *[_row(f"f{i}", p=0.70, result="WIN" if i < 7 else "LOSS") for i in range(10)],
        *[_row(f"u{i}", p=0.40, result="WIN" if i < 4 else "LOSS", role="UPSET") for i in range(10)],
    ]
    groups = calibration_report(rows, config=LearningThresholds(min_sample_size=5))["groups"]
    assert {(g["market_role"], g["n"]) for g in groups} == {("FAVORITE", 10), ("UPSET", 10)}


def test_llp_recalibration_requires_minimum_sample_and_only_recommends_review():
    too_small = [_row("x", p=0.90, result="LOSS")]
    cfg = LearningThresholds(min_sample_size=20, max_abs_calibration_bias=0.05)
    assert calibration_report(too_small, config=cfg)["groups"][0]["review_status"] == "INSUFFICIENT_SAMPLE"

    biased = [_row(f"b{i}", p=0.75, result="WIN" if i < 10 else "LOSS") for i in range(20)]
    group = calibration_report(biased, config=cfg)["groups"][0]
    assert group["review_status"] == "RECALIBRATION_REVIEW_RECOMMENDED"
    assert group["automatic_parameter_mutation"] is False


def test_llp_market_prior_and_failure_path_learning_do_not_rewrite_probability():
    row = _row(
        "heavy",
        p=0.68,
        result="LOSS",
        market_weight=0.60,
        failure_tags=["BULLPEN_COLLAPSE"],
        realized_failure_tags=["BULLPEN_COLLAPSE"],
    )
    before = deepcopy(row)
    market = market_prior_diagnostics([row])
    failure = failure_path_diagnostics([row])
    assert market["market_dependent_prediction_ids"] == ["heavy"]
    assert failure["realized_path_match_rate"] == 1.0
    assert row == before


def test_llp_lower_bound_ranking_regret_never_rewrites_historical_rank():
    rows = [
        _row("top", p=0.72, lower=0.68, result="LOSS"),
        _row("next", p=0.67, lower=0.63, result="WIN"),
    ]
    report = ranking_regret_diagnostics(rows)
    assert len(report["rank_inversions"]) == 1
    assert report["historical_rank_rewritten"] is False


def test_llp_refresh_and_complete_report_are_fail_closed_and_non_executing():
    pre = _row("refresh", p=0.55, result="WIN", immutable=False, snapshot_stage="PRE_REFRESH")
    final = _row("refresh", p=0.70, result="WIN")
    refresh = final_refresh_diagnostics([pre, final])
    assert refresh["improved"] == 1
    rows = [pre, final]
    before = deepcopy(rows)
    report = build_llp_learning_report(rows, config=LearningThresholds(min_sample_size=1))
    assert rows == before
    assert report["probability_or_prediction_rows_mutated"] is False
    assert report["automatic_parameter_mutation"] is False
    assert report["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert report["can_execute"] is False
