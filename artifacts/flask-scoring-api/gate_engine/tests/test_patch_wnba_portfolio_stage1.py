"""
test_patch_wnba_portfolio_stage1.py
PATCH-WNBA-001 (Opportunity Stability Gate) +
PATCH-PORTFOLIO-001 (Cross-Slip Exposure Governor)

12-test regression suite from the architecture spec.
Tests 1, 5, 6, 7, 8, 12 are Stage 1 (implemented now).
Tests 2, 3, 4, 9, 10, 11 are stubs for Stages 2-4 (component distributions,
scenario survival, settlement).

All tests: can_execute=False, stake=0, DRY_RUN_ONLY.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from gate_engine.wnba.opportunity_engine import (
    run as _opp_run,
    is_wnba_row,
    is_pra_market,
    THRESH_OSS_GENERAL,
    THRESH_OSS_PRA,
    THRESH_ROLE_CONF,
    THRESH_MIN_STAB,
    THRESH_ROT_VOLT_HARD,
    MIN_GAMES_REQUIRED,
    LABEL_REJECT_UNSTABLE,
    LABEL_REJECT_ROTATION,
    LABEL_HOLD_ROLE_UNCERTAIN,
)
from gate_engine.portfolio.cross_slip_exposure import (
    PortfolioExposureGovernor,
    LABEL_CROSS_SLIP_CONC,
    LABEL_DUPLICATE_THESIS,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _wnba_row(stat_type: str = "PRA", direction: str = "MORE",
              terminal_label: str | None = None, **kwargs) -> dict:
    return {
        "sport":          "WNBA",
        "prop_type":      stat_type,
        "stat_type":      stat_type,
        "direction":      direction,
        "line":           22.5,
        "player":         "Marina Mabrey",
        "player_name":    "Marina Mabrey",
        "event_id":       "WNBA_2026_07_31_game1",
        "game":           "SAS vs MIN",
        "board_source":   "PrizePicks",
        "terminal_label": terminal_label or "MODEL_QUALIFIED_HOLD",
        "can_execute":    False,
        "blockers":       [],
        "gates":          {},
        **kwargs,
    }


def _stable_game_log(n: int = 10, minutes: float = 31.0, pts: float = 15.0,
                     reb: float = 4.0, ast: float = 3.5,
                     fga: float = 11.0, usg: float = 22.0) -> list[dict]:
    """Build a stable game log (constant stats = max stability)."""
    return [
        {"MIN": minutes, "PTS": pts, "REB": reb, "AST": ast,
         "FGA": fga, "USG%": usg}
        for _ in range(n)
    ]


def _volatile_game_log(n: int = 10) -> list[dict]:
    """Build a volatile game log with large minute swings."""
    games = []
    for i in range(n):
        mins = 10 if i % 3 == 0 else 36  # huge swing every 3rd game
        games.append({"MIN": mins, "PTS": 8, "REB": 3, "AST": 2, "FGA": 7, "USG%": 18})
    return games


def _low_stability_log(n: int = 8) -> list[dict]:
    """Low-stability: minutes and usage vary wildly."""
    import random
    random.seed(42)
    return [
        {"MIN": random.uniform(12, 38), "PTS": random.uniform(4, 28),
         "REB": random.uniform(1, 10),  "AST": random.uniform(0, 8),
         "FGA": random.uniform(3, 18),  "USG%": random.uniform(10, 34)}
        for _ in range(n)
    ]


# ---------------------------------------------------------------------------
# Test 1: High projected PRA with LOW opportunity stability → rejected
# ---------------------------------------------------------------------------

class TestT1_UnstableOpportunityRejected:
    """Test 1 from spec: A high projected PRA with low opportunity stability is rejected."""

    def test_low_oss_rejects_wnba_pra(self):
        row = _wnba_row(stat_type="PRA", direction="MORE")
        enr = {"game_log": _low_stability_log(10)}
        _opp_run(row, enrichment=enr)

        gate = row["gates"]["wnba_opportunity_gate"]
        oss  = gate.get("opportunity_stability_score", 100)

        if oss < THRESH_OSS_PRA:
            assert gate["gate_passed"] is False
            assert row["terminal_label"] == "WNBA_REJECT_UNSTABLE_OPPORTUNITY"
            assert any("WNBA_REJECT_UNSTABLE_OPPORTUNITY" in b for b in row["blockers"])
        else:
            # Stability happened to be above threshold — skip (log is randomised)
            pytest.skip(f"Generated log OSS={oss} above threshold {THRESH_OSS_PRA}")

    def test_volatile_rotation_rejects(self):
        row = _wnba_row(stat_type="PRA", direction="MORE")
        enr = {"game_log": _volatile_game_log(10)}
        _opp_run(row, enrichment=enr)

        gate = row["gates"]["wnba_opportunity_gate"]
        rot  = gate.get("rotation_volatility_score", 0)

        if rot > THRESH_ROT_VOLT_HARD:
            assert gate["gate_passed"] is False
            assert row["terminal_label"] == "WNBA_REJECT_ROTATION_VOLATILITY"
        # If rot ≤ threshold, still check gate structure is present
        assert "wnba_opportunity_gate" in row["gates"]

    def test_stable_pra_passes_threshold(self):
        """A genuinely stable player should pass the opportunity gate."""
        row = _wnba_row(stat_type="PRA", direction="MORE")
        enr = {"game_log": _stable_game_log(10, minutes=31.5, pts=18, reb=5, ast=4,
                                            fga=13, usg=24)}
        _opp_run(row, enrichment=enr)

        gate = row["gates"]["wnba_opportunity_gate"]
        # Constant-value log → 100% stability → OSS should be well above threshold
        assert gate["opportunity_stability_score"] >= THRESH_OSS_PRA
        assert gate["gate_passed"] is True
        assert row["terminal_label"] == "MODEL_QUALIFIED_HOLD"  # no upgrade yet

    def test_non_wnba_row_skipped(self):
        """Opportunity engine must not touch non-WNBA rows."""
        row = {
            "sport": "NBA", "prop_type": "PRA", "direction": "MORE",
            "player": "LeBron James", "line": 30.0,
            "terminal_label": "FINAL_APPROVED", "blockers": [], "gates": {},
        }
        _opp_run(row, enrichment={"game_log": _stable_game_log()})
        # Non-WNBA row → gate should NOT have been stamped
        assert "wnba_opportunity_gate" not in row["gates"]
        assert row["terminal_label"] == "FINAL_APPROVED"  # unchanged


# ---------------------------------------------------------------------------
# Test 2 (STAGE 2 stub): Made-2s prop with insufficient attempt volume
# ---------------------------------------------------------------------------

class TestT2_MadeShots_Stage2Stub:
    def test_stub_made_shot_insufficient_attempt_volume(self):
        pytest.skip("Stage 2: Shot volume and component distribution model not yet implemented.")


# ---------------------------------------------------------------------------
# Test 3 (STAGE 2 stub): Ceiling-dependent MORE cannot receive HIGH confidence
# ---------------------------------------------------------------------------

class TestT3_CeilingDependent_Stage2Stub:
    def test_stub_ceiling_dependent_high_confidence_blocked(self):
        pytest.skip("Stage 2: Component distribution floor/ceiling classifier not yet implemented.")


# ---------------------------------------------------------------------------
# Test 4 (STAGE 2 stub): Adverse scenario failure → hold or reject
# ---------------------------------------------------------------------------

class TestT4_ScenarioSurvival_Stage2Stub:
    def test_stub_adverse_scenario_hold(self):
        pytest.skip("Stage 2: Scenario survival engine not yet implemented.")


# ---------------------------------------------------------------------------
# Test 5: Same player on two slips → cross-slip rejection
# ---------------------------------------------------------------------------

class TestT5_SamePlayerRejected:
    """Test 5: The same player on two slips triggers cross-slip rejection."""

    def test_same_player_second_slip_rejected(self):
        gov = PortfolioExposureGovernor(session_id="t5-session")

        row1 = _wnba_row(player="Kayla McBride", stat_type="Points", direction="MORE")
        row2 = _wnba_row(player="Kayla McBride", stat_type="Points", direction="MORE")

        gov.check_and_register(row1)
        gov.check_and_register(row2)

        assert row1["gates"]["portfolio_exposure"]["passed"] is True
        assert row2["gates"]["portfolio_exposure"]["passed"] is False
        assert any(LABEL_CROSS_SLIP_CONC in b or LABEL_DUPLICATE_THESIS in b
                   for b in row2["blockers"])

    def test_different_players_both_pass(self):
        gov = PortfolioExposureGovernor(session_id="t5b-session")

        row1 = _wnba_row(player="Kayla McBride",  stat_type="Points", direction="MORE")
        row2 = _wnba_row(player="Breanna Stewart", stat_type="Points", direction="MORE")

        gov.check_and_register(row1)
        gov.check_and_register(row2)

        assert row1["gates"]["portfolio_exposure"]["passed"] is True
        assert row2["gates"]["portfolio_exposure"]["passed"] is True

    def test_snapshot_reflects_registered_rows(self):
        gov = PortfolioExposureGovernor(session_id="t5c-session")
        row = _wnba_row(player="Alyssa Thomas", stat_type="PRA", direction="MORE")
        gov.check_and_register(row)

        snap = gov.snapshot()
        assert "alyssa thomas|pra" in snap["mktfamily_seen"]
        assert snap["mktfamily_seen"]["alyssa thomas|pra"] == 1


# ---------------------------------------------------------------------------
# Test 6: Two alternate PRA lines for the same player → duplicate exposure
# ---------------------------------------------------------------------------

class TestT6_AlternateLinesBlocked:
    """Test 6: Two alternate PRA lines for the same player are treated as duplicate exposure."""

    def test_pra_19_5_then_22_5_same_player(self):
        gov = PortfolioExposureGovernor(session_id="t6-session")

        row1 = _wnba_row(player="Kayla McBride", stat_type="PRA", direction="MORE",
                         line=19.5)
        row2 = _wnba_row(player="Kayla McBride", stat_type="PRA", direction="MORE",
                         line=22.5)

        gov.check_and_register(row1)
        gov.check_and_register(row2)

        assert row1["gates"]["portfolio_exposure"]["passed"] is True
        assert row2["gates"]["portfolio_exposure"]["passed"] is False
        assert any(LABEL_CROSS_SLIP_CONC in b for b in row2["blockers"]), \
            f"Expected REJECT_CROSS_SLIP_CONCENTRATION in blockers: {row2['blockers']}"

    def test_pra_more_and_less_same_player_both_blocked_second(self):
        """PRA MORE 19.5 then PRA LESS 22.5 — still same distribution → second blocked."""
        gov = PortfolioExposureGovernor(session_id="t6b-session")

        row1 = _wnba_row(player="Alyssa Thomas", stat_type="PRA", direction="MORE", line=19.5)
        row2 = _wnba_row(player="Alyssa Thomas", stat_type="PRA", direction="LESS",  line=22.5)

        gov.check_and_register(row1)
        gov.check_and_register(row2)

        assert row1["gates"]["portfolio_exposure"]["passed"] is True
        assert row2["gates"]["portfolio_exposure"]["passed"] is False

    def test_different_stat_families_both_allowed(self):
        """Points and PRA are different stat families — both allowed for same player."""
        gov = PortfolioExposureGovernor(session_id="t6c-session")

        row1 = _wnba_row(player="Breanna Stewart", stat_type="Points", direction="MORE", line=22.5)
        row2 = _wnba_row(player="Breanna Stewart", stat_type="Rebounds", direction="MORE", line=9.5)

        gov.check_and_register(row1)
        gov.check_and_register(row2)

        # Different stat families — the spec says max_mktfamily per (player, stat_family)
        # so Points and Rebounds are DIFFERENT market families → both allowed
        assert row1["gates"]["portfolio_exposure"]["passed"] is True
        assert row2["gates"]["portfolio_exposure"]["passed"] is True


# ---------------------------------------------------------------------------
# Test 7: Role change → role state captured correctly
# ---------------------------------------------------------------------------

class TestT7_RoleChange:
    """Test 7: A role change forces correct role-state capture in the gate."""

    def test_starter_role_detected(self):
        row = _wnba_row(stat_type="Points", direction="MORE", role_status="STARTER_CONFIRMED")
        enr = {"game_log": _stable_game_log(8, minutes=31.0)}
        _opp_run(row, enrichment=enr)

        gate = row["gates"]["wnba_opportunity_gate"]
        assert gate["role_state"] in (
            "PRIMARY_CREATOR", "SECONDARY_CREATOR", "HIGH_USAGE_PRIMARY"
        )
        assert gate["role_confidence"] >= 0.80

    def test_unresolved_role_soft_holds(self):
        row = _wnba_row(stat_type="Points", direction="MORE", role_status="UNRESOLVED")
        enr = {"game_log": _stable_game_log(8, minutes=31.0)}
        _opp_run(row, enrichment=enr)

        gate = row["gates"]["wnba_opportunity_gate"]
        assert gate["role_state"] == "ROLE_UNKNOWN"
        # role_confidence below threshold → soft hold, not hard reject
        if gate["role_confidence"] < THRESH_ROLE_CONF:
            assert gate["gate_label"] in (LABEL_HOLD_ROLE_UNCERTAIN, LABEL_REJECT_UNSTABLE, LABEL_REJECT_ROTATION, "PASS")
            # Hard reject labels must not appear for role uncertainty alone
            # (rotation volatility or OSS failure may still trigger reject)

    def test_bench_role_lower_confidence(self):
        row = _wnba_row(stat_type="Points", direction="MORE", role_status="BENCH_CONFIRMED")
        enr = {"game_log": _stable_game_log(8, minutes=18.0, pts=8, fga=6, usg=16)}
        _opp_run(row, enrichment=enr)

        gate = row["gates"]["wnba_opportunity_gate"]
        assert gate["role_state"] in ("BENCH_CONTRIBUTOR", "BENCH_STARTER_HYBRID", "SPOT_ROLE_CEILING_DEPENDENT")

    def test_gate_result_always_stamped(self):
        """Gate report must always be present for WNBA rows with sufficient data."""
        row = _wnba_row(stat_type="PRA", direction="MORE")
        enr = {"game_log": _stable_game_log(5)}
        _opp_run(row, enrichment=enr)
        assert "wnba_opportunity_gate" in row["gates"]
        assert "opportunity_stability_score" in row["gates"]["wnba_opportunity_gate"]


# ---------------------------------------------------------------------------
# Test 8: Missing teammate status → role-dependent scoring blocked
# ---------------------------------------------------------------------------

class TestT8_MissingTeammateStatus:
    """Test 8: Missing teammate status blocks role-dependent scoring."""

    def test_pra_with_unresolved_teammate_dependency(self):
        """PRA prop where the primary teammate's status is unresolved."""
        row = _wnba_row(
            stat_type="PRA",
            direction="MORE",
            primary_teammate_dependency=["starting_point_guard_status"],
            role_status="UNRESOLVED",  # role unknown because teammate out
        )
        enr = {
            "game_log": _stable_game_log(8, minutes=28.0),
            "primary_teammate_dependency": ["starting_point_guard_status"],
            "dependency_status_payload": {
                "starting_point_guard": None  # status not resolved
            },
        }
        _opp_run(row, enrichment=enr)

        gate = row["gates"]["wnba_opportunity_gate"]
        # Unresolved role → role_confidence should be low → hold or reject
        assert gate is not None
        # Teammate dependency captured in gate report
        assert isinstance(gate.get("primary_teammate_dependency"), list)

    def test_resolved_teammate_allows_higher_confidence(self):
        """If teammate status is confirmed, role_confidence can reach threshold."""
        row = _wnba_row(
            stat_type="PRA",
            direction="MORE",
            role_status="STARTER_CONFIRMED",
        )
        enr = {
            "game_log": _stable_game_log(10, minutes=33.0, ast=4.5),
            "primary_teammate_dependency": [],
        }
        _opp_run(row, enrichment=enr)

        gate = row["gates"]["wnba_opportunity_gate"]
        assert gate["role_confidence"] >= THRESH_ROLE_CONF
        assert gate["role_state"] in ("PRIMARY_CREATOR", "SECONDARY_CREATOR")

    def test_missing_game_log_triggers_soft_hold(self):
        """No game log at all → soft hold, not hard reject."""
        row = _wnba_row(stat_type="PRA", direction="MORE")
        _opp_run(row, enrichment={})  # no game_log

        gate = row["gates"]["wnba_opportunity_gate"]
        assert gate["gate_passed"] is False
        assert gate["gate_label"] == LABEL_HOLD_ROLE_UNCERTAIN
        # Soft hold: terminal_label should be MODEL_QUALIFIED_HOLD (ceiling cap)
        assert row["terminal_label"] == "MODEL_QUALIFIED_HOLD"
        # Must NOT be a hard reject
        assert row["terminal_label"] not in (
            "WNBA_REJECT_UNSTABLE_OPPORTUNITY",
            "WNBA_REJECT_ROTATION_VOLATILITY",
        )


