"""Reproduce full 2021-26 first-party NHL historical *research* fit.

The exact same 6,560 official games and prior-result features feed both
Poisson and fitted REG/OT/SO components. No results here are certified
or available to the V17 scoring Action.
"""
from pathlib import Path

from v17.nhl_total_goals_official_fitted_replay import (
    CAN_EXECUTE, PROBABILITY_PUBLISHABLE, RANK_ELIGIBLE,
    replay_real_official_nhl_total_regimes,
)

FIXTURE_DIR=Path(__file__).parent / "tests" / "fixtures"


def test_genuine_five_season_nhl_mixture_and_poisson_matched_replay():
    evidence = replay_real_official_nhl_total_regimes(fixture_directory=FIXTURE_DIR)
    assert evidence["status"] == "NHL_TOTALS_REAL_5SEASON_REGIME_REPLAY_RESEARCH_ONLY"
    assert evidence["canonical_official_games"] == 6560
    assert evidence["firstparty_source_instances_verified"] == 13120
    assert 6000 < evidence["eligible_prior_game_feature_rows"] < 6300
    assert evidence["training_rows"] > 2200
    assert evidence["calibration_rows"] > 1100
    assert evidence["development_rows"] > 1100
    assert evidence["fit_years"] == [2021,2022]
    assert evidence["calibration_year"] == 2023
    assert evidence["development_year"] == 2024
    assert evidence["development_was_previously_consulted"] is True
    assert evidence["2025_reserve_not_used_in_this_fit"] is True
    assert evidence["2025_was_previously_unblinded_for_another_candidate"] is True
    assert len(evidence["regime_model_training_sha256"])==64
    assert sum(sum(t.values()) for t in evidence["regime_by_season"].values()) == 6560
    for name, counts in evidence["regime_by_season"].items():
        assert sum(counts.values()) == 1312, name
        assert set(counts)=={"REG","OT","SO"}
        assert all(value>0 for value in counts.values())
    metrics=evidence["research_development_metrics"]
    assert 0 < metrics["mixture_under55_brier"] < 1
    assert 0 < metrics["poisson_under55_brier"] < 1
    assert -1 < metrics["mixture_minus_poisson_under55_brier"] < 1
    assert 0 < metrics["mixture_exact_six_brier"] < 1
    assert 0 < metrics["observed_exact_six"] < 1
    assert metrics["mixture_goals_nll"] > 0
    assert metrics["poisson_goals_nll"] > 0
    assert -20 < metrics["mixture_minus_poisson_goals_nll"] < 20
    assert evidence["historical_source_audit"]["immutable_pregame_snapshots"] is False
    assert evidence["data_license_certification_complete"] is False
    assert evidence["goalie_starter_pregame_confirmed"] is False
    assert evidence["certified_probability_package"] is None
    assert evidence["governed_calibrated_lower_bound"] is None
    assert evidence["current_pregame_action_receipt"] is None
    assert evidence["model_registered_for_production"] is False
    assert evidence["automatic_certification"] is False
    assert evidence["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert evidence["can_execute"] is False and evidence["probability_publishable"] is False
    assert CAN_EXECUTE is False and PROBABILITY_PUBLISHABLE is False and RANK_ELIGIBLE is False
