"""
test_governance_patch.py — WOW-PATCH-2026-07-15-PROP-CALIBRATION-EXPOSURE-AND-SLIP-GOVERNANCE

Mandatory regression fixtures from the spec table plus supporting tests.

| Fixture | Expected |
|---------|----------|
| Mabrey MORE 3.5 vs sportsbook 3         | REJECT_MARKET_ADVERSE_PUSH_LOSS     |
| Iriafen LESS 10.5 vs sportsbook 11      | REJECT_MARKET_ADVERSE_PUSH_LOSS     |
| Austin LESS 8.5 vs consensus 10         | REJECT_MARKET_ADVERSE_THRESHOLD     |
| Citron MORE 13.5 vs consensus 18        | candidate may pass (not adverse)     |
| Citron 11.5 and 13.5 separate entries   | DUPLICATE_EXPOSURE_BLOCK            |
| Iriafen P+R MORE + rebounds LESS        | COMPONENT_COMPOSITE_CONFLICT        |
| Austin FGA MORE + rebounds LESS         | REJECT_CONTRADICTORY_ROLE_STATE     |
| 3 player unders unreconciled team opp   | REJECT_OPPORTUNITY_SUM_MISMATCH     |
| Polymarket-only 72%                     | confidence capped at MARKET_VERIFIED_HOLD |
| 5-pick same-game Power                  | REJECT_BAD_STRUCTURE                |
| Bonus entry repeats cash-entry distrib  | DUPLICATE_EXPOSURE_BLOCK (model exposure) |
| GPT/Replit governance hashes differ     | HTTP 409 / RUN_INVALID_GOVERNANCE_MISMATCH |
| Hashes match, all gates pass            | normal scoring continues            |
"""
from __future__ import annotations

import pytest
from gate_engine import market_adverse, component_composite, opportunity_state
from gate_engine import slip_structure
from gate_engine.governance import (
    compute_governance_hash,
    get_governance_status,
    validate_handshake,
    is_in_prop_reliability_freeze,
)
from gate_engine.labels import PropLabel
from gate_engine.exposure_gate import ExposureLedger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(
    player: str = "Test Player",
    prop_type: str = "Points",
    line: float = 20.0,
    direction: str = "MORE",
    game: str = "TEAM_A vs TEAM_B",
    sport: str = "WNBA",
    **kwargs,
) -> dict:
    row = {
        "row_id":       f"row_{player.lower().replace(' ', '_')}_{prop_type.lower()}",
        "player":       player,
        "sport":        sport,
        "prop_type":    prop_type,
        "line":         line,
        "direction":    direction,
        "game":         game,
        "blockers":     [],
        "gates":        {},
        "terminal_label": None,
    }
    row.update(kwargs)
    return row


# ===========================================================================
# 1. Market Adverse Gate — check_market_adverse()
# ===========================================================================