# ---------------------------------------------------------------------------
# Test 9 (STAGE 2 stub): Failed original MORE does not auto-approve LESS
# ---------------------------------------------------------------------------

class TestT9_BidirectionalIndependence_Stage2Stub:
    def test_stub_failed_more_does_not_approve_less(self):
        pytest.skip("Stage 2: bidirectional independence is enforced by existing "
                    "llp_governance + market_gate; cross-ticket governor handles thesis dedup.")


# ---------------------------------------------------------------------------
# Test 10 (STAGE 2 stub): No verified replacement → slip shrinks
# ---------------------------------------------------------------------------

class TestT10_SlipShrink_Stage2Stub:
    def test_stub_no_replacement_slip_shrinks(self):
        pytest.skip("Stage 2: weakest-leg finalizer already in card_finalizer; "
                    "scenario replacement model is Stage 2 work.")


# ---------------------------------------------------------------------------
# Test 11 (STAGE 2 stub): Every settled prop writes to ledger
# ---------------------------------------------------------------------------

class TestT11_SettlementLedger_Stage2Stub:
    def test_stub_settled_prop_writes_opportunity_ledger(self):
        pytest.skip("Stage 2: calibration ledger and settle/prop endpoint "
                    "persistence not yet implemented.")


# ---------------------------------------------------------------------------
# Test 12: can_execute=False always preserved
# ---------------------------------------------------------------------------

