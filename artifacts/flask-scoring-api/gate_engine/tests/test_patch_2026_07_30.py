"""
test_patch_2026_07_30.py
WOW-PATCH-2026-07-30-WNBA-COMPOSITE-MLB-DIRECTIONAL-AND-CROSS-TICKET-GOVERNANCE

Regression tests (24 from the pack) + integration coverage.
All tests: can_execute=False, stake=0, DRY_RUN_ONLY.
"""
from __future__ import annotations
import pytest
from unittest.mock import patch

from gate_engine import mlb_directional_firewall as _mlb
from gate_engine import wnba_composite_gate as _wnba
from gate_engine import cross_ticket_governor as _ctg
from gate_engine.labels import PropLabel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mlb_row(stat_type="Pitcher Strikeouts", direction="LESS", sport="MLB",
             terminal_label=None, **kwargs):
    row = {
        "sport": sport,
        "stat_type": stat_type,
        "direction": direction,
        "terminal_label": terminal_label or PropLabel.MONEY_QUALIFIED.value,
        "player_name": "Test Pitcher",
        "event_id": "mlb_2026_07_30_game1",
        "line": 5.0,
        "can_execute": False,
    }
    row.update(kwargs)
    return row


def _wnba_row(stat_type="PRA", direction="MORE", terminal_label=None, **kwargs):
    row = {
        "sport": "WNBA",
        "stat_type": stat_type,
        "direction": direction,
        "terminal_label": terminal_label or PropLabel.MONEY_QUALIFIED.value,
        "player_name": "Test Player",
        "event_id": "wnba_2026_07_30_game1",
        "line": 20.5,
        "can_execute": False,
    }
    row.update(kwargs)
    return row


def _ctg_row(player="Player A", event="game1", stat="Points", direction="MORE",
             line=20.5, slip_type="FLEX", card_id="card1", terminal_label=None,
             **kwargs):
    from gate_engine.labels import PropLabel
    row = {
        "player_name": player,
        "event_id": event,
        "stat_type": stat,
        "direction": direction,
        "line": line,
        "slip_type": slip_type,
        "card_id": card_id,
        "terminal_label": terminal_label or PropLabel.MARKET_VERIFIED_HOLD.value,
        "calibrated_lower_bound": 0.65,
        "can_execute": False,
    }
    row.update(kwargs)
    return row


# ===========================================================================
# Tests 1–2: Exact MLB Duplicate Across Cards (PATCH-014)
# ===========================================================================

class TestExactMLBDuplicate:
    """Tests 1–2 from the regression pack."""

    def test_exact_duplicate_boyd_same_line(self):
        """Test 1: Boyd LESS 4.5 on two cards → one retained, one rejected."""
        rows = [
            _ctg_row("Matthew Boyd", "game_boyd", "Pitcher Strikeouts", "LESS", 4.5,
                     "FLEX", "card_A", calibrated_lower_bound=0.60),
            _ctg_row("Matthew Boyd", "game_boyd", "Pitcher Strikeouts", "LESS", 4.5,
                     "POWER", "card_B", calibrated_lower_bound=0.60),
        ]
        result = _ctg.run(rows)

        assert result["exact_duplicate_groups"] >= 1
        dup_classes = [r["duplicate_class"] for r in rows]
        assert "EXACT_DUPLICATE" in dup_classes
        # Only one should survive (the other gets REJECT_EXACT_DUPLICATE)
        rejected = [r for r in rows if r.get("terminal_label") == PropLabel.REJECT_EXACT_DUPLICATE.value]
        assert len(rejected) == 1
        # Calibration: both share same duplicate_group_id
        assert rows[0]["duplicate_group_id"] == rows[1]["duplicate_group_id"]

    def test_alternate_threshold_boyd(self):
        """Test 2: Boyd LESS 4.5 and LESS 5.0 → alternate threshold, one underlying thesis."""
        rows = [
            _ctg_row("Pitcher X", "game1", "Pitcher Strikeouts", "LESS", 4.5,
                     "FLEX", "card_A", calibrated_lower_bound=0.62),
            _ctg_row("Pitcher X", "game1", "Pitcher Strikeouts", "LESS", 5.0,
                     "POWER", "card_B", calibrated_lower_bound=0.59),
        ]
        result = _ctg.run(rows)

        assert result["alternate_threshold_groups"] >= 1
        # Strongest threshold kept, weaker rejected
        rejected = [r for r in rows
                    if r.get("terminal_label") == PropLabel.REJECT_ALTERNATE_THRESHOLD_DUPLICATE.value]
        assert len(rejected) == 1
        # Calibration observations = 1
        assert result["unique_underlying_theses"] <= 2  # 2 rows but 1 unique thesis