class TestMarketAdverseCheckFunction:
    """Unit tests for the check_market_adverse() function."""

    def test_mabrey_more_35_vs_sb_3_push_loss(self):
        """Mabrey MORE 3.5 vs sportsbook O/U 3 → PUSH_LOSS (SB push at 3 converts to PP loss)."""
        label, detail = market_adverse.check_market_adverse(
            pp_line=3.5, direction="MORE", sb_line=3.0
        )
        assert label == "REJECT_MARKET_ADVERSE_PUSH_LOSS"
        assert detail["adverse_type"] == "PUSH_LOSS"
        assert detail["sb_push_values"] == [3.0]

    def test_iriafen_less_105_vs_sb_11_push_loss(self):
        """Iriafen LESS 10.5 vs sportsbook 11 → PUSH_LOSS."""
        label, detail = market_adverse.check_market_adverse(
            pp_line=10.5, direction="LESS", sb_line=11.0
        )
        assert label == "REJECT_MARKET_ADVERSE_PUSH_LOSS"
        assert detail["adverse_type"] == "PUSH_LOSS"

    def test_austin_less_85_vs_consensus_10_threshold(self):
        """Austin LESS 8.5 vs consensus 10 → THRESHOLD (gap 1.5, materially more extreme)."""
        label, detail = market_adverse.check_market_adverse(
            pp_line=8.5, direction="LESS", sb_line=10.0, source="consensus"
        )
        assert label == "REJECT_MARKET_ADVERSE_THRESHOLD"
        assert detail["adverse_type"] == "THRESHOLD"
        assert abs(detail["threshold_delta"] - 1.5) < 0.01

    def test_citron_more_135_vs_consensus_18_not_adverse(self):
        """Citron MORE 13.5 vs consensus 18 → NOT adverse (SB is more demanding, PP is favorable)."""
        label, detail = market_adverse.check_market_adverse(
            pp_line=13.5, direction="MORE", sb_line=18.0, source="consensus"
        )
        assert label is None
        assert detail["adverse"] is False

    def test_no_sb_line_skips_check(self):
        """If no sportsbook line, no adverse check fires."""
        label, detail = market_adverse.check_market_adverse(
            pp_line=20.0, direction="MORE", sb_line=None
        )
        assert label is None

    def test_exact_match_not_adverse(self):
        """PP line == SB line → not adverse in either direction."""
        for direction in ("MORE", "LESS"):
            label, _ = market_adverse.check_market_adverse(
                pp_line=15.0, direction=direction, sb_line=15.0
            )
            assert label is None

    def test_more_pp_below_sb_not_adverse(self):
        """MORE with PP line < SB line → PP is more favorable, not adverse."""
        label, _ = market_adverse.check_market_adverse(
            pp_line=10.0, direction="MORE", sb_line=12.0
        )
        assert label is None

    def test_less_pp_above_sb_not_adverse(self):
        """LESS with PP line > SB line → PP is more favorable, not adverse."""
        label, _ = market_adverse.check_market_adverse(
            pp_line=14.0, direction="LESS", sb_line=12.0
        )
        assert label is None


class TestMarketAdverseGateRun:
    """Integration tests for market_adverse.run() — mutates row."""

    def test_mabrey_row_gets_reject_push_loss(self):
        row = _make_row(player="Mabrey", line=3.5, direction="MORE")
        result = market_adverse.run(row, sportsbook_line=3.0)
        gate = result["gates"]["market_adverse"]
        assert gate["passed"] is False
        assert gate["label"] == "REJECT_MARKET_ADVERSE_PUSH_LOSS"
        assert result["terminal_label"] == PropLabel.REJECT_MARKET_ADVERSE_PUSH_LOSS.value

    def test_iriafen_row_gets_reject_push_loss(self):
        row = _make_row(player="Iriafen", line=10.5, direction="LESS")
        result = market_adverse.run(row, sportsbook_line=11.0)
        gate = result["gates"]["market_adverse"]
        assert gate["passed"] is False
        assert gate["label"] == "REJECT_MARKET_ADVERSE_PUSH_LOSS"

    def test_austin_row_gets_reject_threshold(self):
        row = _make_row(player="Austin", line=8.5, direction="LESS")
        result = market_adverse.run(row, consensus_line=10.0)
        gate = result["gates"]["market_adverse"]
        assert gate["passed"] is False
        assert gate["label"] == "REJECT_MARKET_ADVERSE_THRESHOLD"
        assert result["terminal_label"] == PropLabel.REJECT_MARKET_ADVERSE_THRESHOLD.value

    def test_citron_135_passes_gate(self):
        row = _make_row(player="Citron", line=13.5, direction="MORE")
        result = market_adverse.run(row, consensus_line=18.0)
        gate = result["gates"]["market_adverse"]
        assert gate["passed"] is True
        assert result["terminal_label"] is None

    def test_push_loss_wins_over_threshold_when_both_fire(self):
        """When PUSH_LOSS fires on one source and THRESHOLD on another, PUSH_LOSS wins."""
        row = _make_row(line=3.5, direction="MORE")
        result = market_adverse.run(row, sportsbook_line=3.0, consensus_line=10.0)
        gate = result["gates"]["market_adverse"]
        assert gate["label"] == "REJECT_MARKET_ADVERSE_PUSH_LOSS"

    def test_no_market_ref_passes(self):
        row = _make_row(line=20.0, direction="MORE")
        result = market_adverse.run(row)
        assert result["gates"]["market_adverse"]["passed"] is True
        assert result["gates"]["market_adverse"]["skipped"] is True

    def test_blocker_appended(self):
        row = _make_row(player="Mabrey", line=3.5, direction="MORE")
        result = market_adverse.run(row, sportsbook_line=3.0)
        assert any("MARKET_ADVERSE" in b for b in result["blockers"])


