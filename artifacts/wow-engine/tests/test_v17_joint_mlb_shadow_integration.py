"""Class C shadow contract integration against the existing MLB fitted-sampler code.

This is a deterministic research fixture, not a validated real-event replay.
Source inputs mirror existing MLB sampler test structure. No API, DB, model
registry, certification or terminal reducer is modified.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import numpy as np
import pytest

from mlb_event_specialist_v16 import LineupAdjustment, WeatherContext
from v17 import joint_game_state_shadow as joint
from v17 import mlb_run_line_shadow as mlb
from v17.mlb_extra_inning_margin_challenger import resolve_tied_nine_inning_samples


def _mlb_aligned_final_samples():
    """Exercise the real MLB nine-inning draw generator and governed tie resolver."""
    n = joint.MIN_SIMULATION_DRAWS
    lineup = LineupAdjustment(1.0, 9, .750, .750, ())
    weather = WeatherContext(1.0, .02, 70.0, 5.0, None, "Clear", "Open")
    features = {
        "opp_starter_era": 4.0, "opp_starter_bb_rate": .09,
        "opp_starter_prior_starts": 10.0, "opp_starter_pitches_last3": 250.0,
        "opp_bp_era": 4.0, "opp_bp_bb_rate": .09,
        "opp_bp_pitches_3d": 180.0, "opp_errors_pg": .55,
    }
    seed = 12345
    home_9, away_9 = mlb._simulate_nine_inning_samples(
        home_mu=4.4, away_mu=4.1, home_alpha=.31, away_alpha=.32,
        lineup_home=lineup, lineup_away=lineup, weather=weather,
        home_features=features, away_features=features,
        seed=seed, simulation_count=n,
    )
    artifact = mlb.frozen_2024_extra_inning_margin_artifact()
    home_final, away_final = resolve_tied_nine_inning_samples(
        home_runs9=home_9.tolist(), away_runs9=away_9.tolist(),
        extra_inning_home_win_probability=.5,
        artifact=artifact, seed=seed ^ 0x5A17E11,
    )
    return home_final, away_final, seed


def test_mlb_existing_simulator_matches_research_joint_spread_and_moneyline():
    home, away, seed = _mlb_aligned_final_samples()
    assert len(home) == len(away) == joint.MIN_SIMULATION_DRAWS
    assert all(h != a for h, a in zip(home, away))
    start = datetime(2026, 11, 12, 20, tzinfo=timezone.utc)
    packet = {
        "event_id": "RESEARCH_MLB_FIXTURE_NOT_A_LIVE_EVENT",
        "fitted_source_model_id": "MLB_V16_RESEARCH_FIXTURE_UNCERTIFIED",
        "fitted_artifact_sha256": sha256(b"MLB-research-sampler-fixture-v1").hexdigest(),
        "captured_at": start - timedelta(hours=1),
        "event_start_at": start,
        "home_scores": home,
        "away_scores": away,
        "home_spread": -1.0,
        "game_total": 8.0,
        "moneyline_tie_rule": "NO_TIES",
        "simulation_seed": seed,
    }
    actual = joint.project_joint_shadow(**packet)
    legacy = mlb.score_final_run_line_samples(home, away, home_run_line=-1.0)
    assert actual["home_spread"]["more"] == pytest.approx(legacy["p_cover"])
    assert actual["home_spread"]["exact"] == pytest.approx(legacy["p_push"])
    assert actual["home_spread"]["less"] == pytest.approx(legacy["p_not_cover"])
    assert actual["moneyline"]["draw"] == 0.0
    assert actual["moneyline"]["home_win"] == pytest.approx(
        sum(h > a for h, a in zip(home, away)) / len(home)
    )
    assert actual["game_total"]["more"] == pytest.approx(
        sum(h + a > 8 for h, a in zip(home, away)) / len(home)
    )
    assert actual["game_total"]["exact"] == pytest.approx(
        sum(h + a == 8 for h, a in zip(home, away)) / len(home)
    )
    assert actual["probability_publishable"] is False
    assert actual["can_execute"] is False
    assert actual["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert joint.project_joint_shadow(**packet) == actual


def test_mlb_unresolved_ninth_inning_ties_must_not_be_misrepresented_as_final():
    home, away, seed = _mlb_aligned_final_samples()
    home[0] = away[0]
    with pytest.raises(ValueError, match="unresolved final-score ties"):
        joint.project_joint_shadow(
            event_id="RESEARCH_FIXTURE", fitted_source_model_id="RESEARCH_ONLY",
            fitted_artifact_sha256=sha256(b"fixture").hexdigest(),
            captured_at=datetime(2026, 11, 12, tzinfo=timezone.utc),
            event_start_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
            home_scores=home, away_scores=away,
            home_spread=-1.5, game_total=8.5, moneyline_tie_rule="NO_TIES",
            simulation_seed=seed,
        )