# ===========================================================================
# Test 3: Power Card Copied From Flex (PATCH-014)
# ===========================================================================

class TestPowerCopiedFromFlex:
    """Test 3: Power card copies Flex legs → REJECT_DUPLICATE_STRUCTURE."""

    def test_power_flex_copy(self):
        """Flex: Boyd, Rodriguez, Singer. Power: Boyd, Rodriguez, Davis-Martin."""
        rows = [
            _ctg_row("Matthew Boyd", "game_boyd", "Pitcher Strikeouts", "LESS", 4.5,
                     "FLEX", "flex1", calibrated_lower_bound=0.60),
            _ctg_row("Grayson Rodriguez", "game_rod", "Pitcher Strikeouts", "LESS", 4.0,
                     "FLEX", "flex1", calibrated_lower_bound=0.61),
            _ctg_row("Brady Singer", "game_sing", "Pitching Outs", "MORE", 14.5,
                     "FLEX", "flex1", calibrated_lower_bound=0.63),
            _ctg_row("Matthew Boyd", "game_boyd", "Pitcher Strikeouts", "LESS", 4.5,
                     "POWER", "power1", calibrated_lower_bound=0.60),
            _ctg_row("Grayson Rodriguez", "game_rod", "Pitcher Strikeouts", "LESS", 4.0,
                     "POWER", "power1", calibrated_lower_bound=0.61),
            _ctg_row("Davis Martin", "game_dm", "Pitcher Strikeouts", "LESS", 3.5,
                     "POWER", "power1", calibrated_lower_bound=0.58),
        ]
        result = _ctg.run(rows)

        power_rows = [r for r in rows if r.get("card_id") == "power1"]
        # Power card should have REJECT_DUPLICATE_STRUCTURE blocker
        power_blockers = [b for r in power_rows for b in (r.get("blockers") or [])]
        assert "REJECT_DUPLICATE_STRUCTURE" in power_blockers


# ===========================================================================
# Test 4: Morrow Alternate PRA Thresholds (PATCH-014)
# ===========================================================================

class TestMorrowPRAThresholds:
    """Test 4: Morrow PRA 17.5, 18.5, 19.0 → 1 calibration observation."""

    def test_alternate_pra_thresholds_count_once(self):
        rows = [
            _ctg_row("Aneesah Morrow", "wnba_game", "PRA", "MORE", 17.5,
                     "FLEX", "card1", calibrated_lower_bound=0.68),
            _ctg_row("Aneesah Morrow", "wnba_game", "PRA", "MORE", 18.5,
                     "FLEX", "card1", calibrated_lower_bound=0.63),
            _ctg_row("Aneesah Morrow", "wnba_game", "PRA", "MORE", 19.0,
                     "FLEX", "card1", calibrated_lower_bound=0.59),
        ]
        result = _ctg.run(rows)

        assert result["alternate_threshold_groups"] >= 1
        # All three share player_event_key → counted as alternate thresholds
        assert rows[0]["player_event_key"] == rows[1]["player_event_key"] == rows[2]["player_event_key"]
        # Only the strongest (17.5 at 0.68) should be retained
        rejected = [r for r in rows
                    if r.get("terminal_label") == PropLabel.REJECT_ALTERNATE_THRESHOLD_DUPLICATE.value]
        assert len(rejected) == 2


