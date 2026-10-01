from __future__ import annotations

from v17.nfl_pickem_weekly_equity import (
    WEEKLY_EQUITY_BLOCKED,
    WEEKLY_EQUITY_READY,
    optimize_weekly_win_equity,
)

CONTROLLING_SPECIALIST = "wow.nfl-game-win-probability-expert"


def _board(probability: float = 0.60) -> dict:
    away_probability = 1.0 - probability
    return {
        "submission_ready": True,
        "source_terminals_preserved": True,
        "picks": [
            {
                "official_event_id": "g1",
                "home_team": "H",
                "away_team": "A",
                "pool_pick": "H" if probability >= 0.5 else "A",
                "home_probability": probability,
                "away_probability": away_probability,
                "calibrated_home_lower_bound": max(0.0, probability - 0.04),
                "calibrated_home_upper_bound": min(1.0, probability + 0.04),
                "calibrated_away_lower_bound": max(0.0, away_probability - 0.04),
                "calibrated_away_upper_bound": min(1.0, away_probability + 0.04),
                "controlling_specialist": CONTROLLING_SPECIALIST,
                "source_terminal_label": "FINAL_APPROVED",
                "source_model_status": "GOVERNED_PROBABILITY_PUBLISHED",
                "source_prediction_id": "pred-g1",
                "source_snapshot_id": "snap-g1",
                "source_blockers": [],
                "can_execute": False,
            }
        ],
    }


def _shares(home_share: float) -> list[dict]:
    return [
        {
            "official_event_id": "g1",
            "home_pick_share": home_share,
            "away_pick_share": 1.0 - home_share,
            "source": "TEST_POOL_SNAPSHOT",
            "snapshot_id": "s1",
            "observed_at": "2026-09-30T23:00:00Z",
            "freshness_status": "CURRENT",
        }
    ]


def test_small_pool_keeps_higher_probability_favorite():
    out = optimize_weekly_win_equity(
        _board(0.60),
        pool_entries=2,
        opponent_pick_shares=_shares(0.99),
        max_candidate_flips=1,
    )
    assert out["status"] == WEEKLY_EQUITY_READY
    assert out["picks"][0]["weekly_equity_pick"] == "H"


def test_large_pool_can_flip_to_unpopular_underdog_without_probability_mutation():
    out = optimize_weekly_win_equity(
        _board(0.60),
        pool_entries=100,
        opponent_pick_shares=_shares(0.99),
        max_candidate_flips=1,
    )
    assert out["status"] == WEEKLY_EQUITY_READY
    assert out["picks"][0]["weekly_equity_pick"] == "A"
    assert out["picks"][0]["selected_governed_probability"] == 0.40
    assert out["sporting_probability_modified"] is False
    assert out["pool_popularity_used_as_sporting_probability"] is False


def test_missing_pick_share_fails_closed_instead_of_inventing_opponent_behavior():
    out = optimize_weekly_win_equity(
        _board(),
        pool_entries=10,
        opponent_pick_shares=[],
    )
    assert out["status"] == WEEKLY_EQUITY_BLOCKED
    assert "PICKEM_OPPONENT_PICK_SHARE_MISSING_EVENT" in out["blockers"]


def test_pick_share_provenance_is_required():
    share = _shares(0.70)[0]
    share.pop("source")
    out = optimize_weekly_win_equity(
        _board(),
        pool_entries=10,
        opponent_pick_shares=[share],
    )
    assert out["status"] == WEEKLY_EQUITY_BLOCKED
    assert "PICKEM_OPPONENT_PICK_SHARE_PROVENANCE_REQUIRED" in out["blockers"]


def test_stale_pick_share_fails_closed():
    share = _shares(0.70)[0]
    share["freshness_status"] = "STALE"
    out = optimize_weekly_win_equity(
        _board(),
        pool_entries=10,
        opponent_pick_shares=[share],
    )
    assert out["status"] == WEEKLY_EQUITY_BLOCKED
    assert "PICKEM_OPPONENT_PICK_SHARE_NOT_CURRENT" in out["blockers"]


def test_popularity_never_changes_governed_probability_package():
    out = optimize_weekly_win_equity(
        _board(0.58),
        pool_entries=100,
        opponent_pick_shares=_shares(0.995),
        max_candidate_flips=1,
    )
    pick = out["picks"][0]
    assert pick["home_governed_probability"] == 0.58
    assert abs(pick["away_governed_probability"] - 0.42) < 1e-12
    assert out["pool_popularity_used_as_sporting_probability"] is False
    assert out["can_execute"] is False


