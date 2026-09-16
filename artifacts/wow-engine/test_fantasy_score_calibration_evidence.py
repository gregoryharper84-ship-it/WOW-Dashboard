from datetime import datetime, timedelta, timezone

import pytest

from fantasy_score_calibration_evidence import (
    IMMUTABLE_PREGAME_SETTLED,
    LANE_CONTRACTS,
    MLB_HITTER,
    MLB_PITCHER,
    NBA,
    NFL,
    SYNTHETIC_TEST_ONLY,
    WNBA,
    FantasyScoreCalibrationEvidenceError,
    PromotionPolicy,
    assess_promotion_readiness,
    build_fantasy_score_calibration_evidence,
    lane_contract,
)


MODEL_HASH = "a" * 64
SCORING_HASH = "b" * 64
BASE = datetime(2025, 9, 1, 18, 0, tzinfo=timezone.utc)


def _row(index: int, *, lane: str = NBA, source: str = IMMUTABLE_PREGAME_SETTLED):
    contract = lane_contract(lane)
    start = BASE + timedelta(days=index)
    probability = 0.08 + 0.84 * ((index % 97) / 96)
    pseudo_uniform = ((index * 37) % 100) / 100.0
    won = pseudo_uniform < (0.20 + 0.60 * probability)
    p_more = probability
    return {
        "lane": lane,
        "market_family": contract.market_family,
        "controlling_specialist": contract.controlling_specialist,
        "evidence_source_kind": source,
        "event_id": f"evt-{index}",
        "player_id": f"player-{index % 16}",
        "position": ("QB", "RB", "WR", "TE")[index % 4] if lane == NFL else "G",
        "exact_line": 15.5,
        "side": "MORE",
        "raw_candidate_probability": probability,
        "realized_fantasy_score": 22.0 if won else 11.0,
        "prediction_timestamp": (start - timedelta(hours=6)).isoformat(),
        "event_start_timestamp": start.isoformat(),
        "model_version": f"{lane}_FANTASY_SCORE_EMPIRICAL_RESIDUAL_CANDIDATE_V1",
        "model_source_sha256": MODEL_HASH,
        "scoring_profile_id": f"PRIZEPICKS_{lane}_FANTASY_SCORE_V1",
        "scoring_profile_sha256": SCORING_HASH,
        "simulation_count": 50_000,
        "seed": 17 + index,
        "p_more": p_more,
        "p_less": 1.0 - p_more,
        "p_push": 0.0,
    }


def _history(*, lane: str = NBA, source: str = IMMUTABLE_PREGAME_SETTLED, n: int = 260):
    return [_row(i, lane=lane, source=source) for i in range(n)]


def _permissive_policy():
    return PromotionPolicy(
        policy_id="TEST_ONLY_PERMISSIVE_POLICY",
        minimum_total_rows=200,
        minimum_holdout_rows=1,
        maximum_brier=1.0,
        maximum_log_loss=10.0,
        maximum_ece=1.0,
        require_brier_improvement_over_raw=False,
    )


def test_all_current_fantasy_score_lanes_have_exact_specialist_contracts():
    assert set(LANE_CONTRACTS) == {NFL, NBA, WNBA, MLB_HITTER, MLB_PITCHER}
    assert lane_contract(NFL).controlling_specialist == "wow.nfl-dfs-fantasy-score-expert"
    assert lane_contract(NBA).controlling_specialist == "wow.nba-dfs-fantasy-score-expert"
    assert lane_contract(WNBA).controlling_specialist == "wow.wnba-dfs-fantasy-score-expert"
    assert lane_contract(MLB_HITTER).stat_type == "HITTER_FANTASY_SCORE"
    assert lane_contract(MLB_PITCHER).stat_type == "PITCHER_FANTASY_SCORE"


def test_generic_mlb_fantasy_score_lane_is_not_inferred():
    with pytest.raises(FantasyScoreCalibrationEvidenceError, match="unsupported"):
        lane_contract("MLB")


def test_cross_sport_rows_cannot_be_mixed_into_one_calibration_cohort():
    rows = _history(n=200)
    other = _row(201, lane=WNBA)
    rows.append(other)
    with pytest.raises(FantasyScoreCalibrationEvidenceError, match="cannot mix lane identity"):
        build_fantasy_score_calibration_evidence(
            rows,
            lane=NBA,
            evidence_source_kind=IMMUTABLE_PREGAME_SETTLED,
        )


def test_specialist_identity_mismatch_fails_closed_before_calibration():
    rows = _history(n=200)
    rows[0]["controlling_specialist"] = "wow.wrong-specialist"
    with pytest.raises(FantasyScoreCalibrationEvidenceError, match="controlling_specialist mismatch"):
        build_fantasy_score_calibration_evidence(
            rows,
            lane=NBA,
            evidence_source_kind=IMMUTABLE_PREGAME_SETTLED,
        )