# ===========================================================================
# 2. Settlement Normalisation
# ===========================================================================

class TestSettlementNormalization:
    def test_pp_more_no_push(self):
        s = market_adverse.normalize_settlement(3.5, "MORE", "prizepicks")
        assert s["push_values"] == []
        assert "result > 3.5" in s["win_zone"]

    def test_sportsbook_whole_has_push(self):
        s = market_adverse.normalize_settlement(3.0, "MORE", "sportsbook")
        assert s["push_values"] == [3.0]

    def test_sportsbook_half_no_push(self):
        s = market_adverse.normalize_settlement(3.5, "MORE", "sportsbook")
        assert s["push_values"] == []

    def test_sportsbook_less_whole(self):
        s = market_adverse.normalize_settlement(11.0, "LESS", "sportsbook")
        assert s["push_values"] == [11.0]


# ===========================================================================
# 3. Component / Composite Mutex
# ===========================================================================

class TestComponentComposite:
    def test_iriafen_pr_more_plus_rebounds_less_conflict(self):
        """Iriafen P+R MORE + rebounds LESS → COMPONENT_COMPOSITE_CONFLICT."""
        rows = [
            _make_row(player="Iriafen", prop_type="P+R",      direction="MORE", line=18.5),
            _make_row(player="Iriafen", prop_type="Rebounds",  direction="LESS", line=9.5),
        ]
        report = component_composite.run(rows)
        assert report["conflicts_found"] > 0
        labels = [c["label"] for c in report["conflicts"]]
        assert PropLabel.COMPONENT_COMPOSITE_CONFLICT.value in labels

    def test_pra_more_points_less_conflict(self):
        """PRA MORE + points LESS → COMPONENT_COMPOSITE_CONFLICT."""
        rows = [
            _make_row(player="PlayerX", prop_type="PRA",    direction="MORE", line=40.0),
            _make_row(player="PlayerX", prop_type="Points", direction="LESS", line=18.0),
        ]
        report = component_composite.run(rows)
        assert report["conflicts_found"] > 0
        assert any(
            c["label"] == PropLabel.COMPONENT_COMPOSITE_CONFLICT.value
            for c in report["conflicts"]
        )

    def test_austin_fga_more_rebounds_less_contradictory_role(self):
        """Austin FGA MORE + rebounds LESS (no joint model) → REJECT_CONTRADICTORY_ROLE_STATE."""
        rows = [
            _make_row(player="Austin", prop_type="FGA",      direction="MORE", line=12.0),
            _make_row(player="Austin", prop_type="Rebounds",  direction="LESS", line=5.5),
        ]
        report = component_composite.run(rows)
        assert report["conflicts_found"] > 0
        labels = [c["label"] for c in report["conflicts"]]
        assert PropLabel.REJECT_CONTRADICTORY_ROLE_STATE.value in labels

    def test_same_stat_opposing_directions_conflict(self):
        """Points MORE + Points LESS (same player) → COMPONENT_COMPOSITE_CONFLICT."""
        rows = [
            _make_row(player="PlayerY", prop_type="Points", direction="MORE", line=20.0),
            _make_row(player="PlayerY", prop_type="Points", direction="LESS", line=22.0),
        ]
        report = component_composite.run(rows)
        assert report["conflicts_found"] > 0

    def test_different_players_no_conflict(self):
        """Different players with any combo → no conflict (no same-player comparison)."""
        rows = [
            _make_row(player="PlayerA", prop_type="P+R",      direction="MORE", line=18.0),
            _make_row(player="PlayerB", prop_type="Rebounds",  direction="LESS", line=9.0),
        ]
        report = component_composite.run(rows)
        assert report["conflicts_found"] == 0

    def test_compatible_directions_no_conflict(self):
        """P+R MORE + assists MORE → no conflict (no opposing component)."""
        rows = [
            _make_row(player="PlayerC", prop_type="P+R",    direction="MORE", line=18.0),
            _make_row(player="PlayerC", prop_type="Assists", direction="MORE", line=5.0),
        ]
        report = component_composite.run(rows)
        assert report["conflicts_found"] == 0

    def test_conflicting_rows_get_terminal_label(self):
        """Rows with component/composite conflicts get terminal labels stamped."""
        rows = [
            _make_row(player="Iriafen", prop_type="P+R",      direction="MORE", line=18.5),
            _make_row(player="Iriafen", prop_type="Rebounds",  direction="LESS", line=9.5),
        ]
        component_composite.run(rows)
        for row in rows:
            assert row["terminal_label"] is not None
            assert len(row["blockers"]) > 0

    def test_clean_rows_get_passed_gate(self):
        """Clean rows get passed=True on their component_composite gate."""
        rows = [
            _make_row(player="Solo", prop_type="Points", direction="MORE", line=20.0),
        ]
        component_composite.run(rows)
        assert rows[0]["gates"]["component_composite"]["passed"] is True