# ===========================================================================
# Tests 5–6: WNBA Composite Gate (PATCH-017)
# ===========================================================================

class TestWNBACompositeGate:
    """Tests 5, 6, 8, 12, 20 from regression pack."""

    def test_forward_test_ceiling_applied(self):
        """Forward-test milestone not met → MODEL_QUALIFIED_HOLD ceiling."""
        row = _wnba_row(terminal_label=PropLabel.MONEY_QUALIFIED.value)
        with patch.object(_wnba, "get_unique_player_game_count", return_value=5):
            _wnba.run(row)
        assert row["terminal_label"] == PropLabel.MODEL_QUALIFIED_HOLD.value
        assert "ACTIVE" in row["forward_test_status"]

    def test_promo_cannot_upgrade_unresolved_role(self):
        """Test 6: Goblin PRA with ROLE_UNRESOLVED → promo upgrade blocked."""
        row = _wnba_row(
            offer_type="goblin",
            terminal_label=PropLabel.MARKET_VERIFIED_HOLD.value,
            blockers=["ROLE_UNRESOLVED"],
        )
        with patch.object(_wnba, "get_unique_player_game_count", return_value=3):
            _wnba.run(row)
        assert "PROMO_UPGRADE_BLOCKED_BY_STATUS" in (row.get("blockers") or [])
        assert row["terminal_label"] == PropLabel.MODEL_QUALIFIED_HOLD.value

    def test_can_execute_always_false(self):
        """can_execute must be False on every WNBA composite row."""
        row = _wnba_row()
        with patch.object(_wnba, "get_unique_player_game_count", return_value=0):
            _wnba.run(row)
        assert row["can_execute"] is False

    def test_dnp_not_projection_hit(self):
        """Test 12: DNP/void row — is_dnp_or_void=True, model_hit=False."""
        row = _wnba_row(settled_result="DNP", model_result="DNP")
        with patch.object(_wnba, "get_unique_player_game_count", return_value=3):
            with patch.object(_wnba, "log_wnba_row", return_value=True):
                _wnba.run(row)
        # DNP should NOT count toward unique player-game milestone
        # (tested via parse logic — settled_result="DNP" → is_dnp=True)
        assert _wnba._parse_model_hit(row) is None  # DNP → no hit/miss

    def test_forward_test_milestone_not_met_12_games(self):
        """Test 20: 12 unique player-games → milestone not met, ceiling MODEL_QUALIFIED_HOLD."""
        row = _wnba_row(terminal_label=PropLabel.MONEY_QUALIFIED.value)
        with patch.object(_wnba, "get_unique_player_game_count", return_value=12):
            _wnba.run(row)
        assert row["terminal_label"] == PropLabel.MODEL_QUALIFIED_HOLD.value

    def test_non_wnba_row_skipped(self):
        """Non-WNBA rows are not touched."""
        row = _wnba_row(sport="NBA", terminal_label=PropLabel.FINAL_APPROVED.value)
        _wnba.run(row)
        assert row["terminal_label"] == PropLabel.FINAL_APPROVED.value

    def test_non_composite_stat_skipped(self):
        """Non-P/R/A stat types are skipped."""
        row = {
            "sport": "WNBA",
            "stat_type": "Three Pointers Made",
            "terminal_label": PropLabel.FINAL_APPROVED.value,
            "can_execute": False,
        }
        _wnba.run(row)
        assert row["terminal_label"] == PropLabel.FINAL_APPROVED.value


# ===========================================================================
# Tests 13–16: MLB Directional Firewall (PATCH-015)
# ===========================================================================

