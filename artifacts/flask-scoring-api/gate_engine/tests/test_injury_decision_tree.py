"""
test_injury_decision_tree.py — Phase 3 Injury Decision Tree Suite

Verifies that injury_decision_tree correctly resolves role-dependency states
for WNBA props and that the classifier enforces the resulting caps.

Run with:
    cd artifacts/flask-scoring-api
    python -m pytest gate_engine/tests/test_injury_decision_tree.py -v
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from gate_engine import injury_decision_tree
from gate_engine.injury_decision_tree import (
    STATUS_DEPENDENCY_CLEAR,
    STATUS_DEPENDENCY_SUPPORTS_MORE,
    STATUS_DEPENDENCY_SUPPORTS_LESS,
    STATUS_DEPENDENCY_CONFLICT,
    STATUS_DEPENDENCY_UNRESOLVED,
    STATUS_ROLE_STATE_STALE,
    STATUS_NO_DEPENDENCY,
    run as idt_run,
    run_batch,
    build_injury_decision_ledger,
)
from gate_engine.classifier import classify
from gate_engine.labels import PropLabel


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _row(player: str, prop_type: str, direction: str = "MORE") -> dict:
    return {
        "row_id":    f"test-{player[:4].replace(' ', '')}-1",
        "player":    player,
        "prop_type": prop_type,
        "direction": direction,
        "line":      10.5,
        "blockers":  [],
        "gates":     {},
    }


def _dep_payload(player_key: str, status: str, confirmed_at: str | None = None) -> dict:
    """Build a dependency_status_payload dict for one dependency player."""
    ts = confirmed_at or datetime.now(tz=timezone.utc).isoformat()
    return {player_key: {"status": status, "confirmed_at": ts, "source": "test"}}


def _fresh_ts() -> str:
    """Return an ISO timestamp 1 hour ago (not stale)."""
    return (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()


def _stale_ts() -> str:
    """Return an ISO timestamp 8 hours ago (stale)."""
    return (datetime.now(tz=timezone.utc) - timedelta(hours=8)).isoformat()


def _would_final_approve_gates(inj_gate: dict | None = None) -> dict:
    """Return a gate dict that passes all REQUIRED_FOR_FINAL and is market-verified."""
    gates = {
        "slate_validation": {"passed": True},
        "status_role":      {"passed": True},
        "l5_l10_ledger":    {"passed": True},
        "market_gate": {
            "passed":                True,
            "market_status":         "MARKET_VERIFIED",
            "confidence_cap":        None,
            "cash_threshold_status": "EXACT_VERIFIED",
        },
        "ev_gate":        {"passed": True, "money_qualified": True, "edge_score": 0.05},
        "slip_structure":  {"passed": True},
        "exposure_gate":   {"passed": True},
    }
    if inj_gate is not None:
        gates["injury_decision_tree"] = inj_gate
    return gates


def _classifier_row(inj_gate: dict | None = None) -> dict:
    return {
        "row_id":   "cls-test-1",
        "player":   "Test Player",
        "blockers": [],
        "gates":    _would_final_approve_gates(inj_gate),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests: injury_decision_tree.run()
# ─────────────────────────────────────────────────────────────────────────────

class TestNoDependency:
    def test_no_dependency_rule_returns_no_dependency(self):
        """Player with no registered dependency rule → NO_DEPENDENCY, flag=False."""
        row = _row("Unknown Player", "points")
        idt_run(row)
        inj = row["gates"]["injury_decision_tree"]
        assert inj["injury_tree_status"] == STATUS_NO_DEPENDENCY
        assert inj["injury_dependency_flag"] is False
        assert inj["dependency_player"] is None
        assert inj["passed"] is True
        assert row["blockers"] == []

    def test_wrong_prop_family_no_dependency(self):
        """Courtney Williams but prop_type='rebounds' (not 'assists') → NO_DEPENDENCY."""
        row = _row("Courtney Williams", "rebounds")
        idt_run(row)
        inj = row["gates"]["injury_decision_tree"]
        assert inj["injury_tree_status"] == STATUS_NO_DEPENDENCY

    def test_wrong_direction_no_dependency(self):
        """Courtney Williams assists LESS → rule only fires for MORE → NO_DEPENDENCY."""
        row = _row("Courtney Williams", "assists", direction="LESS")
        idt_run(row)
        inj = row["gates"]["injury_decision_tree"]
        assert inj["injury_tree_status"] == STATUS_NO_DEPENDENCY


class TestCourtneyWilliamsOliviaMiles:
    """Rule 1: Courtney Williams MORE assists ← Olivia Miles."""

    def test_miles_out_supports_williams_assist_path(self):
        """Miles OUT → DEPENDENCY_SUPPORTS_MORE, flag=True, blocker added."""
        row = _row("Courtney Williams", "assists")
        dep = _dep_payload("olivia miles", "OUT", _fresh_ts())
        idt_run(row, dependency_status_payload=dep)
        inj = row["gates"]["injury_decision_tree"]
        assert inj["injury_tree_status"] == STATUS_DEPENDENCY_SUPPORTS_MORE
        assert inj["injury_dependency_flag"] is True
        assert "Olivia Miles" in (inj["dependency_player"] or "")
        assert inj["dependency_status"] == "OUT"
        assert inj["passed"] is True
        # SUPPORTS_MORE is not a blocker
        assert not any("INJURY_TREE" in b for b in row["blockers"])

    def test_miles_active_conflicts_with_williams_assist_path(self):
        """Miles ACTIVE → DEPENDENCY_CONFLICT, passed=False, blocker added."""
        row = _row("Courtney Williams", "assists")
        dep = _dep_payload("olivia miles", "ACTIVE", _fresh_ts())
        idt_run(row, dependency_status_payload=dep)
        inj = row["gates"]["injury_decision_tree"]
        assert inj["injury_tree_status"] == STATUS_DEPENDENCY_CONFLICT
        assert inj["passed"] is False
        assert inj["injury_dependency_flag"] is True
        assert any("INJURY_TREE:DEPENDENCY_CONFLICT" in b for b in row["blockers"])

    def test_miles_full_go_conflicts_with_williams(self):
        """Miles FULL_GO → also DEPENDENCY_CONFLICT."""
        row = _row("Courtney Williams", "assists")
        dep = _dep_payload("olivia miles", "FULL_GO", _fresh_ts())
        idt_run(row, dependency_status_payload=dep)
        assert row["gates"]["injury_decision_tree"]["injury_tree_status"] == STATUS_DEPENDENCY_CONFLICT

    def test_miles_questionable_blocks_final_approval(self):
        """Miles QUESTIONABLE → DEPENDENCY_UNRESOLVED, blocker added, passed=True."""
        row = _row("Courtney Williams", "assists")
        dep = _dep_payload("olivia miles", "QUESTIONABLE", _fresh_ts())
        idt_run(row, dependency_status_payload=dep)
        inj = row["gates"]["injury_decision_tree"]
        assert inj["injury_tree_status"] == STATUS_DEPENDENCY_UNRESOLVED
        assert inj["passed"] is True
        assert any("INJURY_TREE:DEPENDENCY_UNRESOLVED" in b for b in row["blockers"])

    def test_miles_status_absent_is_unresolved(self):
        """No payload for Miles at all → DEPENDENCY_UNRESOLVED."""
        row = _row("Courtney Williams", "assists")
        idt_run(row, dependency_status_payload={})
        inj = row["gates"]["injury_decision_tree"]
        assert inj["injury_tree_status"] == STATUS_DEPENDENCY_UNRESOLVED

    def test_williams_p_plus_a_triggers_dependency(self):
        """prop_type 'p+a' also triggers the Williams/Miles assists rule."""
        row = _row("Courtney Williams", "p+a")
        dep = _dep_payload("olivia miles", "OUT", _fresh_ts())
        idt_run(row, dependency_status_payload=dep)
        assert row["gates"]["injury_decision_tree"]["injury_tree_status"] == STATUS_DEPENDENCY_SUPPORTS_MORE


class TestAmooreAndMcMahonCitron:
    """Rule 2/3: Georgia Amoore / Coti McMahon MORE points ← Sonia Citron."""

    def test_citron_out_supports_amoore_points(self):
        """Citron OUT → DEPENDENCY_SUPPORTS_MORE for Amoore points."""
        row = _row("Georgia Amoore", "points")
        dep = _dep_payload("sonia citron", "OUT", _fresh_ts())
        idt_run(row, dependency_status_payload=dep)
        inj = row["gates"]["injury_decision_tree"]
        assert inj["injury_tree_status"] == STATUS_DEPENDENCY_SUPPORTS_MORE
        assert inj["injury_dependency_flag"] is True

    def test_citron_active_conflicts_with_amoore(self):
        """Citron ACTIVE → DEPENDENCY_CONFLICT for Amoore points."""
        row = _row("Georgia Amoore", "points")
        dep = _dep_payload("sonia citron", "ACTIVE", _fresh_ts())
        idt_run(row, dependency_status_payload=dep)
        assert row["gates"]["injury_decision_tree"]["injury_tree_status"] == STATUS_DEPENDENCY_CONFLICT

    def test_citron_out_supports_mcmahon_points(self):
        """Citron OUT → DEPENDENCY_SUPPORTS_MORE for Coti McMahon points."""
        row = _row("Coti McMahon", "points")
        dep = _dep_payload("sonia citron", "OUT", _fresh_ts())
        idt_run(row, dependency_status_payload=dep)
        assert row["gates"]["injury_decision_tree"]["injury_tree_status"] == STATUS_DEPENDENCY_SUPPORTS_MORE

    def test_citron_available_conflicts_with_mcmahon(self):
        """Citron AVAILABLE → DEPENDENCY_CONFLICT for McMahon."""
        row = _row("Coti McMahon", "points")
        dep = _dep_payload("sonia citron", "AVAILABLE", _fresh_ts())
        idt_run(row, dependency_status_payload=dep)
        assert row["gates"]["injury_decision_tree"]["injury_tree_status"] == STATUS_DEPENDENCY_CONFLICT


class TestAtkinHambyPlumBrink:
    """Rules 4/5: Ariel Atkins / Dearica Hamby MORE ← Kelsey Plum + Cameron Brink."""

    def test_plum_out_supports_atkins_usage_path(self):
        """Plum OUT → DEPENDENCY_SUPPORTS_MORE for Atkins points."""
        row = _row("Ariel Atkins", "points")
        dep = _dep_payload("kelsey plum", "OUT", _fresh_ts())
        idt_run(row, dependency_status_payload=dep)
        inj = row["gates"]["injury_decision_tree"]
        assert inj["injury_tree_status"] == STATUS_DEPENDENCY_SUPPORTS_MORE
        assert "Kelsey Plum" in (inj["dependency_player"] or "")

    def test_plum_active_conflicts_with_atkins(self):
        """Plum ACTIVE → DEPENDENCY_CONFLICT for Atkins points."""
        row = _row("Ariel Atkins", "points")
        dep = _dep_payload("kelsey plum", "ACTIVE", _fresh_ts())
        idt_run(row, dependency_status_payload=dep)
        assert row["gates"]["injury_decision_tree"]["injury_tree_status"] == STATUS_DEPENDENCY_CONFLICT

    def test_plum_out_supports_hamby(self):
        """Plum OUT → DEPENDENCY_SUPPORTS_MORE for Hamby points."""
        row = _row("Dearica Hamby", "points")
        dep = _dep_payload("kelsey plum", "OUT", _fresh_ts())
        idt_run(row, dependency_status_payload=dep)
        assert row["gates"]["injury_decision_tree"]["injury_tree_status"] == STATUS_DEPENDENCY_SUPPORTS_MORE

    def test_plum_conflict_wins_over_brink_support(self):
        """Plum ACTIVE (conflict) + Brink OUT (support): conflict dominates → DEPENDENCY_CONFLICT."""
        row = _row("Ariel Atkins", "p+r")
        dep = {
            "kelsey plum":  {"status": "ACTIVE",  "confirmed_at": _fresh_ts()},
            "cameron brink": {"status": "OUT",    "confirmed_at": _fresh_ts()},
        }
        idt_run(row, dependency_status_payload=dep)
        assert row["gates"]["injury_decision_tree"]["injury_tree_status"] == STATUS_DEPENDENCY_CONFLICT

    def test_both_plum_and_brink_out_supports_atkins(self):
        """Both Plum OUT and Brink OUT → DEPENDENCY_SUPPORTS_MORE (combined)."""
        row = _row("Ariel Atkins", "p+r")
        dep = {
            "kelsey plum":  {"status": "OUT", "confirmed_at": _fresh_ts()},
            "cameron brink": {"status": "OUT", "confirmed_at": _fresh_ts()},
        }
        idt_run(row, dependency_status_payload=dep)
        assert row["gates"]["injury_decision_tree"]["injury_tree_status"] == STATUS_DEPENDENCY_SUPPORTS_MORE


class TestStaleTimestamp:
    def test_stale_status_timestamp_returns_role_state_stale(self):
        """confirmed_at older than 6 hours → ROLE_STATE_STALE regardless of status value."""
        row = _row("Courtney Williams", "assists")
        dep = _dep_payload("olivia miles", "OUT", _stale_ts())
        idt_run(row, dependency_status_payload=dep)
        inj = row["gates"]["injury_decision_tree"]
        assert inj["injury_tree_status"] == STATUS_ROLE_STATE_STALE
        assert inj["role_state"] == STATUS_ROLE_STATE_STALE
        assert any("INJURY_TREE:ROLE_STATE_STALE" in b for b in row["blockers"])

    def test_fresh_timestamp_is_not_stale(self):
        """confirmed_at 1 hour ago → not stale → normal status resolution."""
        row = _row("Courtney Williams", "assists")
        dep = _dep_payload("olivia miles", "OUT", _fresh_ts())
        idt_run(row, dependency_status_payload=dep)
        inj = row["gates"]["injury_decision_tree"]
        assert inj["injury_tree_status"] != STATUS_ROLE_STATE_STALE


# ─────────────────────────────────────────────────────────────────────────────
# Classifier integration tests
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifierInjuryTreeCaps:
    """Verify the classifier applies Phase 3 caps from injury_tree_status."""

    def test_no_dependency_does_not_block_final_approved(self):
        """NO_DEPENDENCY row with all gates passing → FINAL_APPROVED."""
        inj_gate = {"injury_tree_status": STATUS_NO_DEPENDENCY, "injury_dependency_flag": False}
        row = _classifier_row(inj_gate)
        classify(row)
        assert row["terminal_label"] == PropLabel.FINAL_APPROVED.value

    def test_dependency_supports_more_does_not_block_final_approved(self):
        """DEPENDENCY_SUPPORTS_MORE is a positive signal — must NOT block FINAL_APPROVED."""
        inj_gate = {"injury_tree_status": STATUS_DEPENDENCY_SUPPORTS_MORE,
                    "injury_dependency_flag": True}
        row = _classifier_row(inj_gate)
        classify(row)
        assert row["terminal_label"] == PropLabel.FINAL_APPROVED.value

    def test_dependency_unresolved_caps_at_money_qualified(self):
        """DEPENDENCY_UNRESOLVED blocks FINAL_APPROVED → caps at MONEY_QUALIFIED."""
        inj_gate = {"injury_tree_status": STATUS_DEPENDENCY_UNRESOLVED,
                    "injury_dependency_flag": True}
        row = _classifier_row(inj_gate)
        classify(row)
        assert row["terminal_label"] == PropLabel.MONEY_QUALIFIED.value
        assert any("CLASSIFIER:INJURY_TREE_CAP:DEPENDENCY_UNRESOLVED" in b
                   for b in row["blockers"])

    def test_dependency_conflict_caps_at_model_qualified_hold(self):
        """DEPENDENCY_CONFLICT is the hardest cap → MODEL_QUALIFIED_HOLD."""
        inj_gate = {"injury_tree_status": STATUS_DEPENDENCY_CONFLICT,
                    "injury_dependency_flag": True}
        row = _classifier_row(inj_gate)
        classify(row)
        assert row["terminal_label"] == PropLabel.MODEL_QUALIFIED_HOLD.value
        assert any("CLASSIFIER:INJURY_TREE_CAP:DEPENDENCY_CONFLICT" in b
                   for b in row["blockers"])

    def test_role_state_stale_caps_at_money_qualified(self):
        """ROLE_STATE_STALE blocks FINAL_APPROVED → caps at MONEY_QUALIFIED."""
        inj_gate = {"injury_tree_status": STATUS_ROLE_STATE_STALE,
                    "injury_dependency_flag": True}
        row = _classifier_row(inj_gate)
        classify(row)
        assert row["terminal_label"] == PropLabel.MONEY_QUALIFIED.value
        assert any("CLASSIFIER:INJURY_TREE_CAP:ROLE_STATE_STALE" in b
                   for b in row["blockers"])

    def test_phase2_model_hold_plus_dependency_conflict_stays_model_hold(self):
        """Phase 2 MODEL_QUALIFIED_HOLD (from cash cap) + Phase 3 DEPENDENCY_CONFLICT:
        Phase 2 already returns early with MODEL_QUALIFIED_HOLD — Phase 3 never runs
        but the result is still MODEL_QUALIFIED_HOLD (already most restrictive)."""
        gates = _would_final_approve_gates()
        gates["market_gate"]["confidence_cap"] = "MODEL_QUALIFIED_HOLD"
        gates["market_gate"]["cash_threshold_status"] = "CASH_THRESHOLD_NOT_VALIDATED"
        gates["injury_decision_tree"] = {"injury_tree_status": STATUS_DEPENDENCY_CONFLICT,
                                          "injury_dependency_flag": True}
        row = {"row_id": "combined-1", "player": "Test", "blockers": [], "gates": gates}
        classify(row)
        assert row["terminal_label"] == PropLabel.MODEL_QUALIFIED_HOLD.value

    def test_pre_set_terminal_label_not_overridden(self):
        """Terminal labels set before classify() (e.g. SLATE_PURGE) are never touched."""
        inj_gate = {"injury_tree_status": STATUS_DEPENDENCY_CONFLICT,
                    "injury_dependency_flag": True}
        row = _classifier_row(inj_gate)
        row["terminal_label"] = PropLabel.SLATE_PURGE.value
        classify(row)
        assert row["terminal_label"] == PropLabel.SLATE_PURGE.value


# ─────────────────────────────────────────────────────────────────────────────
# run_batch / ledger tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRunBatchAndLedger:
    def test_run_batch_returns_ledger(self):
        """run_batch() processes all rows and returns a ledger with one entry per row."""
        rows = [
            _row("Courtney Williams", "assists"),
            _row("Unknown Player", "points"),
        ]
        dep = _dep_payload("olivia miles", "OUT", _fresh_ts())
        ledger = run_batch(rows, dependency_status_payload=dep)
        assert len(ledger) == 2
        # First row has dependency
        assert ledger[0]["injury_dependency_flag"] is True
        assert ledger[0]["injury_tree_status"] == STATUS_DEPENDENCY_SUPPORTS_MORE
        # Second row has no dependency
        assert ledger[1]["injury_dependency_flag"] is False
        assert ledger[1]["injury_tree_status"] == STATUS_NO_DEPENDENCY

    def test_ledger_fields_present(self):
        """Every ledger entry contains all required Phase 3 output fields."""
        row = _row("Georgia Amoore", "points")
        dep = _dep_payload("sonia citron", "LIMITED", _fresh_ts())
        ledger = run_batch([row], dependency_status_payload=dep)
        entry = ledger[0]
        for field in [
            "row_id", "player", "prop_type", "direction",
            "injury_dependency_flag", "dependency_player", "dependency_status",
            "role_state", "role_state_timestamp", "role_effect_direction",
            "approval_condition", "injury_tree_status", "injury_tree_blocker",
            "terminal_label",
        ]:
            assert field in entry, f"Missing required ledger field: {field}"