# ===========================================================================
# 4. Opportunity State
# ===========================================================================

class TestOpportunityState:
    def test_three_unders_unreconciled_team_mismatch(self):
        """3 player unders on same stat/event without team totals → REJECT_OPPORTUNITY_SUM_MISMATCH."""
        game = "TEAM_A vs TEAM_B"
        rows = [
            _make_row(player="P1", prop_type="Rebounds", direction="LESS", line=8.0,  game=game),
            _make_row(player="P2", prop_type="Rebounds", direction="LESS", line=7.0,  game=game),
            _make_row(player="P3", prop_type="Rebounds", direction="LESS", line=6.0,  game=game),
        ]
        report = opportunity_state.run(rows)
        assert report["passed"] is False
        assert len(report["conflicts"]) > 0
        assert report["conflicts"][0]["stat"] == "rebounds"
        for row in rows:
            assert row["terminal_label"] == PropLabel.REJECT_OPPORTUNITY_SUM_MISMATCH.value

    def test_two_unders_same_stat_no_mismatch(self):
        """Only 2 LESS entries on same stat — below threshold, no mismatch."""
        game = "TEAM_A vs TEAM_B"
        rows = [
            _make_row(player="P1", prop_type="Rebounds", direction="LESS", line=8.0, game=game),
            _make_row(player="P2", prop_type="Rebounds", direction="LESS", line=7.0, game=game),
        ]
        report = opportunity_state.run(rows)
        assert report["passed"] is True

    def test_three_unders_different_stats_no_mismatch(self):
        """3 LESS entries on different stats → no mismatch (each stat is reconcilable)."""
        game = "TEAM_A vs TEAM_B"
        rows = [
            _make_row(player="P1", prop_type="Rebounds", direction="LESS", line=8.0, game=game),
            _make_row(player="P2", prop_type="Points",   direction="LESS", line=18.0, game=game),
            _make_row(player="P3", prop_type="Assists",  direction="LESS", line=5.0, game=game),
        ]
        report = opportunity_state.run(rows)
        assert report["passed"] is True

    def test_three_unders_with_team_totals_reconcile(self):
        """3 LESS entries but team_totals sum allows it → reconciled, no mismatch."""
        game = "TEAM_A vs TEAM_B"
        rows = [
            _make_row(player="P1", prop_type="Rebounds", direction="LESS", line=8.0, game=game),
            _make_row(player="P2", prop_type="Rebounds", direction="LESS", line=7.0, game=game),
            _make_row(player="P3", prop_type="Rebounds", direction="LESS", line=6.0, game=game),
        ]
        # Team total of 42 rebounds means 8+7+6=21 ≤ 42 → reconciled
        report = opportunity_state.run(rows, team_totals={"rebounds": 42.0})
        assert report["passed"] is True

    def test_mismatch_rows_get_blocker(self):
        """REJECT_OPPORTUNITY_SUM_MISMATCH rows get a blocker string."""
        game = "GAME_X"
        rows = [
            _make_row(player="P1", prop_type="Points", direction="LESS", line=12.0, game=game),
            _make_row(player="P2", prop_type="Points", direction="LESS", line=11.0, game=game),
            _make_row(player="P3", prop_type="Points", direction="LESS", line=10.0, game=game),
        ]
        opportunity_state.run(rows)
        for row in rows:
            assert any("REJECT_OPPORTUNITY_SUM_MISMATCH" in b for b in row["blockers"])