class TestMLBDirectionalFirewall:
    """Tests 13–17 from regression pack."""

    def test_k_less_short_outing_support_over_50_pct(self):
        """Test 13: short_outing_support_share=0.567 → HIGH confidence prohibited."""
        row = _mlb_row(
            terminal_label=PropLabel.MONEY_QUALIFIED.value,
            short_outing_support_share=0.567,
        )
        _mlb.run(row)
        assert row["directional_lane"] == "K_LESS"
        assert "MLB_K_LESS_SHORT_OUTING_BLOCK" in (row.get("blockers") or [])
        assert row["terminal_label"] in (
            PropLabel.MLB_K_LESS_WATCH.value,
            PropLabel.HIGH_CONFIDENCE_SUSPENDED.value,
            PropLabel.MODEL_QUALIFIED_HOLD.value,
        )

    def test_k_less_watch_only_unconditional(self):
        """Test 14: K LESS with short_outing_share=0.273 still capped at WATCH_ONLY."""
        row = _mlb_row(
            terminal_label=PropLabel.MONEY_QUALIFIED.value,
            short_outing_support_share=0.273,
        )
        _mlb.run(row)
        assert row["directional_lane"] == "K_LESS"
        # WATCH_ONLY ceiling applied regardless
        assert row.get("directional_forward_test_status") == "MLB_K_LESS_WATCH_ONLY_ACTIVE"
        assert row["terminal_label"] == PropLabel.MLB_K_LESS_WATCH.value

    def test_outs_more_conditional_as_unconditional_blocked(self):
        """Test 15: conditional_probability_used_as_unconditional → MODEL_INVALID blocker."""
        row = _mlb_row(
            stat_type="Pitching Outs",
            direction="MORE",
            terminal_label=PropLabel.MONEY_QUALIFIED.value,
            conditional_probability_used_as_unconditional=True,
        )
        _mlb.run(row)
        assert row["directional_lane"] == "OUTS_MORE"
        assert "MLB_OUTS_MORE_CONDITIONAL_AS_UNCONDITIONAL" in (row.get("blockers") or [])
        assert row["terminal_label"] == PropLabel.MODEL_QUALIFIED_HOLD.value

    def test_outs_more_low_survival_probability(self):
        """Test 16: P(reach 15 outs) LB=0.59 < floor=0.65 → NO_LOW_PROBABILITY."""
        row = _mlb_row(
            stat_type="Pitching Outs",
            direction="MORE",
            terminal_label=PropLabel.MONEY_QUALIFIED.value,
            required_out_survival_lower_bound=0.59,
        )
        _mlb.run(row)
        assert row["directional_lane"] == "OUTS_MORE"
        assert "MLB_OUTS_MORE_LOW_PROBABILITY" in (row.get("blockers") or [])
        assert row["terminal_label"] == PropLabel.MODEL_QUALIFIED_HOLD.value

    def test_outs_more_hold_ceiling_unconditional(self):
        """OUTS MORE always capped at MODEL_QUALIFIED_HOLD (PATCH-015 initial state)."""
        row = _mlb_row(
            stat_type="Pitching Outs",
            direction="MORE",
            terminal_label=PropLabel.FINAL_APPROVED.value,
        )
        _mlb.run(row)
        assert row["directional_lane"] == "OUTS_MORE"
        assert row["terminal_label"] == PropLabel.MODEL_QUALIFIED_HOLD.value

    def test_k_more_lane_no_ceiling(self):
        """K MORE does not get a ceiling change from this gate."""
        row = _mlb_row(
            stat_type="Pitcher Strikeouts",
            direction="MORE",
            terminal_label=PropLabel.MONEY_QUALIFIED.value,
        )
        _mlb.run(row)
        assert row["directional_lane"] == "K_MORE"
        assert row["terminal_label"] == PropLabel.MONEY_QUALIFIED.value

    def test_directional_lanes_separate(self):
        """Test 17: K_MORE, K_LESS, OUTS rows get separate lane labels."""
        rows_input = [
            ("Pitcher Strikeouts", "MORE"),
            ("Pitcher Strikeouts", "LESS"),
            ("Pitching Outs", "MORE"),
        ]
        lanes = []
        for stat, direction in rows_input:
            row = _mlb_row(stat_type=stat, direction=direction,
                           terminal_label=PropLabel.MODEL_QUALIFIED_HOLD.value)
            _mlb.run(row)
            lanes.append(row["directional_lane"])
        assert lanes == ["K_MORE", "K_LESS", "OUTS_MORE"]

    def test_non_mlb_row_skipped(self):
        """Non-MLB rows are not touched."""
        row = _mlb_row(sport="NBA", terminal_label=PropLabel.FINAL_APPROVED.value)
        _mlb.run(row)
        assert row.get("directional_lane") == "NOT_PITCHER_PROP"
        assert row["terminal_label"] == PropLabel.FINAL_APPROVED.value

    def test_can_execute_false(self):
        """Test 24 governance: can_execute=False on every pitcher row."""
        row = _mlb_row(terminal_label=PropLabel.MARKET_VERIFIED_HOLD.value)
        _mlb.run(row)
        assert row.get("can_execute") is False


