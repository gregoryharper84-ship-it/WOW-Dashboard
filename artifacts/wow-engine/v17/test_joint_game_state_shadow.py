"""Regression checks for the V17 aligned-draw research bridge, not certification."""
from datetime import datetime, timezone, timedelta
import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).with_name("joint_game_state_shadow.py")
SPEC = importlib.util.spec_from_file_location("joint_game_state_shadow", SOURCE)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

N = module.MIN_SIMULATION_DRAWS
NOW = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)


def packet():
    return dict(
        event_id="event:2026:11:12:home-away",
        fitted_source_model_id="governed-fitted-research-source",
        fitted_artifact_sha256="a" * 64,
        captured_at=NOW,
        event_start_at=NOW + timedelta(hours=2),
        home_scores=[10, 10, 8, 8] * (N // 4),
        away_scores=[7, 10, 11, 7] * (N // 4),
        home_spread=-3,
        game_total=17,
        moneyline_tie_rule="PUSH",
        simulation_seed=42,
        player_props={"home:p1:points": (2, [3, 2, 1, 4] * (N // 4))},
    )


def test_deterministic_coherent_market_projection_and_terminal_hold():
    p = packet()
    result = module.project_joint_shadow(**p)
    assert result == module.project_joint_shadow(**p)
    assert result["moneyline"] == {"home_win": 0.5, "away_win": 0.25, "draw": 0.25}
    assert result["home_spread"] == {"line": -3.0, "more": 0.0, "exact": 0.25, "less": 0.75}
    assert result["game_total"] == {"line": 17.0, "more": 0.5, "exact": 0.25, "less": 0.25}
    assert result["player_props"]["home:p1:points"] == {"line": 2.0, "more": 0.5, "exact": 0.25, "less": 0.25}
    for market in [result["moneyline"], result["home_spread"], result["game_total"], *result["player_props"].values()]:
        assert abs(sum(value for key, value in market.items() if key not in {"line"}) - 1) < 1e-12
    assert result["shadow_only"] is True
    assert result["probability_publishable"] is False
    assert result["automatic_certification"] is False
    assert result["can_execute"] is False
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert result["runtime_generation"] == "V17_ACTIVE"
    assert result["custom_gpt_identity"] == "WOW_BETTING_ENGINE"
    assert result["dry_run_only_no_live_trading_no_market_orders"] is True


@pytest.mark.parametrize("field,value,error", [
    ("home_scores", [0, 1], "50000"),
    ("away_scores", [1] * (N - 1), "aligned"),
    ("fitted_artifact_sha256", "not-a-digest", "SHA-256"),
    ("home_spread", float("nan"), "finite"),
    ("game_total", float("inf"), "finite"),
    ("game_total", -1, "nonnegative"),
    ("game_total", 10 ** 400, "finite"),
    ("event_id", "", "explicit"),
    ("simulation_seed", True, "nonnegative integer"),
    ("moneyline_tie_rule", "UNSPECIFIED", "moneyline_tie_rule"),
    ("player_props", {"bad": (2, [0] * 100)}, "align"),
    ("player_props", {"bad": (2, [0.0] * (N - 1) + [float("nan")])}, "finite"),
    ("player_props", {"bad": (2, [0.0] * (N - 1) + [-1])}, "nonnegative"),
])
def test_fail_closed_invalid_packets(field, value, error):
    p = packet()
    p[field] = value
    with pytest.raises(ValueError, match=error):
        module.project_joint_shadow(**p)


def test_no_ties_policy_rejects_unresolved_score_draws():
    p = packet()
    p["moneyline_tie_rule"] = "NO_TIES"
    with pytest.raises(ValueError, match="unresolved"):
        module.project_joint_shadow(**p)


def test_pregame_time_and_player_alignment_are_enforced():
    p = packet()
    p["captured_at"] = NOW + timedelta(hours=3)
    with pytest.raises(ValueError, match="strictly precede"):
        module.project_joint_shadow(**p)
    p = packet()
    p["captured_at"] = NOW.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone aware"):
        module.project_joint_shadow(**p)


def test_threshold_monotonicity_is_draw_derived():
    p = packet()
    p["moneyline_tie_rule"] = "THREE_WAY"
    high = module.project_joint_shadow(**{**p, "game_total": 20, "home_spread": -5})
    low = module.project_joint_shadow(**{**p, "game_total": 16, "home_spread": -2})
    assert high["game_total"]["more"] <= low["game_total"]["more"]
    assert high["home_spread"]["more"] <= low["home_spread"]["more"]
    assert high["moneyline"] == low["moneyline"]