# ===========================================================================
# 5. Duplicate Exposure — Citron 11.5 vs 13.5
# ===========================================================================

class TestDuplicateExposure:
    def test_citron_dual_lines_same_player_block(self):
        """Citron 11.5 and 13.5 on separate entries for same stat → player exposure block."""
        ledger = ExposureLedger(max_player=1, max_game=2, max_archetype=3)
        row_a = _make_row(player="Citron", prop_type="Points", line=11.5,
                          direction="MORE", game="GAME1")
        row_b = _make_row(player="Citron", prop_type="Points", line=13.5,
                          direction="MORE", game="GAME1")
        ledger.check_and_register(row_a)
        ledger.check_and_register(row_b)
        # Second Citron entry should be blocked
        gate_a = row_a["gates"]["exposure_gate"]
        gate_b = row_b["gates"]["exposure_gate"]
        assert gate_a["passed"] is True
        assert gate_b["passed"] is False
        assert any("PLAYER_EXPOSURE" in b for b in row_b["blockers"])

    def test_bonus_entry_counted_in_model_exposure(self):
        """Bonus entry repeating cash-entry distribution counts toward model exposure."""
        ledger = ExposureLedger(max_player=1, max_game=2, max_archetype=3)
        cash_row  = _make_row(player="Citron", prop_type="Points", line=13.5,
                              direction="MORE", game="GAME2")
        bonus_row = _make_row(player="Citron", prop_type="Points", line=13.5,
                              direction="MORE", game="GAME2")
        ledger.check_and_register(cash_row)
        ledger.check_and_register(bonus_row)
        # Bonus should be blocked — same player already registered
        assert bonus_row["gates"]["exposure_gate"]["passed"] is False


# ===========================================================================
# 6. Slip Structure — Prop Reliability Freeze
# ===========================================================================

