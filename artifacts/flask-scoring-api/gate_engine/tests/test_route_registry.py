"""
Tests for gate_engine/route_registry.py
WOW-PATCH-2026-08-02-MANDATORY-ROUTE-COMPLETION
"""
import pytest
from gate_engine.route_registry import (
    QUALIFYING_LABELS,
    UNIVERSAL_REQUIRED_GATES,
    DOWNGRADE_LABEL,
    can_execute,
    get_required_gates,
    check_route_completion,
    enforce_route_completion,
    build_row_execution_trace,
)


def test_can_execute_is_false():
    assert can_execute is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _full_gates():
    return {k: {"passed": True} for k in UNIVERSAL_REQUIRED_GATES}


def _row(sport="MLB", prop_type="STRIKEOUTS", label="FINAL_APPROVED", gates=None):
    return {
        "row_id": "r1",
        "player": "Test Player",
        "sport": sport,
        "prop_type": prop_type,
        "terminal_label": label,
        "blockers": [],
        "gates": gates if gates is not None else _full_gates(),
    }


# ---------------------------------------------------------------------------
# get_required_gates
# ---------------------------------------------------------------------------

class TestGetRequiredGates:
    def test_universal_always_included(self):
        req = get_required_gates({"sport": "MLB", "prop_type": "STRIKEOUTS"})
        assert UNIVERSAL_REQUIRED_GATES.issubset(req)

    def test_mlb_adds_acquisition(self):
        req = get_required_gates({"sport": "MLB", "prop_type": "STRIKEOUTS"})
        assert "acquisition" in req

    def test_wnba_adds_acquisition(self):
        req = get_required_gates({"sport": "WNBA", "prop_type": "POINTS"})
        assert "acquisition" in req

    def test_unknown_sport_no_extra(self):
        req = get_required_gates({"sport": "CRICKET", "prop_type": "RUNS"})
        assert req == UNIVERSAL_REQUIRED_GATES

    def test_1ip_adds_calibration_health(self):
        req = get_required_gates({"sport": "MLB", "prop_type": "1IP_PITCHES_THROWN"})
        assert "calibration_health" in req
        assert "acquisition" in req

    def test_prop_type_normalization(self):
        req1 = get_required_gates({"sport": "MLB", "prop_type": "1IP Pitches Thrown"})
        req2 = get_required_gates({"sport": "MLB", "prop_type": "1IP_PITCHES_THROWN"})
        assert req1 == req2

    def test_none_sport_and_prop_returns_universal(self):
        req = get_required_gates({})
        assert req == UNIVERSAL_REQUIRED_GATES


# ---------------------------------------------------------------------------
# check_route_completion
# ---------------------------------------------------------------------------

class TestCheckRouteCompletion:
    def test_all_gates_present_returns_empty(self):
        gates = {k: {"passed": True} for k in UNIVERSAL_REQUIRED_GATES | {"acquisition"}}
        row = {"sport": "MLB", "prop_type": "STRIKEOUTS", "gates": gates}
        assert check_route_completion(row) == []

    def test_missing_one_gate(self):
        gates = {k: {"passed": True} for k in UNIVERSAL_REQUIRED_GATES}
        # MLB also requires acquisition — leave it out
        row = {"sport": "MLB", "prop_type": "STRIKEOUTS", "gates": gates}
        missing = check_route_completion(row)
        assert "acquisition" in missing

    def test_missing_multiple_gates(self):
        row = {"sport": "MLB", "prop_type": "STRIKEOUTS", "gates": {}}
        missing = check_route_completion(row)
        assert len(missing) >= len(UNIVERSAL_REQUIRED_GATES)

    def test_result_is_sorted_list(self):
        row = {"sport": "NBA", "prop_type": "POINTS", "gates": {}}
        missing = check_route_completion(row)
        assert missing == sorted(missing)


# ---------------------------------------------------------------------------
# enforce_route_completion
# ---------------------------------------------------------------------------

