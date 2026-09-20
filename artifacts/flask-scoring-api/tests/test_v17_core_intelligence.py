from __future__ import annotations

import sys
from pathlib import Path

WOW_ENGINE_ROOT = Path(__file__).resolve().parents[2] / "wow-engine"
if str(WOW_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(WOW_ENGINE_ROOT))

from v17.core_intelligence import (
    build_learning_observation,
    detect_learning_hypotheses,
    evaluate_challenger,
    summarize_cohort,
)
from v17.postmortem_learning_ledger import build_postmortem_outcome


def _prediction(i: int, probability: float = 0.70):
    return {
        "prediction_id": f"00000000-0000-0000-0000-{i:012d}",
        "sport": "MLB",
        "market_type": "PLAYER_PROP",
        "stat_type": "PITCHER_STRIKEOUTS",
        "direction": "MORE",
        "model_family": "MLB_K_MODEL_V1",
        "model_artifact_version": "artifact-1",
        "calibration_version": "cal-1",
        "calibrated_probability": probability,
        "calibrated_probability_lower_bound": probability - 0.04,
    }


def _observation(i: int, result: str, probability: float = 0.70):
    prediction = _prediction(i, probability)
    outcome = build_postmortem_outcome(
        prediction,
        official_result=result,
        settlement_source="official_box_score",
        settlement_timestamp="2026-09-20T12:00:00-05:00",
    )
    return build_learning_observation(prediction, outcome)


def test_core_intelligence_never_executes_or_regrades():
    prediction = _prediction(1, 0.72)
    outcome = build_postmortem_outcome(
        prediction,
        official_result="LOSS",
        settlement_source="official_box_score",
        settlement_timestamp="2026-09-20T12:00:00-05:00",
    )
    row = build_learning_observation(prediction, outcome)
    assert row.official_result == "LOSS"
    assert row.outcome_target == 0
    assert row.probability == 0.72
    assert prediction["calibrated_probability"] == 0.72
    assert row.can_execute is False
    assert row.authority == "ADVISORY_ONLY"


def test_push_is_kept_but_excluded_from_probability_metrics():
    row = _observation(2, "PUSH")
    assert row.outcome_target is None
    assert row.brier_score is None
    assert row.log_loss is None


def test_small_sample_cannot_generate_learning_hypothesis():
    rows = tuple(_observation(i, "LOSS", 0.80) for i in range(10))
    summary = summarize_cohort(rows, min_samples=30)
    assert summary.ready_for_hypothesis is False
    assert detect_learning_hypotheses(summary) == ()


def test_large_overconfident_cohort_opens_review_hypothesis_only():
    rows = tuple(
        _observation(i, "WIN" if i < 12 else "LOSS", 0.80)
        for i in range(40)
    )
    summary = summarize_cohort(rows, min_samples=30)
    hypotheses = detect_learning_hypotheses(summary)
    assert summary.ready_for_hypothesis is True
    assert any(h.hypothesis_type == "CALIBRATION_BIAS" for h in hypotheses)
    assert all(h.automatic_promotion_allowed is False for h in hypotheses)
    assert all(h.can_execute is False for h in hypotheses)


def test_hypothesis_id_changes_when_append_only_evidence_snapshot_grows():
    first_rows = tuple(
        _observation(i, "WIN" if i < 10 else "LOSS", 0.80)
        for i in range(30)
    )
    second_rows = first_rows + tuple(
        _observation(i, "LOSS", 0.80)
        for i in range(30, 35)
    )
    first = detect_learning_hypotheses(summarize_cohort(first_rows, min_samples=30))
    second = detect_learning_hypotheses(summarize_cohort(second_rows, min_samples=30))
    first_cal = next(h for h in first if h.hypothesis_type == "CALIBRATION_BIAS")
    second_cal = next(h for h in second if h.hypothesis_type == "CALIBRATION_BIAS")
    assert first_cal.evidence_n == 30
    assert second_cal.evidence_n == 35
    assert first_cal.hypothesis_id != second_cal.hypothesis_id


def test_mixed_model_versions_cannot_be_silently_pooled():
    a = _observation(1, "WIN")
    prediction = _prediction(2)
    prediction["model_artifact_version"] = "artifact-2"
    outcome = build_postmortem_outcome(
        prediction,
        official_result="WIN",
        settlement_source="official_box_score",
        settlement_timestamp="2026-09-20T12:00:00-05:00",
    )
    b = build_learning_observation(prediction, outcome)
    try:
        summarize_cohort((a, b))
    except ValueError as exc:
        assert str(exc) == "MIXED_COHORT"
    else:
        raise AssertionError("mixed cohort must fail closed")


def test_challenger_can_only_become_review_eligible():
    evaluation = evaluate_challenger(
        challenger_id="challenger-1",
        cohort_key_value="PROP|MLB|UNKNOWN_LEAGUE|PLAYER_PROP|PITCHER_STRIKEOUTS|M|V|C",
        holdout_n=150,
        champion_brier=0.24,
        challenger_brier=0.20,
        champion_log_loss=0.61,
        challenger_log_loss=0.56,
    )
    assert evaluation.eligible_for_review is True
    assert evaluation.review_status == "ELIGIBLE_FOR_GOVERNED_REVIEW"
    assert evaluation.automatic_promotion_allowed is False
    assert evaluation.can_execute is False
