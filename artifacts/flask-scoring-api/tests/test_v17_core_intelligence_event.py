from __future__ import annotations

import sys
from pathlib import Path

WOW_ENGINE_ROOT = Path(__file__).resolve().parents[2] / "wow-engine"
if str(WOW_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(WOW_ENGINE_ROOT))

from v17.core_intelligence import build_learning_observation


def test_event_observation_is_advisory_home_probability_forecast():
    prediction = {
        "event_prediction_id": "11111111-1111-1111-1111-111111111111",
        "sport": "MLB",
        "league": "MLB",
        "market_family": "OUTRIGHT_WINNER",
        "direction": "HOME",
        "model_family": "wow.mlb-game-win-probability-expert",
        "model_version": "mlb-event-v1",
        "calibration_version": "cal-v1",
        "calibrated_probability": 0.64,
        "calibrated_probability_lower_bound": 0.58,
    }
    outcome = {
        "event_prediction_id": prediction["event_prediction_id"],
        "official_result": "WIN",
        "settlement_source": "official_final_score",
        "settlement_timestamp": "2026-09-20T12:00:00-05:00",
    }
    observation = build_learning_observation(
        prediction,
        outcome,
        source_prediction_kind="EVENT",
    )
    assert observation.source_prediction_kind == "EVENT"
    assert observation.direction == "HOME"
    assert observation.probability == 0.64
    assert observation.outcome_target == 1
    assert observation.can_execute is False
    assert observation.authority == "ADVISORY_ONLY"
