from __future__ import annotations

import pytest

from v17.nfl_pickem_weekly_equity import WEEKLY_EQUITY_BLOCKED, WEEKLY_EQUITY_READY
from v17.nfl_pickem_weekly_equity_bridge import optimize_weekly_win_equity_from_governed_rows


def _source_row(event_id: str = "g1", home_p: float = 0.60) -> dict:
    away_p = 1.0 - home_p
    selected = "H" if home_p >= away_p else "A"
    opponent = "A" if selected == "H" else "H"
    selected_p = max(home_p, away_p)
    return {
        "event_prediction_id": f"pred-{event_id}",
        "model_version": "NFL_CHAMPION_TEST",
        "model_timestamp": "2026-09-30T23:00:00Z",
        "calibration_method": "TEST_CALIBRATOR",
        "calibration_version": "TEST_V1",
        "source_snapshot_id": f"snap-{event_id}",
        "calibrated_home_probability": home_p,
        "calibrated_away_probability": away_p,
        "calibrated_home_lower_bound": max(0.0, home_p - 0.04),
        "calibrated_home_upper_bound": min(1.0, home_p + 0.04),
        "calibrated_away_lower_bound": max(0.0, away_p - 0.04),
        "calibrated_away_upper_bound": min(1.0, away_p + 0.04),
        "calibrated_selection_probability": selected_p,
        "rank_calibrated_lower_bound": max(0.0, selected_p - 0.04),
        "calibrated_selection_upper_bound": min(1.0, selected_p + 0.04),
        "selected_participant": selected,
        "opponent": opponent,
        "sporting_probability_completed": True,
        "sporting_probability_status": "COMPLETED",
        "probability_fields_withheld": False,
        "model_probability_available": True,
        "probability_publishable": True,
        "rank_eligible": True,
        "terminal_label": "FINAL_APPROVED",
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "code": "GOVERNED_PROBABILITY_PUBLISHED",
        "blockers": [],
        "candidate_envelope": {
            "official_event_id": event_id,
            "sport": "NFL",
            "league": "NFL",
            "home_team": "H",
            "away_team": "A",
            "source_snapshot_id": f"snap-{event_id}",
        },
        "can_execute": False,
    }


def _shares(home_share: float = 0.99) -> list[dict]:
    return [
        {
            "official_event_id": "g1",
            "home_pick_share": home_share,
            "away_pick_share": 1.0 - home_share,
            "source": "TEST_POOL_SNAPSHOT",
            "snapshot_id": "share-snap",
            "observed_at": "2026-09-30T23:30:00Z",
            "freshness_status": "CURRENT",
        }
    ]


def test_bridge_uses_full_source_bounds_for_contrarian_selection():
    out = optimize_weekly_win_equity_from_governed_rows(
        [_source_row()],
        expected_game_count=1,
        pool_entries=100,
        opponent_pick_shares=_shares(),
        max_candidate_flips=1,
    )
    assert out["status"] == WEEKLY_EQUITY_READY
    assert out["baseline_board_status"] == "PICKEM_BOARD_READY"
    assert out["picks"][0]["weekly_equity_pick"] == "A"
    assert out["picks"][0]["selected_calibrated_lower_bound"] == pytest.approx(0.36)
    assert out["picks"][0]["selected_calibrated_upper_bound"] == pytest.approx(0.44)
    assert out["weekly_equity_two_sided_bounds_from_governed_source"] is True
    assert out["sporting_probability_modified"] is False
    assert out["can_execute"] is False


def test_bridge_never_derives_missing_opposite_side_bounds():
    row = _source_row()
    row.pop("calibrated_away_lower_bound")
    row.pop("calibrated_away_upper_bound")
    out = optimize_weekly_win_equity_from_governed_rows(
        [row],
        expected_game_count=1,
        pool_entries=100,
        opponent_pick_shares=_shares(),
        max_candidate_flips=1,
    )
    assert out["status"] == WEEKLY_EQUITY_BLOCKED
    assert "PICKEM_WEEKLY_EQUITY_TWO_SIDED_BOUNDS_REQUIRED" in out["blockers"]


def test_bridge_preserves_typed_baseline_failure():
    row = _source_row()
    row.update(
        {
            "code": "MODEL_SCORER_FAILED",
            "sporting_probability_completed": False,
            "sporting_probability_status": "MODEL_SCORER_FAILED",
            "blockers": ["NFL_FITTED_SCORER_FAILED"],
        }
    )
    out = optimize_weekly_win_equity_from_governed_rows(
        [row],
        expected_game_count=1,
        pool_entries=10,
        opponent_pick_shares=_shares(0.50),
        max_candidate_flips=1,
    )
    assert out["status"] == WEEKLY_EQUITY_BLOCKED
    assert out["baseline_blocked_event_count"] == 1
    assert "PICKEM_BASELINE_BOARD_NOT_READY" in out["blockers"]
    assert out["can_execute"] is False
