"""Regression: minutes similarity alone cannot satisfy WNBA role-valid history."""
from __future__ import annotations

from datetime import datetime, timezone


def test_minutes_only_history_cannot_be_full_role_match(monkeypatch):
    from gate_engine import wnba_generative_gate as gate

    now = datetime.now(timezone.utc).isoformat()
    row = {
        "sport": "WNBA",
        "player_name": "Role Test Player",
        "event_id": "WNBA-ROLE-1",
        "stat_key": "PTS",
        "line": 18.5,
        "side": "MORE",
        "role_status": "STARTER",
        "starter_flag": True,
        "role_timestamp": now,
        "gates": {
            "role_timestamp": {
                "passed": True,
                "role_staleness": "FRESH",
                "status_staleness": "FRESH",
            },
            "wnba_opportunity_gate": {
                "gate_passed": True,
                "gate_label": "PASS",
                "expected_minutes": 32.0,
                "usage_stability_score": 82,
                "shot_attempt_stability_score": 83,
                "opportunity_stability_score": 82,
            },
        },
        "blockers": [],
    }
    # Deliberately omit historical starter_flag and role. Minutes alone match.
    box_rows = [
        {
            "minutes": 32.0,
            "points": 20,
            "rebounds": 5,
            "assists": 4,
            "field_goal_attempts": 15,
            "usage_rate": 0.24,
            "source_timestamp": now,
        }
        for _ in range(10)
    ]
    enr = {
        "game_log": [17, 20, 21, 19, 22, 18, 24, 20, 19, 23],
        "box_score_log": box_rows,
        "projected_minutes": 32.0,
        "projected_pace": 81.0,
        "opponent_defense": {"def_rating": 103.0},
        "rest_days": 2,
        "blowout_probability": 0.10,
        "game_script": {"expected_margin": 3.0},
    }

    def _must_not_run(*args, **kwargs):
        raise AssertionError("model must not run on minutes-only role comparability")

    monkeypatch.setattr(gate._gen, "score", _must_not_run)
    gate.run(row, enr)

    packet = row["gates"]["wnba_full_model_evidence"]
    assert packet["role_valid_sample"]["strict_role_match_games"] == 0
    assert packet["model_input_ready"] is False
    assert row["gates"]["wnba_generative"]["model_status"] == "NOT_STARTED"
    assert row["probability_publication_allowed"] is False
    assert any("STRICT_ROLE_MATCH_SAMPLE_BLOCKED" in b for b in row["blockers"])
