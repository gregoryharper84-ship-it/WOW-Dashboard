from __future__ import annotations

import sys
from pathlib import Path

WOW_ENGINE_ROOT = Path(__file__).resolve().parents[2] / "wow-engine"
if str(WOW_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(WOW_ENGINE_ROOT))

from v17.core_intelligence import LearningHypothesis
from v17.core_intelligence_compounding import (
    build_challenger_proposals,
    build_market_memory,
    evaluate_promotion_review,
    extract_signal_memories,
    summarize_markets,
    summarize_signals,
    summarize_specialists,
)


def _observation(i: int, *, probability: float = 0.70, target: int = 1, specialist: str = "wow.mlb-k-expert"):
    residual = target - probability
    return {
        "observation_id": f"10000000-0000-0000-0000-{i:012d}",
        "source_prediction_kind": "PROP",
        "source_prediction_id": f"20000000-0000-0000-0000-{i:012d}",
        "sport": "MLB",
        "market_family": "PLAYER_PROP",
        "stat_type": "PITCHER_STRIKEOUTS",
        "model_family": "MLB_K_MODEL_V1",
        "model_artifact_version": "artifact-1",
        "probability": probability,
        "outcome_target": target,
        "residual": residual,
        "brier_score": residual * residual,
        "log_loss": None,
        "specialist_id": specialist,
    }


def test_market_memory_compares_frozen_model_to_close_without_rewriting_probability():
    obs = _observation(1, probability=0.70, target=1)
    row = build_market_memory(
        obs,
        opening_market_probability=0.55,
        closing_market_probability=0.60,
        specialist_id="wow.mlb-k-expert",
    )
    assert row.model_probability == 0.70
    assert row.market_move == 0.05
    assert row.model_minus_closing == 0.10
    assert row.brier_advantage_vs_close > 0
    assert row.model_outperformed_close is True
    assert row.can_execute is False


def test_signal_memory_uses_pregame_receipt_only_and_not_postgame_failure_label():
    obs = _observation(2)
    prediction = {
        "primary_failure_path": "HIGH_PITCH_EFFICIENCY",
        "market_prior_quality": "A",
        "market_prior_weight": 0.20,
        "effective_sample_size": 88,
        "raw_model_probability": 0.68,
        "independent_model_probability": 0.72,
        "calibrated_probability": 0.70,
        "market_prior_probability": 0.56,
        "regime_probabilities_json": {"BASE": 0.7, "VOLATILE": 0.3},
        "failure_cause_tags": ["LINEUP_UNCERTAINTY"],
        "failure_category": "POSTGAME_SHOULD_NEVER_APPEAR",
    }
    rows = extract_signal_memories(obs, prediction, specialist_id="wow.mlb-k-expert")
    names = {row.signal_name for row in rows}
    assert "PRIMARY_FAILURE_PATH" in names
    assert "REGIME_BASE" in names
    assert "TAG_LINEUP_UNCERTAINTY" in names
    assert all("POSTGAME_SHOULD_NEVER_APPEAR" not in (row.signal_value_text or "") for row in rows)
    assert all(row.can_execute is False for row in rows)


def test_signal_scorecards_require_evidence_and_measure_context_lift():
    memories = []
    for i in range(24):
        obs = _observation(i, probability=0.60, target=1 if i < 18 else 0)
        prediction = {"market_prior_quality": "A" if i < 12 else "B"}
        memories.extend(extract_signal_memories(obs, prediction, specialist_id="wow.mlb-k-expert"))
    cards = summarize_signals(memories, min_samples=10)
    quality = [card for card in cards if card.signal_name == "MARKET_PRIOR_QUALITY"]
    assert len(quality) == 2
    assert all(card.ready_for_review for card in quality)
    assert any(card.observed_lift_vs_context is not None for card in quality)


def test_market_and_specialist_scorecards_remain_context_scoped():
    observations = []
    market_rows = []
    for i in range(40):
        specialist = "wow.mlb-k-expert" if i < 20 else "wow.mlb-alt-k-expert"
        obs = _observation(i, probability=0.65, target=1 if i % 3 else 0, specialist=specialist)
        observations.append(obs)
        market_rows.append(build_market_memory(
            obs,
            opening_market_probability=0.55,
            closing_market_probability=0.60,
            specialist_id=specialist,
        ))
    market_cards = summarize_markets(market_rows, min_samples=10)
    specialist_cards = summarize_specialists(observations, market_rows, min_samples=10)
    assert len(market_cards) == 2
    assert len(specialist_cards) == 2
    assert {card.specialist_id for card in specialist_cards} == {"wow.mlb-k-expert", "wow.mlb-alt-k-expert"}
    assert all(card.ready_for_review for card in specialist_cards)


def test_challenger_lab_opens_inert_proposals_from_learning_evidence():
    hypothesis = LearningHypothesis(
        hypothesis_id="30000000-0000-0000-0000-000000000001",
        cohort_key="PROP|MLB|UNKNOWN|PLAYER_PROP|PITCHER_STRIKEOUTS|M|V|C",
        hypothesis_type="CALIBRATION_BIAS",
        direction="OVERCONFIDENT",
        evidence_n=60,
        metric_name="calibration_bias",
        metric_value=-0.12,
        threshold=0.075,
    )
    proposals = build_challenger_proposals(hypotheses=(hypothesis,))
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.proposal_type == "RECALIBRATION_CHALLENGER"
    assert proposal.requested_lifecycle_state == "CANDIDATE"
    assert proposal.shadow_required is True
    assert proposal.automatic_certification is False
    assert proposal.automatic_promotion is False
    assert proposal.probability_publishable is False
    assert proposal.can_execute is False


def test_promotion_review_fails_closed_until_every_governed_gate_passes():
    blocked = evaluate_promotion_review(
        challenger_id="candidate-1",
        target_key="MLB|MLB|OUTRIGHT_WINNER|candidate",
        holdout_n=150,
        champion_brier=0.24,
        challenger_brier=0.20,
        champion_log_loss=0.62,
        challenger_log_loss=0.57,
        champion_calibration_error=0.08,
        challenger_calibration_error=0.06,
        source_review_pass=False,
        certification_replay_pass=False,
    )
    assert blocked.eligible_for_governed_review is False
    assert "SOURCE_REVIEW_REQUIRED" in blocked.blockers
    assert "CERTIFICATION_REPLAY_REQUIRED" in blocked.blockers

    eligible = evaluate_promotion_review(
        challenger_id="candidate-1",
        target_key="MLB|MLB|OUTRIGHT_WINNER|candidate",
        holdout_n=150,
        champion_brier=0.24,
        challenger_brier=0.20,
        champion_log_loss=0.62,
        challenger_log_loss=0.57,
        champion_calibration_error=0.08,
        challenger_calibration_error=0.06,
        source_review_pass=True,
        certification_replay_pass=True,
    )
    assert eligible.eligible_for_governed_review is True
    assert eligible.status == "ELIGIBLE_FOR_GOVERNED_REVIEW"
    assert eligible.automatic_promotion is False
    assert eligible.probability_publishable is False
    assert eligible.can_execute is False
