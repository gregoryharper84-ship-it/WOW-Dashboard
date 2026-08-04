"""
Tests for gate_engine/wnba_enrichment_contract.py

Validates that the WNBA enrichment type contract correctly distinguishes:
  game_log      → list[number]   (L5/L10 ledger)
  box_score_log → list[dict]     (WNBA opportunity engine)

and returns WNBA_ENRICHMENT_TYPE_MISMATCH when types are mixed.
"""
from __future__ import annotations
import pytest
from gate_engine.wnba_enrichment_contract import (
    validate,
    validate_or_raise,
    mismatch_response,
    ERROR_CODE,
)


class TestValidate:
    def test_empty_enrichment_passes(self):
        ok, code, detail = validate({})
        assert ok is True
        assert code is None
        assert detail is None

    def test_none_enrichment_passes(self):
        ok, code, detail = validate(None)
        assert ok is True

    def test_correct_game_log_numbers_passes(self):
        ok, code, _ = validate({"game_log": [28, 32, 25, 30, 27]})
        assert ok is True
        assert code is None

    def test_correct_game_log_floats_passes(self):
        ok, code, _ = validate({"game_log": [28.0, 32.5, 25.0]})
        assert ok is True

    def test_correct_box_score_log_dicts_passes(self):
        ok, code, _ = validate({"box_score_log": [
            {"MIN": 31, "PTS": 17, "REB": 5, "AST": 3, "FGA": 12, "USG%": 28.4}
        ]})
        assert ok is True
        assert code is None

    def test_both_correct_passes(self):
        ok, code, _ = validate({
            "game_log":      [28, 32, 25, 30, 27],
            "box_score_log": [{"MIN": 31, "PTS": 17}],
        })
        assert ok is True

    def test_game_log_with_dicts_returns_mismatch(self):
        """game_log containing dicts → WNBA_ENRICHMENT_TYPE_MISMATCH."""
        ok, code, detail = validate({
            "game_log": [{"MIN": 31, "PTS": 17}, {"MIN": 28, "PTS": 22}]
        })
        assert ok is False
        assert code == ERROR_CODE
        assert "game_log" in detail
        assert "box_score_log" in detail.lower() or "dict" in detail

    def test_box_score_log_with_numbers_returns_mismatch(self):
        """box_score_log containing numbers → WNBA_ENRICHMENT_TYPE_MISMATCH."""
        ok, code, detail = validate({
            "box_score_log": [28, 32, 25, 30, 27]
        })
        assert ok is False
        assert code == ERROR_CODE
        assert "box_score_log" in detail
        assert "game_log" in detail.lower() or "number" in detail

    def test_game_log_not_a_list_returns_mismatch(self):
        ok, code, detail = validate({"game_log": "28,32,25"})
        assert ok is False
        assert code == ERROR_CODE
        assert "game_log" in detail

    def test_box_score_log_not_a_list_returns_mismatch(self):
        ok, code, detail = validate({"box_score_log": {"MIN": 31}})
        assert ok is False
        assert code == ERROR_CODE
        assert "box_score_log" in detail

    def test_empty_game_log_passes(self):
        """Empty list is technically valid — no elements to type-check."""
        ok, code, _ = validate({"game_log": []})
        assert ok is True

    def test_empty_box_score_log_passes(self):
        ok, code, _ = validate({"box_score_log": []})
        assert ok is True

    def test_both_wrong_reports_both_errors(self):
        """Both fields wrong → detail mentions both."""
        ok, code, detail = validate({
            "game_log":      [{"MIN": 31}],
            "box_score_log": [28, 32],
        })
        assert ok is False
        assert code == ERROR_CODE
        # Both field names should appear in the detail
        assert "game_log" in detail
        assert "box_score_log" in detail

    def test_unrelated_fields_are_ignored(self):
        """Enrichment fields other than game_log/box_score_log are not validated."""
        ok, code, _ = validate({
            "sportsbook_line": 27.5,
            "batting_average": 0.285,
            "injury_status":   "active",
        })
        assert ok is True


class TestValidateOrRaise:
    def test_correct_types_does_not_raise(self):
        validate_or_raise({
            "game_log":      [28, 32, 25],
            "box_score_log": [{"MIN": 31, "PTS": 17}],
        })  # no exception

    def test_type_mismatch_raises_value_error(self):
        with pytest.raises(ValueError) as exc_info:
            validate_or_raise({"game_log": [{"MIN": 31}]})
        assert ERROR_CODE in str(exc_info.value)

    def test_empty_enrichment_does_not_raise(self):
        validate_or_raise({})  # no exception


class TestMismatchResponse:
    def test_returns_ok_false(self):
        resp = mismatch_response("game_log must be list[number]")
        assert resp["ok"] is False

    def test_returns_error_code(self):
        resp = mismatch_response("some detail")
        assert resp["error_code"] == ERROR_CODE

    def test_returns_remediation_for_both_fields(self):
        resp = mismatch_response("some detail")
        fields = {r["field"] for r in resp["remediation"]}
        assert "game_log" in fields
        assert "box_score_log" in fields

    def test_each_remediation_has_required_keys(self):
        resp = mismatch_response("test")
        for item in resp["remediation"]:
            assert "field" in item
            assert "required_for" in item
            assert "accepted_format" in item
            assert "resubmission_key" in item


class TestModelRegistryProvisionalCeiling:
    """Verify model_registry.py now includes provisional_ceiling on PROVISIONAL entries."""

    def test_provisional_entry_has_ceiling(self):
        from gate_engine.model_registry import lookup
        entry = lookup("NBA", "PTS")
        assert entry["status"] == "PROVISIONAL"
        assert "provisional_ceiling" in entry
        pc = entry["provisional_ceiling"]
        assert pc["maximum_label"] == "MODEL_QUALIFIED_HOLD"
        assert pc["power_eligibility"] is False
        assert pc["money_grade_allowed"] is False

    def test_active_entry_has_no_ceiling(self):
        from gate_engine.model_registry import lookup
        entry = lookup("MLB", "H", line=0.5)
        assert entry["status"] == "ACTIVE"
        assert "provisional_ceiling" not in entry

    def test_no_registered_model_has_no_ceiling(self):
        """The NO_REGISTERED_MODEL sentinel carries no provisional_ceiling.
        Uses NHL (unregistered) — NFL now has PROVISIONAL models."""
        from gate_engine.model_registry import lookup
        entry = lookup("NHL", "G")
        assert entry["status"] == "NO_REGISTERED_MODEL"
        assert "provisional_ceiling" not in entry

    def test_wnba_provisional_has_ceiling(self):
        from gate_engine.model_registry import lookup
        entry = lookup("WNBA", "PTS")
        assert "provisional_ceiling" in entry

    def test_mlb_provisional_has_ceiling(self):
        from gate_engine.model_registry import lookup
        entry = lookup("MLB", "SO")
        assert entry["status"] == "PROVISIONAL"
        assert "provisional_ceiling" in entry