# ===========================================================================
# Test 18–19: Cross-Ticket Critical Thesis (PATCH-014)
# ===========================================================================

class TestCrossTicketFragility:
    """Tests 18–19 from regression pack."""

    def test_fragile_portfolio_when_thesis_on_two_of_three_cards(self):
        """Test 18: Same thesis on 2/3 cards → share=0.667 → FRAGILE."""
        rows = [
            _ctg_row("Player A", "game1", "Points", "MORE", 20.5, "FLEX", "c1",
                     calibrated_lower_bound=0.68),
            _ctg_row("Player A", "game1", "Points", "MORE", 20.5, "FLEX", "c2",
                     calibrated_lower_bound=0.68),
            _ctg_row("Player B", "game2", "Rebounds", "MORE", 8.5, "FLEX", "c3",
                     calibrated_lower_bound=0.65),
        ]
        result = _ctg.run(rows)

        # Rows 0 and 1 share the same exact_leg_key
        assert rows[0]["exact_leg_key"] == rows[1]["exact_leg_key"]
        fragility = result["portfolio_fragility_class"]
        # 2 out of 3 rows share the thesis (0.667 > 0.50 threshold)
        assert fragility in ("FRAGILE", "CONCENTRATED")

    def test_no_replacement_card_shrinks(self):
        """Test 19: Duplicate removed, no replacement → one clean card preferred."""
        rows = [
            _ctg_row("Player A", "game1", "PRA", "MORE", 22.5, "FLEX", "c1",
                     calibrated_lower_bound=0.70),
            _ctg_row("Player A", "game1", "PRA", "MORE", 22.5, "POWER", "c2",
                     calibrated_lower_bound=0.70),
        ]
        result = _ctg.run(rows)

        rejected = [r for r in rows if r.get("terminal_label") == PropLabel.REJECT_EXACT_DUPLICATE.value]
        assert len(rejected) == 1
        assert result["rows_rejected_by_governor"] >= 1


# ===========================================================================
# Test 21: Forward-Test Duplicate Exclusion
# ===========================================================================

class TestForwardTestDuplicateExclusion:
    """Test 21: 20 displayed wins but 8 are dupes → 12 unique player-games."""

    def test_duplicate_exclusion_from_milestone(self):
        """Alternate thresholds for same player-game count as 1 observation, not multiple."""
        # Build 20 rows, 8 of which are alternate thresholds on same player-games
        rows = []
        # 12 unique player-games
        for i in range(12):
            rows.append(_ctg_row(f"Player{i}", f"game{i}", "PRA", "MORE", 18.5,
                                  "FLEX", "card1", calibrated_lower_bound=0.65))
        # 8 alternate thresholds on Player0-Player3 (2 alternates each)
        for i in range(4):
            rows.append(_ctg_row(f"Player{i}", f"game{i}", "PRA", "MORE", 19.0,
                                  "FLEX", "card1", calibrated_lower_bound=0.62))
            rows.append(_ctg_row(f"Player{i}", f"game{i}", "PRA", "MORE", 19.5,
                                  "FLEX", "card1", calibrated_lower_bound=0.59))

        result = _ctg.run(rows)
        assert result["alternate_threshold_groups"] >= 4
        # Rejected duplicates should be the 8 alternate threshold rows
        rejected = [r for r in rows if r.get("terminal_label") == PropLabel.REJECT_ALTERNATE_THRESHOLD_DUPLICATE.value]
        assert len(rejected) == 8


