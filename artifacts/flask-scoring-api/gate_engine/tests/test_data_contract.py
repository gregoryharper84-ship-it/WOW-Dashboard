"""Tests for Module B: data_contract.py"""
import pytest
from gate_engine.data_contract import run, check_fields_present, ROW_REQUIRED_FIELDS, ENRICHMENT_REQUIRED_FIELDS
from gate_engine.labels import PropLabel


def _base_row():
    return {
        "player":    "LeBron James",
        "sport":     "NBA",
        "prop_type": "points",
        "line":      25.5,
        "direction": "MORE",
        "blockers":  [],
        "gates":     {},
        "terminal_label": None,
    }


def _full_enrichment():
    return {
        "opponent":                  "GSW",
        "game_date":                 "2026-06-27",
        "book_or_platform":          "PrizePicks",
        "odds_or_payout":            3.0,
        "data_timestamp":            "2026-06-27T12:00:00Z",
        "status_timestamp":          "2026-06-27T11:30:00Z",
        "role_timestamp":            "2026-06-27T11:45:00Z",
        "l5_values":                 [28, 30, 22, 26, 31],
        "l10_values":                [28, 30, 22, 26, 31, 24, 27, 29, 25, 33],
        "l10_median":                27.5,
        "l10_mean":                  27.5,
        "l5_line_used":              25.5,
        "market_no_vig_probability": 0.54,
        "model_probability_ledger":  {"final_model_prob": 0.57},
        "payout_context":            {"intended_format": "3-pick Power"},
        "failure_path_matrix":       {"PRIMARY_KILL_PATH": {}},
        "directional_exposure_tags": ["fast_pace_over"],
        "provisional_label":         "WATCH",
        "validation_status":         "PENDING",
        "blocker_reason_if_blocked": None,
    }


class TestContractPass:
    def test_all_fields_present_passes(self):
        row = _base_row()
        result = run(row, _full_enrichment())
        assert result["passed"] is True
        assert result["code"] == "CONTRACT_PASS"
        assert result["missing_fields"] == []
        assert row["terminal_label"] is None

    def test_team_accepted_instead_of_player(self):
        row = _base_row()
        row.pop("player")
        row["team"] = "LAL"
        result = run(row, _full_enrichment())
        assert "player" not in result["missing_fields"]

    def test_market_unavailable_sentinel_accepted(self):
        enr = _full_enrichment()
        enr["market_no_vig_probability"] = "MARKET_UNAVAILABLE"
        row = _base_row()
        result = run(row, enr)
        assert result["passed"] is True

    def test_source_conflict_sentinel_accepted(self):
        enr = _full_enrichment()
        enr["market_no_vig_probability"] = "SOURCE_CONFLICT"
        row = _base_row()
        result = run(row, enr)
        assert result["passed"] is True

    def test_blocker_reason_optional_when_not_failed(self):
        enr = _full_enrichment()
        enr["validation_status"] = "PENDING"
        enr["blocker_reason_if_blocked"] = None
        row = _base_row()
        result = run(row, enr)
        assert "blocker_reason_if_blocked" not in result["missing_fields"]


class TestContractFail:
    def test_missing_row_field_fails(self):
        row = _base_row()
        row["player"] = None
        result = run(row, _full_enrichment())
        assert result["passed"] is False
        assert "player" in result["missing_fields"]
        assert row["terminal_label"] == PropLabel.DATA_CONTRACT_FAIL.value

    def test_missing_sport_fails(self):
        row = _base_row()
        row["sport"] = ""
        result = run(row, _full_enrichment())
        assert result["passed"] is False
        assert "sport" in result["missing_fields"]

    def test_missing_enrichment_field_fails(self):
        row = _base_row()
        enr = _full_enrichment()
        del enr["opponent"]
        result = run(row, enr)
        assert result["passed"] is False
        assert "opponent" in result["missing_fields"]

    def test_missing_l5_values_fails(self):
        row = _base_row()
        enr = _full_enrichment()
        enr["l5_values"] = None
        result = run(row, enr)
        assert result["passed"] is False
        assert "l5_values" in result["missing_fields"]

    def test_missing_multiple_fields_lists_all(self):
        row = _base_row()
        enr = _full_enrichment()
        del enr["l5_values"]
        del enr["l10_values"]
        del enr["opponent"]
        result = run(row, enr)
        assert len(result["missing_fields"]) >= 3
        assert "l5_values" in result["missing_fields"]
        assert "l10_values" in result["missing_fields"]
        assert "opponent" in result["missing_fields"]

    def test_blocker_appended_on_fail(self):
        row = _base_row()
        enr = _full_enrichment()
        del enr["game_date"]
        run(row, enr)
        assert any("DATA_CONTRACT_FAIL" in b for b in row["blockers"])

    def test_blocker_reason_required_when_validation_failed(self):
        row = _base_row()
        enr = _full_enrichment()
        enr["validation_status"] = "FAILED"
        enr["blocker_reason_if_blocked"] = None
        result = run(row, enr)
        assert "blocker_reason_if_blocked" in result["missing_fields"]

    def test_empty_string_fields_count_as_missing(self):
        row = _base_row()
        enr = _full_enrichment()
        enr["game_date"] = "   "
        result = run(row, enr)
        assert "game_date" in result["missing_fields"]


class TestCheckFieldsPresent:
    def test_all_present_returns_empty(self):
        prop = {"a": 1, "b": "x", "c": [1, 2]}
        assert check_fields_present(prop, ["a", "b", "c"]) == []

    def test_missing_field_returned(self):
        prop = {"a": 1}
        missing = check_fields_present(prop, ["a", "b"])
        assert "b" in missing

    def test_none_value_is_missing(self):
        prop = {"a": None}
        assert "a" in check_fields_present(prop, ["a"])