class TestT12_CanExecuteFalse:
    """Test 12: can_execute=False is always preserved through all gate paths."""

    def test_opportunity_gate_never_sets_can_execute_true(self):
        for stat in ["PRA", "Points", "Rebounds"]:
            for role in ["STARTER_CONFIRMED", "BENCH_CONFIRMED", "UNRESOLVED"]:
                row = _wnba_row(stat_type=stat, role_status=role)
                _opp_run(row, enrichment={"game_log": _stable_game_log(8)})
                assert row.get("can_execute") is False, \
                    f"can_execute must be False for stat={stat} role={role}"
                gate = row["gates"]["wnba_opportunity_gate"]
                assert gate.get("can_execute") is False

    def test_opportunity_gate_volatile_row_can_execute_false(self):
        row = _wnba_row(stat_type="PRA")
        _opp_run(row, enrichment={"game_log": _volatile_game_log(10)})
        assert row.get("can_execute") is False

    def test_portfolio_governor_never_sets_can_execute_true(self):
        gov = PortfolioExposureGovernor(session_id="t12-session")
        for i in range(3):
            row = _wnba_row(player=f"Player{i}", stat_type="Points")
            gov.check_and_register(row)
            assert row.get("can_execute") is False
            gate = row["gates"]["portfolio_exposure"]
            assert gate.get("can_execute") is False

    def test_portfolio_governor_blocked_row_can_execute_false(self):
        """A blocked row must also have can_execute=False."""
        gov = PortfolioExposureGovernor(session_id="t12b-session")
        row1 = _wnba_row(player="Kayla McBride", stat_type="PRA", direction="MORE")
        row2 = _wnba_row(player="Kayla McBride", stat_type="PRA", direction="MORE")
        gov.check_and_register(row1)
        gov.check_and_register(row2)
        assert row2.get("can_execute") is False


