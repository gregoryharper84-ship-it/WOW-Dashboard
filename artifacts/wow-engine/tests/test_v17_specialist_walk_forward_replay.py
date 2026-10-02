from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from v17.specialist_walk_forward_replay import ReplayPolicy, run_walk_forward_replay


def _game(i, outcome=0):
    return {
        "id": f"g{i}",
        "game_timestamp": datetime(2026, 1, i + 1, tzinfo=timezone.utc),
        "outcome_index": outcome,
    }


def test_threshold_pass_still_cannot_auto_promote():
    games = [_game(i, 0) for i in range(4)]

    def hydrate(game, cutoff):
        return {"max_source_timestamp": cutoff, "x": 1.0}

    receipt = run_walk_forward_replay(
        challenger_id="example_v1",
        historical_games=games,
        hydrate_features=hydrate,
        predict_distribution=lambda features: [0.9, 0.1],
        policy=ReplayPolicy(
            policy_id="test",
            max_ece=0.2,
            max_brier=0.1,
            minimum_validation_rows=4,
            ece_bins=5,
        ),
    )
    assert receipt["status"] == "CANDIDATE_PASSED_THRESHOLDS_PENDING_GOVERNED_REVIEW"
    assert receipt["promotion_allowed"] is False
    assert receipt["can_execute"] is False


def test_future_feature_timestamp_invalidates_replay():
    game = _game(1, 0)

    def leaky_hydrate(game, cutoff):
        return {
            "max_source_timestamp": datetime(2026, 12, 1, tzinfo=timezone.utc),
            "x": 999,
        }

    receipt = run_walk_forward_replay(
        challenger_id="leaky_v1",
        historical_games=[game],
        hydrate_features=leaky_hydrate,
        predict_distribution=lambda features: [0.8, 0.2],
        policy=ReplayPolicy("test", 1.0, 1.0, 1),
    )
    assert receipt["status"] == "REPLAY_INVALID_LOOKAHEAD_DETECTED"
    assert receipt["typed_failure"] == "HISTORICAL_FEATURE_LOOKAHEAD_DETECTED"
    assert receipt["promotion_allowed"] is False


def test_distribution_normalization_is_enforced():
    game = _game(1, 0)

    def hydrate(game, cutoff):
        return {"max_source_timestamp": cutoff}

    try:
        run_walk_forward_replay(
            challenger_id="bad_probs_v1",
            historical_games=[game],
            hydrate_features=hydrate,
            predict_distribution=lambda features: [0.8, 0.8],
            policy=ReplayPolicy("test", 1.0, 1.0, 1),
        )
    except ValueError as exc:
        assert "normalize" in str(exc)
    else:
        raise AssertionError("invalid distribution was accepted")