def test_model_and_scoring_identity_still_cannot_mix():
    rows = _history(n=200)
    rows[1]["scoring_profile_sha256"] = "c" * 64
    with pytest.raises(FantasyScoreCalibrationEvidenceError, match="cannot mix"):
        build_fantasy_score_calibration_evidence(
            rows,
            lane=NBA,
            evidence_source_kind=IMMUTABLE_PREGAME_SETTLED,
        )


def test_real_immutable_evidence_can_reach_review_eligible_but_never_publishable():
    packet = build_fantasy_score_calibration_evidence(
        _history(),
        lane=NBA,
        evidence_source_kind=IMMUTABLE_PREGAME_SETTLED,
        promotion_policy=_permissive_policy(),
    )
    assert packet.lane == NBA
    assert packet.market_family == "NBA_FANTASY_SCORE"
    assert packet.certification_review_eligible is True
    assert packet.terminal_status == "CERTIFICATION_REVIEW_ELIGIBLE"
    assert packet.certification_status == "CANDIDATE_ONLY"
    assert packet.calibration_candidate_sha256
    assert len(packet.calibration_candidate_sha256) == 64
    assert packet.probability_publishable is False
    assert packet.rank_eligible is False
    assert packet.can_execute is False


def test_synthetic_evidence_never_becomes_certification_review_eligible():
    packet = build_fantasy_score_calibration_evidence(
        _history(source=SYNTHETIC_TEST_ONLY),
        lane=NBA,
        evidence_source_kind=SYNTHETIC_TEST_ONLY,
        promotion_policy=_permissive_policy(),
    )
    assert packet.certification_review_eligible is False
    assert "SYNTHETIC_EVIDENCE_NOT_CERTIFIABLE" in packet.blockers
    assert packet.terminal_status == "CALIBRATION_EVALUATED_SYNTHETIC_TEST_ONLY"
    assert packet.probability_publishable is False
    assert packet.rank_eligible is False
    assert packet.can_execute is False


def test_missing_external_policy_keeps_real_evidence_candidate_only():
    packet = build_fantasy_score_calibration_evidence(
        _history(),
        lane=NBA,
        evidence_source_kind=IMMUTABLE_PREGAME_SETTLED,
    )
    assert packet.certification_review_eligible is False
    assert "PROMOTION_POLICY_NOT_CONFIGURED" in packet.blockers
    assert packet.terminal_status == "CALIBRATION_EVALUATED_CANDIDATE_ONLY"


def test_phase_b_minimum_remains_200_binary_rows_for_every_lane_adapter():
    packet = build_fantasy_score_calibration_evidence(
        _history(lane=MLB_HITTER, n=199),
        lane=MLB_HITTER,
        evidence_source_kind=IMMUTABLE_PREGAME_SETTLED,
    )
    assert packet.terminal_status == "MODEL_INPUTS_INSUFFICIENT"
    assert "PHASE_B_REQUIRES_200_SETTLED_BINARY_ROWS" in packet.blockers
    assert packet.probability_publishable is False


def test_promotion_readiness_reports_every_missing_governed_prerequisite():
    packet = build_fantasy_score_calibration_evidence(
        _history(),
        lane=NBA,
        evidence_source_kind=IMMUTABLE_PREGAME_SETTLED,
        promotion_policy=_permissive_policy(),
    )
    readiness = assess_promotion_readiness(packet)
    assert readiness.promotion_review_ready is False
    assert "INDEPENDENT_CERTIFICATION_APPROVAL_REQUIRED" in readiness.blockers
    assert "FITTED_ARTIFACT_ID_REQUIRED" in readiness.blockers
    assert "CALIBRATION_ARTIFACT_ID_REQUIRED" in readiness.blockers
    assert "RUNTIME_ADAPTER_REGISTRATION_REQUIRED" in readiness.blockers
    assert "RUNTIME_HYDRATION_VERIFICATION_REQUIRED" in readiness.blockers
    assert "CANONICAL_ACTION_VERIFICATION_REQUIRED" in readiness.blockers
    assert readiness.governed_promotion_authorized is False
    assert readiness.probability_publishable is False
    assert readiness.can_execute is False


def test_even_complete_prerequisites_only_reach_promotion_review_ready():
    packet = build_fantasy_score_calibration_evidence(
        _history(),
        lane=NBA,
        evidence_source_kind=IMMUTABLE_PREGAME_SETTLED,
        promotion_policy=_permissive_policy(),
    )
    readiness = assess_promotion_readiness(
        packet,
        independent_certification_approved=True,
        certification_approval_id="cert-review-001",
        fitted_artifact_id="fit-nba-fs-001",
        calibration_artifact_id="cal-nba-fs-001",
        runtime_adapter_registered=True,
        hydration_verified=True,
        canonical_action_verified=True,
    )
    assert readiness.promotion_review_ready is True
    assert readiness.terminal_status == "PROMOTION_REVIEW_READY"
    assert readiness.blockers == ()
    assert readiness.governed_promotion_authorized is False
    assert readiness.probability_publishable is False
    assert readiness.rank_eligible is False
    assert readiness.can_execute is False