def test_extreme_popularity_cannot_break_probability_coherence():
    out = optimize_weekly_win_equity(
        _board(0.51),
        pool_entries=100,
        opponent_pick_shares=_shares(1.0),
        max_candidate_flips=1,
    )
    assert out["status"] == WEEKLY_EQUITY_READY
    pick = out["picks"][0]
    assert pick["weekly_equity_pick"] in {"H", "A"}
    assert abs(
        pick["home_governed_probability"]
        + pick["away_governed_probability"]
        - 1.0
    ) < 1e-12
    assert out["sporting_probability_modified"] is False


def test_full_16_game_board_is_bounded_and_terminal():
    picks: list[dict] = []
    pick_shares: list[dict] = []
    for index in range(16):
        probability = 0.52 + (index % 7) * 0.02
        away_probability = 1.0 - probability
        event_id = f"g{index:02d}"
        picks.append(
            {
                "official_event_id": event_id,
                "home_team": f"H{index}",
                "away_team": f"A{index}",
                "pool_pick": f"H{index}",
                "home_probability": probability,
                "away_probability": away_probability,
                "calibrated_home_lower_bound": max(0.0, probability - 0.04),
                "calibrated_home_upper_bound": min(1.0, probability + 0.04),
                "calibrated_away_lower_bound": max(0.0, away_probability - 0.04),
                "calibrated_away_upper_bound": min(1.0, away_probability + 0.04),
                "controlling_specialist": CONTROLLING_SPECIALIST,
                "source_terminal_label": "FINAL_APPROVED",
                "source_model_status": "GOVERNED_PROBABILITY_PUBLISHED",
                "source_prediction_id": f"pred-{event_id}",
                "source_snapshot_id": f"snap-{event_id}",
                "source_blockers": [],
                "can_execute": False,
            }
        )
        home_share = 0.20 + (index % 5) * 0.15
        pick_shares.append(
            {
                "official_event_id": event_id,
                "home_pick_share": home_share,
                "away_pick_share": 1.0 - home_share,
                "source": "TEST_POOL_SNAPSHOT",
                "snapshot_id": "week4",
                "observed_at": "2026-09-30T23:00:00Z",
                "freshness_status": "CURRENT",
            }
        )

    out = optimize_weekly_win_equity(
        {
            "submission_ready": True,
            "source_terminals_preserved": True,
            "picks": picks,
        },
        pool_entries=32,
        opponent_pick_shares=pick_shares,
        max_candidate_flips=2,
    )
    assert out["status"] == WEEKLY_EQUITY_READY
    assert out["game_count"] == 16
    assert len(out["picks"]) == 16
    assert out["candidates_evaluated"] == 137
    assert out["can_execute"] is False
    assert all(pick["sporting_probability_modified"] is False for pick in out["picks"])


def test_tie_share_proxy_is_deterministic_and_discloses_tiebreaker_limit():
    out1 = optimize_weekly_win_equity(
        _board(0.55),
        pool_entries=8,
        opponent_pick_shares=_shares(0.55),
        max_candidate_flips=1,
    )
    out2 = optimize_weekly_win_equity(
        _board(0.55),
        pool_entries=8,
        opponent_pick_shares=_shares(0.55),
        max_candidate_flips=1,
    )
    assert out1["optimized_first_place_equity"] == out2["optimized_first_place_equity"]
    assert 0.0 <= out1["optimized_first_place_equity"] <= 1.0
    assert out1["tiebreaker_opponent_guess_distribution_modeled"] is False


def test_changed_pick_keeps_two_sided_calibration_and_source_provenance():
    out = optimize_weekly_win_equity(
        _board(0.60),
        pool_entries=100,
        opponent_pick_shares=_shares(0.99),
        max_candidate_flips=1,
    )
    pick = out["picks"][0]
    assert pick["selection_changed"] is True
    assert (
        pick["selected_calibrated_lower_bound"]
        <= pick["selected_governed_probability"]
        <= pick["selected_calibrated_upper_bound"]
    )
    assert pick["controlling_specialist"] == CONTROLLING_SPECIALIST
    assert pick["source_terminal_label"] == "FINAL_APPROVED"
    assert pick["decision_reason_code"] == "WEEKLY_EQUITY_IMPROVEMENT_WITHIN_SEARCH"
    assert pick["conditional_first_place_equity_gain_if_reverted"] >= 0.0
