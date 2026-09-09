from datetime import datetime, timedelta, timezone

import pytest

from nfl_dfs_calibration_evidence import (
    NflDfsCalibrationEvidenceError,
    PromotionPolicy,
    build_calibration_evidence,
    ingest_settled_rows,
)


MODEL_HASH = "a" * 64
SCORING_HASH = "b" * 64
BASE = datetime(2025, 9, 1, 18, 0, tzinfo=timezone.utc)


def _row(
    index: int,
    *,
    side: str = "MORE",
    raw_probability: float | None = None,
    realized_score: float | None = None,
    event_start: datetime | None = None,
):
    start = event_start or (BASE + timedelta(days=index))
    p = raw_probability if raw_probability is not None else 0.08 + 0.84 * ((index % 97) / 96)
    pseudo_uniform = ((index * 37) % 100) / 100.0
    true_p = 0.20 + 0.60 * p
    won = pseudo_uniform < true_p
    score = realized_score if realized_score is not None else (22.0 if won else 11.0)
    if side == "LESS":
        score = realized_score if realized_score is not None else (11.0 if won else 22.0)
    p_more = p if side == "MORE" else 1.0 - p
    p_less = 1.0 - p_more
    return {
        "event_id": f"evt-{index}",
        "player_id": f"player-{index % 16}",
        "position": ("QB", "RB", "WR", "TE")[index % 4],
        "exact_line": 15.5,
        "side": side,
        "raw_candidate_probability": p,
        "realized_fantasy_score": score,
        "prediction_timestamp": (start - timedelta(hours=6)).isoformat(),
        "event_start_timestamp": start.isoformat(),
        "model_version": "NFL_DFS_EMPIRICAL_RESIDUAL_CANDIDATE_V1",
        "model_source_sha256": MODEL_HASH,
        "scoring_profile_id": "PRIZEPICKS_NFL_FANTASY_SCORE_V1",
        "scoring_profile_sha256": SCORING_HASH,
        "simulation_count": 50_000,
        "seed": 17 + index,
        "p_more": p_more,
        "p_less": p_less,
        "p_push": 0.0,
    }


def _history(n=260):
    return [_row(i) for i in range(n)]


def test_ingestion_rejects_post_start_prediction():
    row = _row(1)
    row["prediction_timestamp"] = row["event_start_timestamp"]
    with pytest.raises(NflDfsCalibrationEvidenceError, match="strictly before"):
        ingest_settled_rows([row])


def test_ingestion_rejects_side_probability_identity_mismatch():
    row = _row(1)
    row["raw_candidate_probability"] = 0.25
    with pytest.raises(NflDfsCalibrationEvidenceError, match="exact requested side"):
        ingest_settled_rows([row])


def test_ingestion_rejects_duplicate_immutable_prediction():
    row = _row(1)
    with pytest.raises(NflDfsCalibrationEvidenceError, match="duplicate immutable"):
        ingest_settled_rows([row, dict(row)])


def test_ingestion_rejects_mixed_model_or_scoring_identity():
    rows = [_row(1), _row(2)]
    rows[1]["model_source_sha256"] = "c" * 64
    with pytest.raises(NflDfsCalibrationEvidenceError, match="cannot mix"):
        ingest_settled_rows(rows)


def test_phase_b_sample_shortage_is_typed_and_never_publishable():
    packet = build_calibration_evidence(_history(199))
    assert packet.terminal_status == "MODEL_INPUTS_INSUFFICIENT"
    assert "PHASE_B_REQUIRES_200_SETTLED_BINARY_ROWS" in packet.blockers
    assert packet.certification_status == "CANDIDATE_ONLY"
    assert packet.certification_review_eligible is False
    assert packet.probability_publishable is False
    assert packet.rank_eligible is False
    assert packet.can_execute is False


def test_holdout_and_calibration_are_strictly_chronological_and_candidate_only():
    packet = build_calibration_evidence(_history())
    assert packet.calibration_rows >= 200
    assert packet.holdout_rows > 0
    assert packet.fold_count == 6
    assert datetime.fromisoformat(packet.calibration_end) < datetime.fromisoformat(packet.holdout_start)
    assert packet.calibration_method in {"PLATT_TIME_SPLIT_V1", "ISOTONIC_V1"}
    assert packet.raw_holdout_metrics is not None
    assert packet.calibrated_holdout_metrics is not None
    assert packet.oof_calibration_metrics is not None
    assert packet.terminal_status == "CALIBRATION_EVALUATED_CANDIDATE_ONLY"
    assert packet.blockers == ("PROMOTION_POLICY_NOT_CONFIGURED",)
    assert packet.probability_publishable is False
    assert packet.rank_eligible is False
    assert packet.can_execute is False


def test_equal_kickoff_events_never_straddle_chronological_boundary():
    rows = _history()
    shared_start = BASE + timedelta(days=207)
    rows[207] = _row(207, event_start=shared_start)
    rows[208] = _row(208, event_start=shared_start)
    packet = build_calibration_evidence(rows)
    assert datetime.fromisoformat(packet.calibration_end) < datetime.fromisoformat(packet.holdout_start)


def test_push_is_preserved_in_audit_but_excluded_from_binary_fit():
    rows = _history()
    push = _row(300, realized_score=15.5)
    rows.append(push)
    packet = build_calibration_evidence(rows)
    assert packet.total_rows == 261
    assert packet.binary_rows == 260
    assert packet.pushes_excluded == 1


def test_external_policy_can_make_evidence_review_eligible_but_never_certified_or_publishable():
    policy = PromotionPolicy(
        policy_id="TEST_ONLY_PERMISSIVE_POLICY",
        minimum_total_rows=200,
        minimum_holdout_rows=1,
        maximum_brier=1.0,
        maximum_log_loss=10.0,
        maximum_ece=1.0,
        require_brier_improvement_over_raw=False,
    )
    packet = build_calibration_evidence(_history(), promotion_policy=policy)
    assert packet.certification_review_eligible is True
    assert packet.terminal_status == "CERTIFICATION_REVIEW_ELIGIBLE"
    assert packet.certification_status == "CANDIDATE_ONLY"
    assert packet.probability_publishable is False
    assert packet.rank_eligible is False
    assert packet.can_execute is False


def test_simulation_minimum_is_enforced_at_ingestion():
    row = _row(1)
    row["simulation_count"] = 49_999
    with pytest.raises(NflDfsCalibrationEvidenceError, match="50000"):
        ingest_settled_rows([row])