class TestPropReliabilityFreeze:
    FREEZE_DATE = "2026-07-16"  # within 2026-07-15 to 2026-07-22

    def test_is_in_freeze_window(self):
        assert is_in_prop_reliability_freeze("2026-07-15") is True
        assert is_in_prop_reliability_freeze("2026-07-22") is True
        assert is_in_prop_reliability_freeze("2026-07-16") is True

    def test_outside_freeze_window(self):
        assert is_in_prop_reliability_freeze("2026-07-14") is False
        assert is_in_prop_reliability_freeze("2026-07-23") is False

    def test_five_pick_power_during_freeze_rejected(self):
        """5-pick same-game Power during freeze → REJECT_BAD_STRUCTURE on all rows."""
        rows = [
            _make_row(player=f"P{i}", prop_type="Points", direction="MORE",
                      line=float(15 + i), game="BIG_GAME") for i in range(5)
        ]
        result = slip_structure.run_slip(rows, slip_type="power", as_of=self.FREEZE_DATE)
        # All rows should carry REJECT_BAD_STRUCTURE
        for row in result:
            assert row.get("terminal_label") == PropLabel.REJECT_BAD_STRUCTURE.value, \
                f"Expected REJECT_BAD_STRUCTURE, got {row.get('terminal_label')}"

    def test_three_pick_power_during_freeze_passes(self):
        """3-pick Power during freeze is allowed."""
        rows = [
            _make_row(player=f"P{i}", prop_type="Points", direction="MORE",
                      line=float(15 + i), game=f"GAME{i}") for i in range(3)
        ]
        result = slip_structure.run_slip(rows, slip_type="power", as_of=self.FREEZE_DATE)
        for row in result:
            # No REJECT_BAD_STRUCTURE from freeze
            freeze_errors = [
                e for e in row.get("gates", {}).get("slip_structure", {}).get("slip_errors", [])
                if "REJECT_BAD_STRUCTURE" in e
            ]
            assert freeze_errors == []

    def test_four_pick_flex_during_freeze_rejected(self):
        """4-pick Flex during freeze → REJECT_BAD_STRUCTURE (max 3 legs)."""
        rows = [
            _make_row(player=f"P{i}", prop_type="Points", direction="MORE",
                      line=float(15 + i), game=f"GAME{i}") for i in range(4)
        ]
        result = slip_structure.run_slip(rows, slip_type="flex", as_of=self.FREEZE_DATE)
        for row in result:
            assert row.get("terminal_label") == PropLabel.REJECT_BAD_STRUCTURE.value

    def test_three_pick_flex_during_freeze_passes(self):
        """3-pick Flex during freeze is allowed."""
        rows = [
            _make_row(player=f"P{i}", prop_type="Points", direction="MORE",
                      line=float(15 + i), game=f"GAME{i}") for i in range(3)
        ]
        result = slip_structure.run_slip(rows, slip_type="flex", as_of=self.FREEZE_DATE)
        for row in result:
            freeze_errors = [
                e for e in row.get("gates", {}).get("slip_structure", {}).get("slip_errors", [])
                if "REJECT_BAD_STRUCTURE" in e
            ]
            assert freeze_errors == []

    def test_three_same_event_during_freeze_blocked(self):
        """3 legs from same event during freeze → REJECT_BAD_STRUCTURE."""
        rows = [
            _make_row(player=f"P{i}", prop_type="Points", direction="MORE",
                      line=float(15 + i), game="SAME_GAME") for i in range(3)
        ]
        result = slip_structure.run_slip(rows, slip_type="flex", as_of=self.FREEZE_DATE)
        has_event_error = any(
            "REJECT_BAD_STRUCTURE" in e
            for row in result
            for e in row.get("gates", {}).get("slip_structure", {}).get("slip_errors", [])
            if "SAME_EVENT" in e
        )
        assert has_event_error

    def test_six_pick_power_outside_freeze_allowed(self):
        """Outside freeze window, 6-pick Power is not blocked by freeze rules."""
        rows = [
            _make_row(player=f"P{i}", prop_type="Points", direction="MORE",
                      line=float(15 + i), game=f"GAME{i}") for i in range(6)
        ]
        result = slip_structure.run_slip(rows, slip_type="power", as_of="2026-07-23")
        for row in result:
            freeze_errors = [
                e for e in row.get("gates", {}).get("slip_structure", {}).get("slip_errors", [])
                if "REJECT_BAD_STRUCTURE" in e and "FREEZE" in e
            ]
            assert freeze_errors == [], f"Unexpected freeze error outside window: {freeze_errors}"

    def test_check_freeze_power_helper(self):
        """check_freeze_power() standalone helper works for route-level check."""
        five_rows = [_make_row() for _ in range(5)]
        result = slip_structure.check_freeze_power(five_rows, as_of=self.FREEZE_DATE)
        assert result["passed"] is False
        assert result["label"] == PropLabel.REJECT_BAD_STRUCTURE.value

        three_rows = [_make_row() for _ in range(3)]
        result2 = slip_structure.check_freeze_power(three_rows, as_of=self.FREEZE_DATE)
        assert result2["passed"] is True


# ===========================================================================
# 7. Source Ceiling — prediction-market-only
# ===========================================================================

