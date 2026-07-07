"""
test_mutex_groups.py — Phase 1 Item 3: Mutex grouping + best-candidate selection

Proves that:
- Same-player overlapping stats produce a mutex group
- Best candidate (by label priority then edge) is selected
- Non-selected rows get DUPLICATE_EXPOSURE_BLOCK + mutex_group_id
- Pitcher-script stats (Ks, outs, pitches) are grouped together
- Rows already terminated are not grouped
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from gate_engine.mutex_groups import run as run_mutex, _group_key, _stat_family
from gate_engine.labels import PropLabel


def _row(player, prop_type, line=22.5, direction="MORE",
         terminal_label=PropLabel.MODEL_QUALIFIED_HOLD.value, edge=0.0):
    return {
        "row_id":        f"{player}:{prop_type}",
        "player":        player,
        "prop_type":     prop_type,
        "line":          line,
        "direction":     direction,
        "terminal_label": terminal_label,
        "blockers":      [],
        "gates":         {"ev_gate": {"edge_score": edge}},
    }


class TestStatFamilyMapping:
    def test_points_family(self):
        assert _stat_family("points") == "points"
        assert _stat_family("pts+reb") == "points"
        assert _stat_family("PRA") == "points"

    def test_pitcher_script_family(self):
        assert _stat_family("pitcher strikeouts") == "pitcher_script"
        assert _stat_family("pitching outs") == "pitcher_script"
        assert _stat_family("pitches thrown") == "pitcher_script"

    def test_unknown_family_is_none(self):
        assert _stat_family("three pointers") is None
        assert _stat_family("tackles") is None

    def test_rebounds_family(self):
        assert _stat_family("rebounds") == "rebounds"


class TestGroupKey:
    def test_same_player_same_family_same_key(self):
        r1 = _row("Howard", "Points")
        r2 = _row("Howard", "Pts+Reb")
        assert _group_key(r1) == _group_key(r2)

    def test_different_player_different_key(self):
        r1 = _row("Howard", "Points")
        r2 = _row("Atkins", "Points")
        assert _group_key(r1) != _group_key(r2)

    def test_unknown_family_returns_none(self):
        r = _row("Howard", "Three Pointers")
        assert _group_key(r) is None


class TestMutexGrouping:
    def test_howard_points_vs_ptsreb_grouped(self):
        r1 = _row("Natasha Howard", "Points", 15.5, "MORE",
                  PropLabel.MODEL_QUALIFIED_HOLD.value, edge=4.2)
        r2 = _row("Natasha Howard", "Pts+Reb", 23.0, "MORE",
                  PropLabel.MODEL_QUALIFIED_HOLD.value, edge=2.1)
        report = run_mutex([r1, r2])
        assert len(report) == 1
        assert report[0]["candidate_count"] == 2

    def test_best_edge_selected(self):
        r1 = _row("Natasha Howard", "Points", 15.5, "MORE",
                  PropLabel.MODEL_QUALIFIED_HOLD.value, edge=5.0)
        r2 = _row("Natasha Howard", "Pts+Reb", 23.0, "MORE",
                  PropLabel.MODEL_QUALIFIED_HOLD.value, edge=2.0)
        run_mutex([r1, r2])
        assert r1.get("preferred_candidate") is True
        assert r2.get("preferred_candidate") is False
        assert r2.get("rejected_due_to_mutex") is True
        assert r2.get("terminal_label") == PropLabel.DUPLICATE_EXPOSURE_BLOCK.value

    def test_higher_label_beats_better_edge(self):
        r1 = _row("Atkins", "Points", 10.0, "MORE",
                  PropLabel.MONEY_QUALIFIED.value, edge=2.0)
        r2 = _row("Atkins", "Pts+Asts", 12.5, "MORE",
                  PropLabel.MODEL_QUALIFIED_HOLD.value, edge=9.9)
        run_mutex([r1, r2])
        assert r1.get("preferred_candidate") is True
        assert r2.get("terminal_label") == PropLabel.DUPLICATE_EXPOSURE_BLOCK.value

    def test_pitcher_script_grouped(self):
        r1 = _row("Sánchez", "Pitcher Strikeouts", 6.5, "MORE",
                  PropLabel.MODEL_QUALIFIED_HOLD.value, edge=3.0)
        r2 = _row("Sánchez", "Pitching Outs", 20.0, "LESS",
                  PropLabel.MODEL_QUALIFIED_HOLD.value, edge=1.0)
        r3 = _row("Sánchez", "Pitches Thrown", 87.5, "LESS",
                  PropLabel.RESEARCH_INTEREST.value, edge=0.5)
        report = run_mutex([r1, r2, r3])
        assert len(report) == 1
        assert report[0]["candidate_count"] == 3
        assert report[0]["selected_prop"].lower() in ("pitcher strikeouts", "ks")

    def test_mutex_group_id_attached(self):
        r1 = _row("Freeland", "Pitching Outs", 16.0, "LESS",
                  PropLabel.RESEARCH_INTEREST.value, edge=1.0)
        r2 = _row("Freeland", "Pitches Thrown", 87.5, "LESS",
                  PropLabel.RESEARCH_INTEREST.value, edge=0.5)
        run_mutex([r1, r2])
        assert r1.get("mutex_group_id") is not None
        assert r2.get("mutex_group_id") == r1.get("mutex_group_id")

    def test_already_rejected_row_not_regrouped(self):
        r1 = _row("Howard", "Points", 15.5, "MORE",
                  PropLabel.REJECT_DATA_QUALITY.value, edge=0.0)
        r2 = _row("Howard", "Pts+Reb", 23.0, "MORE",
                  PropLabel.MODEL_QUALIFIED_HOLD.value, edge=3.0)
        report = run_mutex([r1, r2])
        assert r1.get("terminal_label") == PropLabel.REJECT_DATA_QUALITY.value
        assert r2.get("preferred_candidate") is True

    def test_single_row_in_family_no_rejection(self):
        r = _row("Howard", "Points", 15.5, "MORE",
                 PropLabel.MODEL_QUALIFIED_HOLD.value, edge=3.0)
        report = run_mutex([r])
        assert r.get("rejected_due_to_mutex") is not True
        assert r.get("terminal_label") == PropLabel.MODEL_QUALIFIED_HOLD.value

    def test_different_players_not_grouped(self):
        r1 = _row("Chourio", "Batter Hits", 1.5, "MORE",
                  PropLabel.MODEL_QUALIFIED_HOLD.value, edge=3.0)
        r2 = _row("Contreras", "Batter Hits", 1.5, "MORE",
                  PropLabel.MODEL_QUALIFIED_HOLD.value, edge=2.0)
        report = run_mutex([r1, r2])
        assert len(report) == 0
        assert r1.get("rejected_due_to_mutex") is not True
        assert r2.get("rejected_due_to_mutex") is not True
