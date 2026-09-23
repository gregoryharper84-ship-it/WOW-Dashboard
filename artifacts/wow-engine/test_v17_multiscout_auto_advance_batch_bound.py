from __future__ import annotations

from v17 import multiscout_auto_advance as core
from v17 import multiscout_auto_advance_oidc as oidc


def _handoff(event_count: int = 32) -> dict:
    events = []
    for index in range(event_count):
        events.append(
            {
                "official_event_id": f"event-{index}",
                "sport_key": "baseball_mlb",
                "commence_time": "2026-09-18T00:10:00Z",
                "home_team": f"Home {index}",
                "away_team": f"Away {index}",
                "route": "LLP_TEAM_BETTING_ENGINE",
                "discovery_status": "DISCOVERY_ONLY",
                "research_ceiling": "RESEARCH_INTEREST",
                "market_evidence": [],
            }
        )
    return {
        "schema_version": "wow.v17.nightly_multiscout.v1",
        "status": "DISCOVERY_COMPLETE",
        "generated_at": "2026-09-16T19:00:00Z",
        "run_id": "wow-scout-batch-bound",
        "research_run_id": "wow-scout-batch-bound",
        "model_handoff_ready": True,
        "model_handoff": {
            "prop_candidates": [],
            "team_event_candidates": events,
        },
        "governance": {
            "sportsbook_implied_probability_is_model_probability": False,
            "scout_consensus_is_model_probability": False,
            "v17_terminal_reducer_is_terminal_authority": True,
            "can_execute": False,
        },
    }


def test_oidc_nightly_path_bounds_large_team_event_slates():
    assert oidc.TEAM_EVENT_BATCH_ROWS == 8
    assert core.MAX_TEAM_EVENT_ROWS == 8
    assert oidc.OIDC_MAX_IN_FLIGHT == 2

    dispatch = core.build_dispatch(_handoff())
    batches = dispatch["team_event_batches"]

    assert dispatch["mapping"]["mapped_team_events"] == 32
    assert dispatch["mapping"]["downstream_team_objective_rows"] == 64
    assert len(batches) == 8
    assert sum(len(batch["rows"]) for batch in batches) == 64
    assert all(1 <= len(batch["rows"]) <= 8 for batch in batches)
    assert dispatch["governance"]["can_execute"] is False