class TestSourceCeiling:
    def test_polymarket_only_caps_at_market_verified_hold(self):
        """
        Prediction-market-only support cannot exceed MARKET_VERIFIED_HOLD / MEDIUM.
        Tested via pipeline.run_pipeline() with market_source_type=prediction_market
        and no sportsbook_line / consensus_line.
        """
        from gate_engine.pipeline import run_pipeline
        from datetime import date as _date
        today_str = "2026-07-15"  # matches system date; slate_validation won't reject
        rows = [
            {
                "player":       "Test Player",
                "sport":        "WNBA",
                "prop_type":    "Points",
                "line":         20.5,
                "direction":    "MORE",
                "slate_date":   today_str,
                "board_source": "PrizePicks",
                "game":         "TEAM_A vs TEAM_B",
            }
        ]
        enrichment_by_player = {
            "test player:points": {
                "game_log":          [22, 18, 25, 21, 19, 23, 20, 24, 17, 26],
                "status_payload":    {"status": "ACTIVE", "source": "ESPN",
                                      "dnp_risk": False, "minutes_restriction": False},
                "market_source_type": "prediction_market",
                # No sportsbook_line, no consensus_line — prediction market only
                # WNBA evidence-acquisition required fields
                "event_status":    "SCHEDULED",
                "role_timestamp":  "2026-07-15T10:00:00Z",
                "role_confirmation_age_minutes": 5,   # forces FRESH regardless of wall clock
                "projected_minutes": 34.0,
                "role_status": {
                    "active_status":     "ACTIVE",
                    "role_timestamp":    "2026-07-15T10:00:00Z",
                    "projected_minutes": 34.0,
                },
                "box_score_log": [
                    {"date": "2026-07-10", "PTS": 22, "REB": 6, "AST": 4, "MIN": 34, "FGA": 15},
                    {"date": "2026-07-07", "PTS": 18, "REB": 5, "AST": 3, "MIN": 30, "FGA": 13},
                    {"date": "2026-07-04", "PTS": 25, "REB": 7, "AST": 5, "MIN": 36, "FGA": 17},
                    {"date": "2026-07-01", "PTS": 21, "REB": 6, "AST": 4, "MIN": 33, "FGA": 14},
                    {"date": "2026-06-28", "PTS": 19, "REB": 5, "AST": 3, "MIN": 31, "FGA": 12},
                ],
                "matchup": {
                    "pace": 96.0, "opponent_defense": 108.0,
                    "position_defense": 111.0, "rebound_environment": 0.50,
                    "assist_environment": 0.60,
                },
            }
        }
        target = _date.fromisoformat(today_str)
        result = run_pipeline(
            raw_rows=rows,
            target_date=target,
            enrichment=enrichment_by_player,
            skip_health_gate=True,
            skip_settlement_check=True,
        )
        prop = result["prop_ledger"][0]
        sc = prop.get("gates", {}).get("source_ceiling")
        assert sc is not None, (
            f"source_ceiling gate should be present; "
            f"terminal_label={prop.get('terminal_label')}, "
            f"blockers={prop.get('blockers')}"
        )
        assert sc["reason"] == "prediction_market_only"
        # Terminal label must not be FINAL_APPROVED or MONEY_QUALIFIED
        lbl = prop.get("terminal_label")
        if lbl:
            assert lbl not in (PropLabel.FINAL_APPROVED.value, PropLabel.MONEY_QUALIFIED.value), \
                f"Prediction-market-only row should not reach {lbl}"


# ===========================================================================
# 8. Governance Registry
# ===========================================================================

class TestGovernanceRegistry:
    def test_governance_status_shape(self):
        status = get_governance_status()
        assert "governance_hash" in status
        assert "active_patch_ids" in status
        assert "master_spec_version" in status
        assert "engine_code_version" in status
        assert "loaded_at" in status
        assert "patch_count" in status
        assert status["can_execute"] is False

    def test_governance_hash_deterministic(self):
        h1 = compute_governance_hash()
        h2 = compute_governance_hash()
        assert h1 == h2

    def test_governance_hash_64_hex_chars(self):
        h = compute_governance_hash()
        assert len(h) == 64
        int(h, 16)  # raises if not valid hex

    def test_active_patch_contains_2026_07_15(self):
        status = get_governance_status()
        assert any(
            "2026-07-15" in pid
            for pid in status["active_patch_ids"]
        )


# ===========================================================================
# 9. Governance Handshake — validate_handshake()
# ===========================================================================

class TestGovernanceHandshake:
    def test_matching_hash_valid(self):
        status = get_governance_status()
        result = validate_handshake(
            expected_hash=status["governance_hash"],
            expected_patch_ids=status["active_patch_ids"],
            expected_master_spec_version=status["master_spec_version"],
        )
        assert result["valid"] is True
        assert result["code"] == "GOVERNANCE_MATCH"
        assert result["mismatches"] == []

    def test_wrong_hash_returns_mismatch(self):
        """GPT/Replit governance hashes differ → RUN_INVALID_GOVERNANCE_MISMATCH."""
        result = validate_handshake(expected_hash="deadbeef" * 8)
        assert result["valid"] is False
        assert result["code"] == "RUN_INVALID_GOVERNANCE_MISMATCH"
        assert result["can_execute"] is False
        assert len(result["mismatches"]) > 0

    def test_missing_patch_id_returns_mismatch(self):
        """If caller lists a patch ID the server doesn't have → mismatch."""
        result = validate_handshake(
            expected_hash=None,
            expected_patch_ids=["WOW-FAKE-PATCH-9999"],
        )
        assert result["valid"] is False
        assert result["code"] == "RUN_INVALID_GOVERNANCE_MISMATCH"

    def test_wrong_spec_version_returns_mismatch(self):
        result = validate_handshake(
            expected_hash=None,
            expected_master_spec_version="WOW-v99",
        )
        assert result["valid"] is False
        assert result["code"] == "RUN_INVALID_GOVERNANCE_MISMATCH"

    def test_none_inputs_pass_gracefully(self):
        """No governance fields supplied → passes (backward compat path)."""
        result = validate_handshake(
            expected_hash=None,
            expected_patch_ids=None,
            expected_master_spec_version=None,
        )
        assert result["valid"] is True

    def test_server_hash_always_in_result(self):
        result = validate_handshake(expected_hash="bad_hash")
        assert "server_hash" in result
        assert len(result["server_hash"]) == 64


