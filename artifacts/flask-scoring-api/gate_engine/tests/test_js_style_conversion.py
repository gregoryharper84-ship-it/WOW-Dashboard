"""
test_js_style_conversion.py — Regression tests for WOW-PATCH-2026-07-07-JS-STYLE-CONVERSION-LAYER

8 required tests:
1. Discount-only Goblin line returns JS_WATCH_FAKE_DISCOUNT
2. WNBA same-game PRA MORE pair without full env support → JS_REJECT_SAME_GAME_PRA_CLUSTER
3. Pitcher outs MORE + same pitcher K LESS without contact-outs proof → JS_REJECT_MARKET_CONFLICT
4. Pitcher K LESS 4.0 without restrictions → JS_REJECT_LOW_K_LESS_TRAP
5. JS leg with only 0.5–1.0 cushion cannot enter Power/Flex → JS_CLOSE_NO_UPGRADE, slip_structure_allowed=True (advisory)
6. 2-pick Power with two JS_VALID independent anchors is allowed
7. 4–5 pick Flex with repeated exposure is blocked
8. Thin 0.5 win/loss logs JS_CLOSE_NO_UPGRADE and does not upgrade archetype
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from gate_engine import js_style_conversion as jsc


# ── Row factory ───────────────────────────────────────────────────────────────

def _row(
    player="Test Player", sport="WNBA", prop_type="Points",
    line=20.5, direction="MORE", game="IND @ CHI",
    l10_median=None, l10_avg=None,
    **kwargs,
):
    """Build a minimal normalised-style row for testing."""
    row = {
        "player": player, "sport": sport, "prop_type": prop_type,
        "line": line, "direction": direction, "game": game,
        "gates": {}, "blockers": [],
    }
    if l10_median is not None or l10_avg is not None:
        row["gates"]["l5_l10_ledger"] = {
            "l10_median": l10_median,
            "l10_avg": l10_avg,
        }
    row.update(kwargs)
    return row


# ── Test 1: Discount-only returns JS_WATCH_FAKE_DISCOUNT ─────────────────────

class TestDiscountOnlyFakeJS:
    def test_discount_goblin_only_returns_watch_fake_discount(self):
        """
        A row with ONLY discount_goblin_demon_line — no substance features — must land
        JS_WATCH_FAKE_DISCOUNT, not JS_VALID. Discount alone cannot qualify a leg.

        Fixture: WNBA PRA MORE with cushion exactly at the 2.5 hard floor (not below it)
        to avoid Gate D, and not large enough to auto-detect strong_cushion.
        The PRA MORE combo market suppresses one_stat_simplicity auto-detection.
        """
        row = _row(
            sport="WNBA", prop_type="Points+Rebounds+Assists", direction="MORE",
            line=18.5, l10_median=21.0,   # cushion=2.5 (at WNBA PRA floor, not below)
            game="IND @ CHI",
        )
        result = jsc.run(row, enrichment={"js_features": ["discount_goblin_demon_line"]})
        assert result["js_style_label"] == jsc.JS_WATCH_FAKE_DISCOUNT, (
            "discount_goblin_demon_line alone must not qualify as JS_VALID; "
            f"got label={result['js_style_label']} features={result['js_valid_features']}"
        )
        # All detected valid features must be in the discount/goblin-only bucket
        assert all(
            f in ("discount_goblin_demon_line", "clear_less_inflation")
            for f in result["js_valid_features"]
        )

    def test_discount_with_no_other_features_not_js_valid(self):
        # Same WNBA PRA MORE structure as above but different game — discount
        # is the only supplied feature; PRA combo suppresses one_stat_simplicity
        # auto-detect; cushion exactly at 2.5 floor avoids Gate D while keeping
        # the row off the advisory-range override (cushion 2.5 > 1.0).
        row = _row(
            sport="WNBA", prop_type="Points+Rebounds+Assists", direction="MORE",
            line=20.5, l10_median=23.0,  # cushion=2.5, at floor, not below
            game="NY @ ATL",
        )
        result = jsc.run(row, enrichment={"js_features": ["discount_goblin_demon_line"]})
        assert result["js_style_label"] != jsc.JS_VALID


# ── Test 2: WNBA same-game PRA MORE cluster rejection ────────────────────────

class TestWNBASameGamePRACluster:
    def test_two_pra_more_same_game_no_env_returns_cluster_reject(self):
        """
        Two WNBA PRA MORE rows in the same game WITHOUT full env support
        → BOTH must be labelled JS_REJECT_SAME_GAME_PRA_CLUSTER after run_slip().
        """
        row1 = _row(
            player="Player A", sport="WNBA",
            prop_type="Points+Rebounds+Assists", direction="MORE",
            line=22.5, l10_median=25.0, game="IND @ CHI",
            js_features=["low_threshold_relative_to_role", "one_stat_simplicity"],
        )
        row2 = _row(
            player="Player B", sport="WNBA",
            prop_type="Points+Rebounds+Assists", direction="MORE",
            line=18.5, l10_median=22.0, game="IND @ CHI",
            js_features=["low_threshold_relative_to_role", "one_stat_simplicity"],
        )

        enrichment = {
            "js_features": ["low_threshold_relative_to_role", "one_stat_simplicity"],
            # NO js_env_support → Gate A fires
        }
        jsc.run(row1, enrichment=enrichment)
        jsc.run(row2, enrichment=enrichment)
        jsc.run_slip([row1, row2])

        assert row1["js_style_label"] == jsc.JS_REJECT_SAME_GAME_PRA_CLUSTER, (
            "First PRA MORE row must be rejected when env support is incomplete"
        )
        assert row2["js_style_label"] == jsc.JS_REJECT_SAME_GAME_PRA_CLUSTER

    def test_single_pra_more_not_rejected_as_cluster(self):
        """A single WNBA PRA MORE row cannot trigger the pair-gate."""
        row = _row(
            player="Player A", sport="WNBA",
            prop_type="Points+Rebounds+Assists", direction="MORE",
            line=20.5, l10_median=24.0, game="IND @ CHI",
        )
        enr = {"js_features": [
            "low_threshold_relative_to_role", "participation_or_workload_floor"
        ]}
        jsc.run(row, enrichment=enr)
        jsc.run_slip([row])
        assert row["js_style_label"] != jsc.JS_REJECT_SAME_GAME_PRA_CLUSTER


# ── Test 3: Pitcher outs MORE + K LESS conflict ───────────────────────────────

class TestPitcherMarketConflict:
    def test_pitcher_conflict_without_proof_returns_market_conflict(self):
        """
        When enrichment signals same_pitcher_outs_more_and_k_less=True and
        no pitcher_conflict_proof is provided, Gate B must fire.
        """
        row = _row(
            sport="MLB", prop_type="Pitcher Outs", direction="MORE",
            line=16.5, l10_median=17.0, game="NYY @ BOS",
        )
        enrichment = {
            "js_features": ["low_threshold_relative_to_role", "participation_or_workload_floor"],
            "same_pitcher_outs_more_and_k_less": True,
            # no pitcher_conflict_proof
        }
        result = jsc.run(row, enrichment=enrichment)
        assert result["js_style_label"] == jsc.JS_REJECT_MARKET_CONFLICT
        assert result["slip_structure_allowed"] is False
        assert "pitcher_market_conflict" in result["js_trap_flags"]
        assert any("PITCHER_MARKET_CONFLICT" in b for b in result["blockers"])

    def test_pitcher_conflict_with_full_proof_not_rejected(self):
        row = _row(
            sport="MLB", prop_type="Pitcher Outs", direction="MORE",
            line=16.5, l10_median=17.5, game="NYY @ BOS",
        )
        enrichment = {
            "js_features": [
                "low_threshold_relative_to_role", "participation_or_workload_floor",
            ],
            "same_pitcher_outs_more_and_k_less": True,
            "pitcher_conflict_proof": {
                "low_whiff_profile": True,
                "low_k_projection": True,
                "low_opp_k_rate": True,
                "pitch_count_supports_outs": True,
                "market_not_implying_k_upside": True,
            },
        }
        result = jsc.run(row, enrichment=enrichment)
        assert result["js_style_label"] != jsc.JS_REJECT_MARKET_CONFLICT


# ── Test 4: Pitcher K LESS 4.0 without restrictions ─────────────────────────

class TestLowKLessTrap:
    def test_k_less_4_without_proof_returns_low_k_less_trap(self):
        """
        Pitcher K LESS at line ≤ 4.0 without k_less_proof → Gate C.
        Must block Power/Flex for this leg.
        """
        row = _row(
            sport="MLB", prop_type="Pitcher Ks", direction="LESS",
            line=4.0, l10_median=4.2, game="HOU @ TEX",
        )
        enrichment = {
            "js_features": ["low_threshold_relative_to_role", "participation_or_workload_floor"],
            # no k_less_proof
        }
        result = jsc.run(row, enrichment=enrichment)
        assert result["js_style_label"] == jsc.JS_REJECT_LOW_K_LESS_TRAP
        assert result["slip_structure_allowed"] is False
        assert any("LOW_K_LESS_TRAP" in b for b in result["blockers"])

    def test_k_less_above_4_not_gated(self):
        """K LESS at 4.5 (above the 4.0 trigger) does not fire Gate C."""
        row = _row(
            sport="MLB", prop_type="Pitcher Ks", direction="LESS",
            line=4.5, l10_median=3.8, game="HOU @ TEX",
        )
        result = jsc.run(row, enrichment={
            "js_features": [
                "low_threshold_relative_to_role", "participation_or_workload_floor",
            ]
        })
        assert result["js_style_label"] != jsc.JS_REJECT_LOW_K_LESS_TRAP

    def test_k_less_with_full_proof_not_gated(self):
        """K LESS 4.0 with all restriction proofs does not fire Gate C."""
        row = _row(
            sport="MLB", prop_type="Pitcher Ks", direction="LESS",
            line=3.5, l10_median=3.2, game="HOU @ TEX",
        )
        enrichment = {
            "js_features": [
                "low_threshold_relative_to_role", "minimal_shooting_efficiency_dependence",
            ],
            "k_less_proof": {
                "pitch_count_restriction": True,
                "low_whiff_profile": True,
                "low_opp_k_rate": True,
                "early_hook_risk": True,
            },
        }
        result = jsc.run(row, enrichment=enrichment)
        assert result["js_style_label"] != jsc.JS_REJECT_LOW_K_LESS_TRAP


# ── Test 5: 0.5–1.0 cushion leg logs JS_CLOSE_NO_UPGRADE, not hard-rejected ─

class TestThinCushionAdvisory:
    def test_thin_cushion_logs_close_no_upgrade(self):
        """
        A leg with cushion in [0.5, 1.0] should be JS_CLOSE_NO_UPGRADE —
        advisory, not a hard reject. slip_structure_allowed stays True
        (the leg can enter a slip, it just cannot upgrade the archetype).
        """
        row = _row(
            sport="NBA", prop_type="Points", direction="MORE",
            line=25.5, l10_median=26.2,  # cushion = 0.7 → thin
        )
        enrichment = {
            "js_features": [
                "low_threshold_relative_to_role", "participation_or_workload_floor",
            ]
        }
        result = jsc.run(row, enrichment=enrichment)
        assert result["js_style_label"] == jsc.JS_CLOSE_NO_UPGRADE
        assert result["js_close_no_upgrade"] is True
        assert result["projected_cushion"] == pytest.approx(0.7, abs=0.01)
        # Advisory only — does NOT make slip_structure_allowed False
        assert result["slip_structure_allowed"] is True

    def test_thin_cushion_does_not_upgrade_archetype(self):
        """JS_CLOSE_NO_UPGRADE must not map to JS_VALID."""
        row = _row(
            sport="NBA", prop_type="Assists", direction="MORE",
            line=8.5, l10_median=9.0,  # cushion = 0.5
        )
        result = jsc.run(row, enrichment={
            "js_features": [
                "one_stat_simplicity", "participation_or_workload_floor",
            ]
        })
        assert result["js_style_label"] != jsc.JS_VALID


# ── Test 6: 2-pick Power with two JS_VALID independent anchors ───────────────

class TestValidTwoPickPower:
    def test_two_js_valid_independent_legs_allow_power(self):
        """
        Two JS_VALID rows from different games/players should allow 2-pick Power.
        slip_rule["two_pick_power_allowed"] must be True for both rows.
        """
        row1 = _row(
            player="Alice", sport="WNBA", prop_type="Points",
            line=18.5, l10_median=22.0, game="IND @ CHI", direction="MORE",
        )
        row2 = _row(
            player="Bob", sport="MLB", prop_type="Hits",
            line=1.5, l10_median=3.5, game="NYY @ BOS", direction="MORE",
            # cushion=2.0 (above advisory range 0.5–1.0) → JS_VALID not JS_CLOSE_NO_UPGRADE
        )
        enrichment = {
            "js_features": [
                "low_threshold_relative_to_role", "participation_or_workload_floor",
            ]
        }
        jsc.run(row1, enrichment=enrichment)
        jsc.run(row2, enrichment=enrichment)
        jsc.run_slip([row1, row2])

        for row in [row1, row2]:
            slip_rule = row["gates"]["js_style"].get("slip_rule", {})
            assert slip_rule.get("two_pick_power_allowed") is True, (
                f"{row['player']}: two_pick_power_allowed must be True"
            )

    def test_js_valid_label_set(self):
        """All gates passing correctly labels the row JS_VALID."""
        row = _row(
            sport="WNBA", prop_type="Points", line=18.5,
            l10_median=22.5, direction="MORE", game="IND @ CHI",
        )
        enrichment = {
            "js_features": [
                "low_threshold_relative_to_role", "participation_or_workload_floor",
            ]
        }
        result = jsc.run(row, enrichment=enrichment)
        assert result["js_style_label"] == jsc.JS_VALID
        assert result["slip_structure_allowed"] is True


# ── Test 7: 4–5 pick Flex with repeated exposure is blocked ──────────────────

class TestFlexDuplicateExposureBlocked:
    def test_repeated_exposure_blocks_4_5_flex(self):
        """
        Two rows with the same player:market:side key → duplicate_exposure_count > 0
        → four_five_flex_allowed must be False.
        """
        row1 = _row(
            player="Caitlin Clark", sport="WNBA", prop_type="Points",
            line=18.5, l10_median=23.0, direction="MORE", game="IND @ CHI",
        )
        row2 = _row(
            player="Caitlin Clark", sport="WNBA", prop_type="Points",
            line=19.5, l10_median=23.0, direction="MORE", game="IND @ CHI",
        )
        enrichment = {
            "js_features": [
                "low_threshold_relative_to_role", "participation_or_workload_floor",
            ]
        }
        jsc.run(row1, enrichment=enrichment)
        jsc.run(row2, enrichment=enrichment)
        jsc.run_slip([row1, row2])

        for row in [row1, row2]:
            slip_rule = row["gates"]["js_style"].get("slip_rule", {})
            assert slip_rule.get("four_five_flex_allowed") is False, (
                "Repeated same player:market:side must block 4-5 pick Flex"
            )
            assert row["duplicate_exposure_count"] > 0

    def test_anchor_of_slate_not_duplicate_blocked(self):
        """A row marked js_anchor_of_slate=True is exempt from the dup gate."""
        row1 = _row(
            player="Caitlin Clark", sport="WNBA", prop_type="Points",
            line=18.5, l10_median=23.0, direction="MORE", game="IND @ CHI",
            js_anchor_of_slate=True,
        )
        row2 = _row(
            player="Caitlin Clark", sport="WNBA", prop_type="Points",
            line=19.5, l10_median=23.0, direction="MORE", game="IND @ CHI",
            js_anchor_of_slate=True,
        )
        enrichment = {"js_features": [
            "low_threshold_relative_to_role", "participation_or_workload_floor",
        ]}
        jsc.run(row1, enrichment=enrichment)
        jsc.run(row2, enrichment=enrichment)
        jsc.run_slip([row1, row2])

        for row in [row1, row2]:
            slip_rule = row["gates"]["js_style"].get("slip_rule", {})
            # duplicate_exposure_blocked should be False when anchor flag set
            assert slip_rule.get("duplicate_exposure_blocked") is False


# ── Test 8: Thin 0.5 win/loss logs JS_CLOSE_NO_UPGRADE, no archetype upgrade ─

class TestCloseNoUpgrade:
    def test_close_no_upgrade_recorded_at_exact_0_5_cushion(self):
        """
        Cushion exactly 0.5 (the lower bound of the thin-cushion range)
        → JS_CLOSE_NO_UPGRADE, not JS_VALID, not JS_REJECT_THIN_CUSHION.
        """
        row = _row(
            sport="MLB", prop_type="Hits", direction="MORE",
            line=2.5, l10_median=3.0,  # cushion = 0.5 exactly
            game="CHC @ STL",
        )
        enrichment = {"js_features": [
            "low_threshold_relative_to_role", "one_stat_simplicity",
        ]}
        result = jsc.run(row, enrichment=enrichment)
        assert result["js_style_label"] == jsc.JS_CLOSE_NO_UPGRADE
        assert result["js_close_no_upgrade"] is True
        assert result["projected_cushion"] == pytest.approx(0.5, abs=0.01)
        # The row is not a hard-reject
        assert result["slip_structure_allowed"] is True

    def test_cushion_above_1_0_not_close_no_upgrade(self):
        """Cushion > 1.0 does not trigger JS_CLOSE_NO_UPGRADE."""
        row = _row(
            sport="MLB", prop_type="Hits", direction="MORE",
            line=1.5, l10_median=3.0,  # cushion = 1.5
            game="CHC @ STL",
        )
        enrichment = {"js_features": [
            "low_threshold_relative_to_role", "one_stat_simplicity",
        ]}
        result = jsc.run(row, enrichment=enrichment)
        assert result["js_style_label"] != jsc.JS_CLOSE_NO_UPGRADE


import pytest