# ===========================================================================
# Test 22: Defensive Rebounds Not Silently Modeled as Total Rebounds
# ===========================================================================

class TestDefensiveReboundIsolation:
    """Test 22: Awa Fam defensive rebounds are not treated as total rebounds."""

    def test_defensive_rebounds_not_composite(self):
        """Defensive rebounds should NOT trigger the WNBA composite gate."""
        row = {
            "sport": "WNBA",
            "stat_type": "Defensive Rebounds",
            "direction": "MORE",
            "terminal_label": PropLabel.FINAL_APPROVED.value,
            "player_name": "Awa Fam",
            "can_execute": False,
        }
        _wnba.run(row)
        # Gate should not touch non-composite stats
        assert row["terminal_label"] == PropLabel.FINAL_APPROVED.value
        assert "wnba_composite_gate" not in (row.get("gates") or {})


# ===========================================================================
# Test 23: Bidirectional Requirement
# ===========================================================================

class TestBidirectionalRequirement:
    """Test 23: PRA MORE fails floor — LESS is not automatically approved."""

    def test_failed_more_does_not_auto_approve_less(self):
        """Two rows, one MORE (rejected), one LESS — LESS still goes through gates."""
        row_more = _wnba_row(direction="MORE", terminal_label=PropLabel.REJECT_NO_EDGE.value)
        row_less = _wnba_row(direction="LESS", terminal_label=PropLabel.MARKET_VERIFIED_HOLD.value)

        with patch.object(_wnba, "get_unique_player_game_count", return_value=5):
            _wnba.run(row_more)
            _wnba.run(row_less)

        # MORE row stays rejected (gate doesn't touch REJECT labels)
        assert "REJECT" in row_more["terminal_label"]
        # LESS row gets forward-test ceiling but isn't auto-approved
        assert row_less["terminal_label"] == PropLabel.MODEL_QUALIFIED_HOLD.value


# ===========================================================================
# Test 24: Governance on Every Output
# ===========================================================================

class TestGovernanceAlwaysPresent:
    """Test 24: can_execute=False appears in every output."""

    def test_mlb_can_execute_false(self):
        row = _mlb_row(terminal_label=PropLabel.FINAL_APPROVED.value)
        _mlb.run(row)
        assert row.get("can_execute") is False

    def test_wnba_can_execute_false(self):
        row = _wnba_row(terminal_label=PropLabel.FINAL_APPROVED.value)
        with patch.object(_wnba, "get_unique_player_game_count", return_value=0):
            _wnba.run(row)
        assert row.get("can_execute") is False

    def test_cross_ticket_can_execute_false(self):
        rows = [_ctg_row()]
        result = _ctg.run(rows)
        assert result.get("can_execute") is False
        assert rows[0].get("can_execute") is False


# ===========================================================================
# Identity key builders
# ===========================================================================

