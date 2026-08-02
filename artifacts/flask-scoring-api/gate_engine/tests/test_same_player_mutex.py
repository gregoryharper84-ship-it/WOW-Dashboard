"""
test_same_player_mutex.py
Tests for gate_engine/same_player_mutex.py
WOW-PATCH-2026-08-01-MULTI-WINDOW-PROP-PERSISTENCE-AND-DISTRIBUTION-AUDIT
"""
import pytest
from gate_engine.same_player_mutex import (
    detect_same_player_clusters,
    apply_same_player_mutex,
    check_same_game_correlated_legs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(player: str, event: str, lb: float = 0.65, **extra) -> dict:
    return {
        "player_name":              player,
        "game_date":                event,
        "team":                     "NYL",
        "calibrated_lower_bound":   lb,
        "prop":                     f"{player}-{event}",
        **extra,
    }


def _row_pid(player_id: str, event_id: str, lb: float = 0.65, **extra) -> dict:
    return {
        "player_id":                player_id,
        "event_id":                 event_id,
        "calibrated_lower_bound":   lb,
        "prop":                     f"{player_id}-{event_id}",
        **extra,
    }


# ---------------------------------------------------------------------------
# detect_same_player_clusters
# ---------------------------------------------------------------------------

class TestDetectSamePlayerClusters:

    def test_no_clusters_when_all_different_players(self):
        rows = [_row("Plum", "2026-08-02"), _row("Mitchell", "2026-08-02")]
        r    = detect_same_player_clusters(rows)
        assert r["total_clusters"] == 0
        assert r["clusters"] == []

    def test_detects_cluster_when_same_player_same_date(self):
        rows = [
            _row("Kelsey Plum", "2026-08-02", lb=0.70),
            _row("Kelsey Plum", "2026-08-02", lb=0.65),
        ]
        r = detect_same_player_clusters(rows)
        assert r["total_clusters"] == 1
        assert r["clusters"][0]["cluster_label"] == "SAME_PLAYER_SHARED_THESIS"
        assert r["total_blocked"] == 1   # 2 members, 1 primary, 1 blocked

    def test_primary_is_highest_calibrated_lower_bound(self):
        rows = [
            _row("Plum", "2026-08-02", lb=0.60),
            _row("Plum", "2026-08-02", lb=0.75),
            _row("Plum", "2026-08-02", lb=0.68),
        ]
        r       = detect_same_player_clusters(rows)
        cluster = r["clusters"][0]
        primary_row_id = cluster["primary_survivor_row_id"]
        # The row with lb=0.75 should be primary
        primary_row = next(row for row in rows if id(row) == primary_row_id)
        assert abs(primary_row["calibrated_lower_bound"] - 0.75) < 0.001

    def test_detects_two_independent_clusters(self):
        rows = [
            _row("Plum",     "2026-08-02", lb=0.70),
            _row("Plum",     "2026-08-02", lb=0.65),
            _row("Mitchell", "2026-08-03", lb=0.72),
            _row("Mitchell", "2026-08-03", lb=0.68),
        ]
        r = detect_same_player_clusters(rows)
        assert r["total_clusters"] == 2

    def test_player_id_takes_priority_over_name(self):
        rows = [
            _row_pid("P001", "EVT-A", lb=0.70),
            _row_pid("P001", "EVT-A", lb=0.60),
        ]
        r = detect_same_player_clusters(rows)
        assert r["total_clusters"] == 1

    def test_missing_player_id_falls_through_to_name(self):
        rows = [
            {"player_name": "Plum", "game_date": "2026-08-02", "team": "LVA",
             "calibrated_lower_bound": 0.70, "prop": "p1"},
            {"player_name": "Plum", "game_date": "2026-08-02", "team": "LVA",
             "calibrated_lower_bound": 0.60, "prop": "p2"},
        ]
        r = detect_same_player_clusters(rows)
        assert r["total_clusters"] == 1

    def test_shared_risks_listed(self):
        rows = [_row("Plum", "2026-08-02"), _row("Plum", "2026-08-02")]
        r    = detect_same_player_clusters(rows)
        shared = r["clusters"][0]["shared_risks"]
        assert "minutes" in shared
        assert "injury_risk" in shared
        assert "blowout_risk" in shared

    def test_can_execute_false(self):
        r = detect_same_player_clusters([])
        assert r["can_execute"] is False


# ---------------------------------------------------------------------------
# apply_same_player_mutex
# ---------------------------------------------------------------------------

class TestApplySamePlayerMutex:

    def test_non_primary_blocked_by_default(self):
        rows = [
            _row("Plum", "2026-08-02", lb=0.75),
            _row("Plum", "2026-08-02", lb=0.60),
        ]
        apply_same_player_mutex(rows)
        # One should be blocked, one should not
        blocked = [r for r in rows if r.get("same_player_blocked")]
        primary = [r for r in rows if not r.get("same_player_blocked") and r.get("shared_thesis_cluster")]
        assert len(blocked) == 1
        assert len(primary) == 1

    def test_blocked_carries_reason(self):
        rows = [_row("Plum", "2026-08-02", lb=0.75), _row("Plum", "2026-08-02", lb=0.60)]
        apply_same_player_mutex(rows)
        blocked = next(r for r in rows if r.get("same_player_blocked"))
        assert blocked["same_player_block_reason"] is not None
        assert "SAME_PLAYER_SHARED_THESIS" in blocked["same_player_block_reason"]

    def test_joint_dependence_override_allows_both(self):
        rows = [_row("Plum", "2026-08-02", lb=0.75), _row("Plum", "2026-08-02", lb=0.60)]
        apply_same_player_mutex(rows, joint_dependence_modeled=True)
        blocked = [r for r in rows if r.get("same_player_blocked")]
        assert len(blocked) == 0

    def test_unrelated_rows_unaffected(self):
        rows = [
            _row("Plum",     "2026-08-02", lb=0.75),
            _row("Mitchell", "2026-08-02", lb=0.72),
        ]
        apply_same_player_mutex(rows)
        assert all(not r.get("same_player_blocked") for r in rows)
        assert all(not r.get("shared_thesis_cluster") for r in rows)

    def test_result_counts_correct(self):
        rows = [
            _row("Plum", "2026-08-02", lb=0.75),
            _row("Plum", "2026-08-02", lb=0.68),
            _row("Plum", "2026-08-02", lb=0.60),
        ]
        result = apply_same_player_mutex(rows)
        assert result["rows_blocked"] == 2
        assert result["clusters_found"] == 1
        assert result["can_execute"] is False

    def test_regression_plum_points_and_assists(self):
        """Plum points + Plum assists must become a shared-thesis cluster."""
        rows = [
            {"player_name": "Kelsey Plum", "game_date": "2026-08-02", "team": "LVA",
             "prop": "points_14.5", "calibrated_lower_bound": 0.68},
            {"player_name": "Kelsey Plum", "game_date": "2026-08-02", "team": "LVA",
             "prop": "assists_4.5", "calibrated_lower_bound": 0.62},
        ]
        result = apply_same_player_mutex(rows)
        assert result["clusters_found"] == 1
        assert result["rows_blocked"] == 1
        blocked = next(r for r in rows if r.get("same_player_blocked"))
        assert blocked["prop"] == "assists_4.5"   # lower lb blocked


# ---------------------------------------------------------------------------
# check_same_game_correlated_legs
# ---------------------------------------------------------------------------

class TestCheckSameGameCorrelatedLegs:

    def test_no_groups_when_single_player_per_game(self):
        rows = [
            _row("Plum", "2026-08-02"),
            _row("Mitchell", "2026-08-03"),
        ]
        r = check_same_game_correlated_legs(rows)
        assert r["same_game_groups"] == []
        assert r["independence_warning"] is None

    def test_detects_dodgers_trio_pattern(self):
        """3 different players in same game → same-game correlation group."""
        rows = [
            {"player_name": "Freeman",  "game_date": "2026-08-02", "team": "LAD",
             "prop": "hits_0.5", "calibrated_lower_bound": 0.72},
            {"player_name": "Pages",    "game_date": "2026-08-02", "team": "LAD",
             "prop": "hits_0.5", "calibrated_lower_bound": 0.68},
            {"player_name": "Rafaela",  "game_date": "2026-08-02", "team": "LAD",
             "prop": "hits_0.5", "calibrated_lower_bound": 0.65},
        ]
        r = check_same_game_correlated_legs(rows)
        assert len(r["same_game_groups"]) >= 1
        assert r["total_correlated_legs"] == 3
        assert r["independence_warning"] is not None
        assert "independent probability multiplication is prohibited" in r["independence_warning"].lower() or \
               "prohibited" in r["independence_warning"]

    def test_warning_mentions_event_group_count(self):
        rows = [
            {"player_name": "Freeman", "game_date": "2026-08-02", "team": "LAD",
             "prop": "p1", "calibrated_lower_bound": 0.70},
            {"player_name": "Pages",   "game_date": "2026-08-02", "team": "LAD",
             "prop": "p2", "calibrated_lower_bound": 0.68},
        ]
        r = check_same_game_correlated_legs(rows)
        assert r["independence_warning"] is not None

    def test_shared_factors_listed(self):
        rows = [
            {"player_name": "Freeman", "game_date": "2026-08-02", "team": "LAD",
             "prop": "p1", "calibrated_lower_bound": 0.70},
            {"player_name": "Pages",   "game_date": "2026-08-02", "team": "LAD",
             "prop": "p2", "calibrated_lower_bound": 0.68},
        ]
        r = check_same_game_correlated_legs(rows)
        shared = r["same_game_groups"][0]["shared_factors"]
        assert "pitcher_performance" in shared
        assert "game_environment" in shared

    def test_can_execute_false(self):
        r = check_same_game_correlated_legs([])
        assert r["can_execute"] is False