class TestEnforceRouteCompletion:
    def test_non_qualifying_label_not_touched(self):
        row = _row(label="RESEARCH_INTEREST")
        changed = enforce_route_completion(row)
        assert changed is False
        assert row["terminal_label"] == "RESEARCH_INTEREST"

    def test_reject_label_not_touched(self):
        row = _row(label="REJECT_NO_EDGE")
        changed = enforce_route_completion(row)
        assert changed is False

    def test_no_play_not_touched(self):
        row = _row(label="NO_PLAY")
        changed = enforce_route_completion(row)
        assert changed is False

    def test_complete_route_not_downgraded(self):
        """MLB row with all universal + acquisition gates present."""
        gates = {k: {"passed": True} for k in UNIVERSAL_REQUIRED_GATES | {"acquisition"}}
        row = _row(sport="MLB", prop_type="STRIKEOUTS", label="FINAL_APPROVED", gates=gates)
        changed = enforce_route_completion(row)
        assert changed is False
        assert row["terminal_label"] == "FINAL_APPROVED"

    def test_missing_acquisition_downgrades_final_approved(self):
        row = _row(sport="MLB", prop_type="STRIKEOUTS", label="FINAL_APPROVED")
        # default gates don't include acquisition
        changed = enforce_route_completion(row)
        assert changed is True
        assert row["terminal_label"] == DOWNGRADE_LABEL
        assert any("REQUIRED_GATE_NOT_EXECUTED:acquisition" in b for b in row["blockers"])

    def test_missing_acquisition_downgrades_money_qualified(self):
        row = _row(sport="NBA", prop_type="POINTS", label="MONEY_QUALIFIED")
        changed = enforce_route_completion(row)
        assert changed is True
        assert row["terminal_label"] == DOWNGRADE_LABEL

    def test_missing_acquisition_downgrades_market_verified_hold(self):
        row = _row(sport="WNBA", prop_type="POINTS", label="MARKET_VERIFIED_HOLD")
        changed = enforce_route_completion(row)
        assert changed is True
        assert row["terminal_label"] == DOWNGRADE_LABEL

    def test_all_qualifying_labels_covered(self):
        for label in QUALIFYING_LABELS:
            row = _row(sport="MLB", label=label)
            changed = enforce_route_completion(row)
            assert changed is True, f"Expected downgrade for label={label}"

    def test_blocker_format(self):
        row = _row(sport="MLB", label="FINAL_APPROVED")
        enforce_route_completion(row)
        blocker_text = " ".join(row["blockers"])
        assert "REQUIRED_GATE_NOT_EXECUTED:" in blocker_text

    def test_multiple_missing_gates_all_blocked(self):
        row = _row(sport="MLB", label="MONEY_QUALIFIED", gates={})
        enforce_route_completion(row)
        # Should have one blocker per missing gate
        route_blockers = [b for b in row["blockers"] if "REQUIRED_GATE_NOT_EXECUTED" in b]
        assert len(route_blockers) >= len(UNIVERSAL_REQUIRED_GATES)

    def test_route_completion_key_stamped(self):
        row = _row(sport="MLB", label="FINAL_APPROVED")
        enforce_route_completion(row)
        assert "route_completion" in row["gates"]
        rc = row["gates"]["route_completion"]
        assert rc["passed"] is False
        assert rc["original_label"] == "FINAL_APPROVED"
        assert rc["enforced_ceiling"] == DOWNGRADE_LABEL
        assert "acquisition" in rc["missing_gates"]

    def test_1ip_missing_calibration_health_downgrades(self):
        gates = {k: {"passed": True} for k in UNIVERSAL_REQUIRED_GATES | {"acquisition"}}
        row = _row(sport="MLB", prop_type="1IP_PITCHES_THROWN", label="FINAL_APPROVED", gates=gates)
        changed = enforce_route_completion(row)
        assert changed is True
        assert any("calibration_health" in b for b in row["blockers"])

    def test_1ip_with_all_gates_not_downgraded(self):
        required = get_required_gates({"sport": "MLB", "prop_type": "1IP_PITCHES_THROWN"})
        gates = {k: {"passed": True} for k in required}
        row = _row(sport="MLB", prop_type="1IP_PITCHES_THROWN", label="FINAL_APPROVED", gates=gates)
        changed = enforce_route_completion(row)
        assert changed is False

    def test_only_lowers_label_never_raises(self):
        """Rows below QUALIFYING_LABELS are untouched."""
        for label in ["RESEARCH_INTEREST", "MODEL_QUALIFIED_HOLD", "REJECT_NO_EDGE",
                      "REJECT_DATA_QUALITY", "SLATE_PURGE", "NO_PLAY"]:
            row = _row(sport="MLB", label=label, gates={})
            original = row["terminal_label"]
            enforce_route_completion(row)
            assert row["terminal_label"] == original


# ---------------------------------------------------------------------------
# build_row_execution_trace
# ---------------------------------------------------------------------------

class TestBuildRowExecutionTrace:
    def test_basic_fields_present(self):
        gates = {k: {"passed": True} for k in UNIVERSAL_REQUIRED_GATES | {"acquisition"}}
        row = _row(sport="MLB", prop_type="STRIKEOUTS", label="FINAL_APPROVED", gates=gates)
        trace = build_row_execution_trace(row)
        assert trace["row_id"] == "r1"
        assert trace["sport"] == "MLB"
        assert trace["terminal_label"] == "FINAL_APPROVED"
        assert "gates_ran" in trace
        assert "gates_passed" in trace
        assert "gates_failed" in trace
        assert "required_missing" in trace
        assert "route_complete" in trace

    def test_complete_route_flagged(self):
        required = get_required_gates({"sport": "MLB", "prop_type": "STRIKEOUTS"})
        gates = {k: {"passed": True} for k in required}
        row = _row(sport="MLB", label="FINAL_APPROVED", gates=gates)
        trace = build_row_execution_trace(row)
        assert trace["route_complete"] is True
        assert trace["required_missing"] == []

    def test_incomplete_route_flagged(self):
        row = _row(sport="MLB", label="FINAL_APPROVED", gates=_full_gates())
        trace = build_row_execution_trace(row)
        assert trace["route_complete"] is False
        assert "acquisition" in trace["required_missing"]

    def test_route_downgraded_flag_set(self):
        row = _row(sport="MLB", label="FINAL_APPROVED")
        enforce_route_completion(row)
        trace = build_row_execution_trace(row)
        assert trace["route_downgraded"] is True
        assert trace["original_label_before_route_enforcement"] == "FINAL_APPROVED"

    def test_route_completion_gate_excluded_from_gates_ran(self):
        row = _row(sport="MLB", label="FINAL_APPROVED")
        enforce_route_completion(row)
        trace = build_row_execution_trace(row)
        assert "route_completion" not in trace["gates_ran"]