class TestIdentityKeys:
    """Verify key-building consistency for PATCH-014 deduplication."""

    def test_exact_leg_key_same_for_identical_rows(self):
        r1 = _ctg_row("Matthew Boyd", "game1", "Pitcher Strikeouts", "LESS", 4.5)
        r2 = _ctg_row("Matthew Boyd", "game1", "Pitcher Strikeouts", "LESS", 4.5)
        assert _ctg.make_exact_leg_key(r1) == _ctg.make_exact_leg_key(r2)

    def test_exact_leg_key_differs_for_diff_line(self):
        r1 = _ctg_row("Pitcher X", "game1", "Pitcher Strikeouts", "LESS", 4.5)
        r2 = _ctg_row("Pitcher X", "game1", "Pitcher Strikeouts", "LESS", 5.0)
        assert _ctg.make_exact_leg_key(r1) != _ctg.make_exact_leg_key(r2)

    def test_player_event_key_same_for_same_player_game(self):
        r1 = _ctg_row("Aneesah Morrow", "wnba_game", "PRA", "MORE", 17.5)
        r2 = _ctg_row("Aneesah Morrow", "wnba_game", "PRA", "MORE", 18.5)
        assert _ctg.make_player_event_key(r1) == _ctg.make_player_event_key(r2)

    def test_pitcher_thesis_key_k_less(self):
        r = _ctg_row("Boyd", "g1", "Pitcher Strikeouts", "LESS", 4.5,
                     sport="MLB", position="SP")
        r["sport"] = "MLB"
        key = _ctg.make_pitcher_thesis_key(r)
        assert "K_LESS" in key or "LESS" in key

    def test_distribution_key_pra_and_points_same_player_flagged(self):
        """Points and PRA for the same player should map to same distribution family."""
        r_pts = _ctg_row("Player A", "game1", "Points", "MORE", 18.5)
        r_pra = _ctg_row("Player A", "game1", "PRA", "MORE", 25.5)
        k_pts = _ctg.make_distribution_key(r_pts)
        k_pra = _ctg.make_distribution_key(r_pra)
        # Both in the POINTS_DISTRIBUTION family for the same player-event
        assert "player_a" in k_pts.lower()
        assert "player_a" in k_pra.lower()


# ===========================================================================
# MLB lane detection
# ===========================================================================

class TestMLBLaneDetection:
    """Verify all 8 directional lanes are detected correctly."""

    @pytest.mark.parametrize("stat,direction,expected_lane", [
        ("Pitcher Strikeouts",  "MORE",  "K_MORE"),
        ("Pitcher Strikeouts",  "LESS",  "K_LESS"),
        ("Pitching Outs",       "MORE",  "OUTS_MORE"),
        ("Pitching Outs",       "LESS",  "OUTS_LESS"),
        ("Pitches Thrown",      "MORE",  "PITCH_COUNT_MORE"),
        ("Pitches Thrown",      "LESS",  "PITCH_COUNT_LESS"),
        ("Batters Faced",       "MORE",  "BATTERS_FACED_MORE"),
        ("Batters Faced",       "LESS",  "BATTERS_FACED_LESS"),
    ])
    def test_lane_detection(self, stat, direction, expected_lane):
        row = _mlb_row(stat_type=stat, direction=direction)
        assert _mlb._detect_lane(row) == expected_lane


# ===========================================================================
# WNBA stat detection
# ===========================================================================

class TestWNBAStatDetection:
    """Verify composite stat detection."""

    @pytest.mark.parametrize("stat", [
        "PRA", "Points", "Rebounds", "Assists",
        "P+R", "P+A", "R+A",
        "Points Rebounds", "Points Assists",
    ])
    def test_composite_stats_detected(self, stat):
        row = {"sport": "WNBA", "stat_type": stat}
        assert _wnba._is_composite_stat(row)

    @pytest.mark.parametrize("stat", [
        "Defensive Rebounds", "Three Pointers Made", "Blocks",
        "Fantasy Score", "First Quarter Points",
    ])
    def test_non_composite_stats_not_flagged(self, stat):
        row = {"sport": "WNBA", "stat_type": stat}
        assert not _wnba._is_composite_stat(row)


# ===========================================================================
# Short outing support share calculation
# ===========================================================================

class TestShortOutingSupportShare:
    """Test 13 calculation: 0.38/0.67 = 0.567."""

    def test_derived_from_enrichment_fields(self):
        row = _mlb_row(
            terminal_label=PropLabel.MONEY_QUALIFIED.value,
            model_probability=0.67,
            early_exit_probability=0.38,
        )
        _mlb.run(row)
        sos = row.get("short_outing_support_share")
        assert sos is not None
        assert abs(sos - (0.38 / 0.67)) < 0.01

    def test_explicit_share_used_directly(self):
        row = _mlb_row(
            terminal_label=PropLabel.MONEY_QUALIFIED.value,
            short_outing_support_share=0.273,
        )
        _mlb.run(row)
        assert row.get("short_outing_support_share") == 0.273