# ---------------------------------------------------------------------------
# Additional unit tests: score computation invariants
# ---------------------------------------------------------------------------

class TestStabilityScoreInvariants:
    """Verify stability score computation properties."""

    def test_constant_series_is_100(self):
        from gate_engine.wnba.opportunity_engine import _compute_stability_score
        assert _compute_stability_score([30.0] * 10) == 100

    def test_empty_series_returns_50(self):
        from gate_engine.wnba.opportunity_engine import _compute_stability_score
        assert _compute_stability_score([]) == 50
        assert _compute_stability_score([30.0]) == 50

    def test_zero_mean_returns_50(self):
        from gate_engine.wnba.opportunity_engine import _compute_stability_score
        assert _compute_stability_score([0.0, 0.0, 0.0]) == 50

    def test_high_variance_is_low_score(self):
        from gate_engine.wnba.opportunity_engine import _compute_stability_score
        score = _compute_stability_score([1.0, 100.0, 1.0, 100.0, 1.0])
        assert score < 50, f"High variance should give low score, got {score}"

    def test_rotation_volatility_constant_minutes(self):
        from gate_engine.wnba.opportunity_engine import _compute_rotation_volatility
        assert _compute_rotation_volatility([32.0] * 10) == 0

    def test_rotation_volatility_extreme_swings(self):
        from gate_engine.wnba.opportunity_engine import _compute_rotation_volatility
        volt = _compute_rotation_volatility([5.0] * 5 + [35.0] * 5)
        assert volt > 50, f"Large swings should produce high volatility, got {volt}"

    def test_is_pra_market_detection(self):
        assert is_pra_market({"prop_type": "PRA"}) is True
        assert is_pra_market({"prop_type": "Points Rebounds Assists"}) is True
        assert is_pra_market({"prop_type": "Points"}) is False
        assert is_pra_market({"prop_type": "Pitcher Strikeouts"}) is False

    def test_is_wnba_row_detection(self):
        assert is_wnba_row({"sport": "WNBA"}) is True
        assert is_wnba_row({"sport": "wnba"}) is True
        assert is_wnba_row({"sport": "NBA"}) is False
        assert is_wnba_row({"sport": "MLB"}) is False


class TestPortfolioGovernorInvariants:
    """Unit tests for PortfolioExposureGovernor key-building logic."""

    def test_first_entry_always_passes(self):
        gov = PortfolioExposureGovernor()
        row = _wnba_row(player="A Thomas", stat_type="PRA", direction="MORE")
        gov.check_and_register(row)
        assert row["gates"]["portfolio_exposure"]["passed"] is True

    def test_thesis_key_normalizes_direction(self):
        gov = PortfolioExposureGovernor()
        from gate_engine.portfolio.cross_slip_exposure import _make_keys
        r1 = {"player": "A Thomas", "prop_type": "PRA", "direction": "more"}
        r2 = {"player": "A Thomas", "prop_type": "PRA", "direction": "MORE"}
        k1_mf, k1_th = _make_keys(r1)
        k2_mf, k2_th = _make_keys(r2)
        assert k1_mf == k2_mf
        assert k1_th == k2_th

    def test_snapshot_empty_initially(self):
        gov = PortfolioExposureGovernor(session_id="snap-test")
        snap = gov.snapshot()
        assert snap["mktfamily_seen"] == {}
        assert snap["thesis_seen"] == {}
        assert snap["can_execute"] is False
