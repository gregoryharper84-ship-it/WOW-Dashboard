"""
test_pp_thresholds.py — Phase 1 Item 2: PrizePicks threshold conversion tests

Proves that:
- Whole-number MORE lines require cash_threshold = line + 1 (not line)
- Whole-number LESS lines require cash_threshold = line - 1
- Half-point lines have no push threshold
- sportsbook_comp_note warns against using adjacent half-point markets
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from gate_engine.pp_thresholds import compute_pp_thresholds, run as run_threshold


class TestMoreWholeNumber:
    def test_more_5_cash_is_6(self):
        t = compute_pp_thresholds(5.0, "MORE")
        assert t["cash_threshold"] == 6.0

    def test_more_5_push_is_5(self):
        t = compute_pp_thresholds(5.0, "MORE")
        assert t["push_threshold"] == 5.0

    def test_more_5_loss_is_4(self):
        t = compute_pp_thresholds(5.0, "MORE")
        assert t["loss_threshold"] == 4.0

    def test_more_5_whole_number_flag(self):
        t = compute_pp_thresholds(5.0, "MORE")
        assert t["whole_number_line"] is True
        assert t["push_possible"] is True

    def test_more_10_cash_is_11(self):
        t = compute_pp_thresholds(10.0, "MORE")
        assert t["cash_threshold"] == 11.0

    def test_more_10_note_warns_against_adjacent(self):
        t = compute_pp_thresholds(10.0, "MORE")
        assert t["sportsbook_comp_note"] is not None
        assert "9.5" in t["sportsbook_comp_note"] or "does NOT" in t["sportsbook_comp_note"]

    def test_more_20_outs_cash_is_21(self):
        t = compute_pp_thresholds(20.0, "MORE")
        assert t["cash_threshold"] == 21.0

    def test_more_18_pts_asts_cash_is_19(self):
        t = compute_pp_thresholds(18.0, "MORE")
        assert t["cash_threshold"] == 19.0


class TestLessWholeNumber:
    def test_less_20_outs_cash_is_19(self):
        t = compute_pp_thresholds(20.0, "LESS")
        assert t["cash_threshold"] == 19.0

    def test_less_5_assists_cash_is_4(self):
        t = compute_pp_thresholds(5.0, "LESS")
        assert t["cash_threshold"] == 4.0

    def test_less_5_push_is_5(self):
        t = compute_pp_thresholds(5.0, "LESS")
        assert t["push_threshold"] == 5.0

    def test_less_5_loss_is_6(self):
        t = compute_pp_thresholds(5.0, "LESS")
        assert t["loss_threshold"] == 6.0

    def test_less_whole_note_present(self):
        t = compute_pp_thresholds(20.0, "LESS")
        assert t["sportsbook_comp_note"] is not None


class TestHalfPointLines:
    def test_more_half_no_push(self):
        t = compute_pp_thresholds(6.5, "MORE")
        assert t["push_threshold"] is None
        assert t["push_possible"] is False

    def test_less_half_no_push(self):
        t = compute_pp_thresholds(16.5, "LESS")
        assert t["push_threshold"] is None
        assert t["push_possible"] is False

    def test_more_half_no_note(self):
        t = compute_pp_thresholds(22.5, "MORE")
        assert t["sportsbook_comp_note"] is None

    def test_more_half_cash_above_line(self):
        t = compute_pp_thresholds(7.5, "MORE")
        assert t["cash_threshold"] > 7.5

    def test_less_half_cash_below_line(self):
        t = compute_pp_thresholds(16.5, "LESS")
        assert t["cash_threshold"] < 16.5


class TestRunAttachesToRow:
    def _make_row(self, line, side):
        return {"row_id": "r1", "player": "P", "prop_type": "points", "line": line, "direction": side}

    def test_thresholds_attached(self):
        row = self._make_row(5.0, "MORE")
        run_threshold(row)
        assert "pp_thresholds" in row
        assert row["pp_thresholds"]["cash_threshold"] == 6.0

    def test_none_line_handled(self):
        row = self._make_row(None, "MORE")
        run_threshold(row)
        assert row["pp_thresholds"]["cash_threshold"] is None

    def test_whole_number_sportsbook_note_warns(self):
        """Critical: whole-number MORE lines must warn against using 4.5/9.5 comps."""
        row = self._make_row(10.0, "MORE")
        run_threshold(row)
        note = row["pp_thresholds"]["sportsbook_comp_note"]
        assert note is not None
        assert "does NOT" in note or "NOT" in note