# ===========================================================================
# 10. Pipeline output includes governance fields
# ===========================================================================

class TestPipelineGovernanceOutput:
    def test_pipeline_output_has_governance_hash(self):
        from gate_engine.pipeline import run_pipeline
        rows = [
            {
                "player":       "Test",
                "sport":        "WNBA",
                "prop_type":    "Points",
                "line":         20.0,
                "direction":    "MORE",
                "slate_date":   "2026-07-16",
                "board_source": "PrizePicks",
                "game":         "A vs B",
            }
        ]
        result = run_pipeline(
            raw_rows=rows,
            skip_health_gate=True,
            skip_settlement_check=True,
        )
        assert "governance_hash" in result
        assert "patch_ids_applied" in result
        assert "can_execute" in result
        assert result["can_execute"] is False
        assert len(result["governance_hash"]) == 64

    def test_pipeline_output_has_component_composite_report(self):
        from gate_engine.pipeline import run_pipeline
        rows = [
            {
                "player": "Iriafen", "sport": "WNBA",
                "prop_type": "P+R", "line": 18.5, "direction": "MORE",
                "slate_date": "2026-07-16", "board_source": "PrizePicks",
                "game": "A vs B",
            },
            {
                "player": "Iriafen", "sport": "WNBA",
                "prop_type": "Rebounds", "line": 9.5, "direction": "LESS",
                "slate_date": "2026-07-16", "board_source": "PrizePicks",
                "game": "A vs B",
            },
        ]
        result = run_pipeline(
            raw_rows=rows,
            skip_health_gate=True,
            skip_settlement_check=True,
        )
        assert "component_composite_report" in result
        report = result["component_composite_report"]
        assert report["conflicts_found"] > 0

    def test_pipeline_output_has_opportunity_state_report(self):
        from gate_engine.pipeline import run_pipeline
        rows = [
            {
                "player": f"P{i}", "sport": "WNBA",
                "prop_type": "Rebounds", "line": float(8 - i), "direction": "LESS",
                "slate_date": "2026-07-16", "board_source": "PrizePicks",
                "game": "A vs B",
            }
            for i in range(3)
        ]
        result = run_pipeline(
            raw_rows=rows,
            skip_health_gate=True,
            skip_settlement_check=True,
        )
        assert "opportunity_state_report" in result


# ===========================================================================
# 11. Hashes match, all gates pass → normal scoring continues
# ===========================================================================

class TestNormalScoringWhenHashMatches:
    def test_correct_hash_does_not_block_scoring(self):
        """When governance hash is correct, the pipeline produces results normally."""
        from gate_engine.pipeline import run_pipeline
        status = get_governance_status()
        rows = [
            {
                "player":       "NormalPlayer",
                "sport":        "NBA",
                "prop_type":    "Points",
                "line":         25.5,
                "direction":    "MORE",
                "slate_date":   "2026-07-16",
                "board_source": "PrizePicks",
                "game":         "LAL vs GSW",
            }
        ]
        # Validate handshake first (as GPT would)
        hs = validate_handshake(
            expected_hash=status["governance_hash"],
            expected_patch_ids=status["active_patch_ids"],
            expected_master_spec_version=status["master_spec_version"],
        )
        assert hs["valid"] is True, "Hash should match"

        # Pipeline should still produce results
        result = run_pipeline(
            raw_rows=rows,
            skip_health_gate=True,
            skip_settlement_check=True,
        )
        assert "prop_ledger" in result
        assert len(result["prop_ledger"]) == 1
        assert result["governance_hash"] == status["governance_hash"]
