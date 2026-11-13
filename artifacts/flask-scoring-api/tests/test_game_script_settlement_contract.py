"""Research-only game-script contract: 1X2 mass, missingness and no promotions."""
from __future__ import annotations

import pytest

from gate_engine.moneyline.game_state_sim import (
    can_execute,
    run_game_state_simulation,
)

SOCCER_INPUTS = {
    "home_xg_per_game": 1.35,
    "away_xg_per_game": 1.20,
    "h2h_draw_rate": 0.30,
}


@pytest.mark.parametrize("sport", ("SOCCER", "EPL", "MLS"))
def test_soccer_preserves_regulation_draw_without_binary_conditioning(sport):
    sim = run_game_state_simulation(
        {"sport": sport}, SOCCER_INPUTS, 0.48, n_sims=8000, seed=20
    )
    data = sim.to_dict()
    outcomes = data["three_way_probabilities"]
    assert sim.simulation_ran
    assert data["settlement_basis"] == "SOCCER_REGULATION_1X2"
    assert data["script_probability_certified"] is False
    assert data["script_probability_publishable"] is False
    assert 0.15 < outcomes["draw"] < 0.50
    assert sum(outcomes.values()) == pytest.approx(1.0, abs=1e-10)
    assert sim.adjusted_prob == pytest.approx(sim.three_way_probabilities["home"])
    assert sim.adjusted_prob < (
        sim.three_way_probabilities["home"] /
        (sim.three_way_probabilities["home"] + sim.three_way_probabilities["away"])
    )
    assert data["regime_distribution"]["draw_frequency"] == pytest.approx(outcomes["draw"], abs=1e-4)


def test_soccer_reproducible_and_tactical_partition_normalized():
    kwargs = {"h2h_draw_rate": 1.0, "home_xg_per_game": 2.9, "away_xg_per_game": 0.9}
    a = run_game_state_simulation({"sport": "SOCCER"}, kwargs, 0.8, 4000, seed=9)
    b = run_game_state_simulation({"sport": "SOCCER"}, kwargs, 0.8, 4000, seed=9)
    assert a.to_dict() == b.to_dict()
    assert sum(value for key, value in a.regime_distribution.items() if key != "draw_frequency") == pytest.approx(1.0)


@pytest.mark.parametrize("inputs", [
    {},
    {"home_xg_per_game": 1.5},
    {"home_xg_per_game": None, "away_xg_per_game": 1.2},
    {"home_xg_per_game": float("nan"), "away_xg_per_game": 1.2},
    {"home_xg_per_game": float("inf"), "away_xg_per_game": 1.2},
    {"home_xg_per_game": -0.1, "away_xg_per_game": 1.2},
    {"home_xg_per_game": 1.5, "away_xg_per_game": 0.0},
    {"home_xg_per_game": 1.5, "away_xg_per_game": 1.2, "h2h_draw_rate": 1.3},
])
def test_soccer_missing_bad_inputs_cannot_invent_three_outcome_distribution(inputs):
    sim = run_game_state_simulation({"sport": "SOCCER"}, inputs, 0.51, seed=3)
    assert not sim.simulation_ran
    assert sim.adjusted_prob == sim.base_prob == 0.51
    assert sim.three_way_probabilities is None
    assert "three_way_probabilities" not in sim.to_dict()


@pytest.mark.parametrize("sport", ("NCAAF", "NCAAB", "TENNIS", "PGA", "MMA", "BOXING", "CRICKET", "UNKNOWN"))
def test_no_sport_specific_regime_does_not_change_model_probability(sport):
    sim = run_game_state_simulation({"sport": sport}, {}, 0.607, n_sims=5000, seed=42)
    assert sim.adjusted_prob == sim.base_prob == 0.607
    assert not sim.simulation_ran
    assert sim.n_sims == 0
    assert sim.regime_distribution == {}
    assert sim.to_dict()["script_probability_publishable"] is False
    assert any("no_probability_override" in item for item in sim.simulation_notes)


@pytest.mark.parametrize("n", (0, -1, True, 50001, 1.5, "10"))
def test_invalid_simulation_size_fails_closed(n):
    sim = run_game_state_simulation({"sport": "MLB"}, {}, 0.51, n_sims=n)
    assert not sim.simulation_ran
    assert "INVALID_SIMULATION_COUNT:simulation_skipped" in sim.simulation_notes


@pytest.mark.parametrize("p", (float("nan"), float("inf"), -0.1, 0.0, 1.0, None, "NaN"))
def test_invalid_probability_fails_closed(p):
    sim = run_game_state_simulation({"sport": "NFL"}, {}, p)
    assert not sim.simulation_ran
    assert "INVALID_BASE_PROB:simulation_skipped" in sim.simulation_notes


def test_execution_remains_unconditionally_disabled():
    assert can_execute is False
