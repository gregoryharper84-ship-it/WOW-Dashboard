"""Tests for Module F: failure_path.py"""
import pytest
from gate_engine.failure_path import run, PRIMARY_HIGH_PROBABILITY_FLOOR, DOUBLE_PATH_DOWNGRADE_FLOOR
from gate_engine.labels import PropLabel


def _row():
    return {"blockers": [], "gates": {}, "terminal_label": None}


def _full_matrix(primary_floor=20, secondary_floor=15, include_model_adj=True):
    adj = "-3% applied to model_prob" if include_model_adj else ""
    return {
        "failure_path_matrix": {
            "PRIMARY_KILL_PATH": {
                "scenario":         "Blowout substitution in 3Q",
                "probability_band": f"{primary_floor}–{primary_floor + 10}%",
                "model_adjustment": adj,
                "evidence":         "L5 shows 2 DNP risk games",
            },
            "SECONDARY_KILL_PATH": {
                "scenario":         "Foul trouble limiting minutes",
                "probability_band": f"{secondary_floor}–{secondary_floor + 5}%",
                "model_adjustment": "-2% applied",
                "evidence":         "3.2 fouls per game L10",
            },
            "BLACK_SWAN_PATH": {
                "scenario":         "Late injury scratch",
                "probability_band": "2–5%",
                "model_adjustment": "void risk — no adjustment",
                "evidence":         "Pre-game injury history noted",
                "void_dnp_risk":    "Yes",
            },
        }
    }


class TestFullPathsPass:
    def test_complete_matrix_passes(self):
        row = _row()
        result = run(row, _full_matrix())
        assert result["passed"] is True
        assert result["code"] == "FAILURE_PATH_OK"
        assert result["paths_missing"] == []
        assert result["paths_abstract"] == []

    def test_all_paths_present(self):
        row = _row()
        result = run(row, _full_matrix())
        assert len(result["paths_present"]) == 3


class TestMissingPaths:
    def test_missing_primary_fails(self):
        row = _row()
        enr = _full_matrix()
        del enr["failure_path_matrix"]["PRIMARY_KILL_PATH"]
        result = run(row, enr)
        assert result["passed"] is False
        assert "PRIMARY_KILL_PATH" in result["paths_missing"]
        assert row["terminal_label"] == PropLabel.DATA_CONTRACT_FAIL.value

    def test_missing_secondary_fails(self):
        row = _row()
        enr = _full_matrix()
        del enr["failure_path_matrix"]["SECONDARY_KILL_PATH"]
        result = run(row, enr)
        assert "SECONDARY_KILL_PATH" in result["paths_missing"]

    def test_missing_black_swan_fails(self):
        row = _row()
        enr = _full_matrix()
        del enr["failure_path_matrix"]["BLACK_SWAN_PATH"]
        result = run(row, enr)
        assert "BLACK_SWAN_PATH" in result["paths_missing"]

    def test_no_matrix_at_all_fails(self):
        row = _row()
        result = run(row, {})
        assert result["passed"] is False
        assert len(result["paths_missing"]) == 3

    def test_data_contract_fail_set_on_missing(self):
        row = _row()
        run(row, {})
        assert row["terminal_label"] == PropLabel.DATA_CONTRACT_FAIL.value


class TestAbstractPaths:
    def test_placeholder_paths_fail(self):
        row = _row()
        enr = {
            "failure_path_matrix": {
                "PRIMARY_KILL_PATH":   {"scenario": "failure paths reviewed",
                                        "probability_band": "n/a",
                                        "model_adjustment": "n/a", "evidence": "n/a"},
                "SECONDARY_KILL_PATH": {"scenario": "tbd", "probability_band": "tbd",
                                        "model_adjustment": "tbd", "evidence": "tbd"},
                "BLACK_SWAN_PATH":     {"scenario": "see above",
                                        "probability_band": "n/a",
                                        "model_adjustment": "n/a", "evidence": "n/a"},
            }
        }
        result = run(row, enr)
        assert result["passed"] is False
        assert len(result["paths_abstract"]) >= 1
        assert result["code"] == "FAILURE_PATH_DATA_CONTRACT_FAIL"


class TestHighProbabilityHaircut:
    def test_primary_floor_over_30_requires_haircut(self):
        row = _row()
        enr = _full_matrix(primary_floor=35, include_model_adj=False)
        result = run(row, enr)
        assert result["primary_requires_haircut"] is True
        assert result["passed"] is False

    def test_primary_floor_over_30_with_adj_passes(self):
        row = _row()
        enr = _full_matrix(primary_floor=35, include_model_adj=True)
        result = run(row, enr)
        assert result["primary_requires_haircut"] is True
        assert result["passed"] is True

    def test_primary_floor_at_30_no_haircut(self):
        row = _row()
        enr = _full_matrix(primary_floor=PRIMARY_HIGH_PROBABILITY_FLOOR)
        result = run(row, enr)
        assert result["primary_requires_haircut"] is False


class TestDoublePathDowngrade:
    def test_two_paths_above_20_triggers_downgrade(self):
        row = _row()
        enr = _full_matrix(primary_floor=25, secondary_floor=22)
        result = run(row, enr)
        assert result["double_path_downgrade"] is True
        assert result["tier_downgrade"] is True

    def test_one_path_above_20_no_double_downgrade(self):
        row = _row()
        enr = _full_matrix(primary_floor=25, secondary_floor=10)
        result = run(row, enr)
        assert result["double_path_downgrade"] is False


class TestRoleMinutesSignal:
    def test_blowout_sub_triggers_role_signal(self):
        row = _row()
        enr = _full_matrix()
        result = run(row, enr)
        assert result["role_minutes_signal"] is True   # primary has "blowout sub"

    def test_no_role_keywords_no_signal(self):
        row = _row()
        enr = {
            "failure_path_matrix": {
                "PRIMARY_KILL_PATH": {
                    "scenario": "Opponent zone defense suppresses assists",
                    "probability_band": "15–20%",
                    "model_adjustment": "-2% applied",
                    "evidence": "L5 matchup data",
                },
                "SECONDARY_KILL_PATH": {
                    "scenario": "Bad weather slows pace",
                    "probability_band": "10–15%",
                    "model_adjustment": "-1% applied",
                    "evidence": "Weather API",
                },
                "BLACK_SWAN_PATH": {
                    "scenario": "Forfeit / postponement",
                    "probability_band": "1–2%",
                    "model_adjustment": "void",
                    "evidence": "Historical rate",
                    "void_dnp_risk": "Yes",
                },
            }
        }
        result = run(row, enr)
        assert result["role_minutes_signal"] is False
