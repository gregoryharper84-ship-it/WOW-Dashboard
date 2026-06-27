"""Tests for Module E: role_timestamp.py"""
from datetime import datetime, timezone, timedelta
import pytest
from gate_engine.role_timestamp import run, FRESH_THRESHOLD, RECHECK_THRESHOLD
from gate_engine.labels import PropLabel


def _now():
    return datetime.now(timezone.utc)


def _ts(minutes_ago: float) -> str:
    dt = _now() - timedelta(minutes=minutes_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _row():
    return {"blockers": [], "gates": {}, "terminal_label": None}


class TestRoleTimestampFresh:
    def test_fresh_role_and_status_passes(self):
        row = _row()
        enr = {
            "role_timestamp":   _ts(30),
            "status_timestamp": _ts(20),
        }
        result = run(row, enr, now=_now())
        assert result["passed"] is True
        assert result["role_staleness"] == "FRESH"
        assert result["status_staleness"] == "FRESH"
        assert result["ceiling"] is None
        assert result["code"] == "ROLE_TIMESTAMP_FRESH"
        assert row["blockers"] == []

    def test_exactly_at_fresh_threshold_passes(self):
        row = _row()
        enr = {
            "role_timestamp":   _ts(FRESH_THRESHOLD - 1),
            "status_timestamp": _ts(FRESH_THRESHOLD - 1),
        }
        result = run(row, enr, now=_now())
        assert result["role_staleness"] == "FRESH"
        assert result["ceiling"] is None


class TestRoleTimestampRecheck:
    def test_role_91_min_is_recheck(self):
        row = _row()
        enr = {
            "role_timestamp":   _ts(91),
            "status_timestamp": _ts(30),
        }
        result = run(row, enr, now=_now())
        assert result["role_staleness"] == "RECHECK"
        assert result["code"] == "ROLE_TIMESTAMP_RECHECK"
        assert result["ceiling"] is None
        assert any("RECHECK" in b for b in row["blockers"])

    def test_recheck_does_not_set_terminal_label(self):
        row = _row()
        enr = {
            "role_timestamp":   _ts(100),
            "status_timestamp": _ts(30),
        }
        run(row, enr, now=_now())
        assert row["terminal_label"] is None


class TestRoleTimestampStale:
    def test_role_121_min_is_stale(self):
        row = _row()
        enr = {
            "role_timestamp":   _ts(121),
            "status_timestamp": _ts(30),
        }
        result = run(row, enr, now=_now())
        assert result["role_staleness"] == "STALE"
        assert result["ceiling"] == PropLabel.MODEL_QUALIFIED_HOLD.value
        assert result["code"] == "ROLE_TIMESTAMP_STALE"
        assert any("STALE" in b for b in row["blockers"])

    def test_status_stale_caps_even_if_role_fresh(self):
        row = _row()
        enr = {
            "role_timestamp":   _ts(30),
            "status_timestamp": _ts(150),
        }
        result = run(row, enr, now=_now())
        assert result["status_staleness"] == "STALE"
        assert result["ceiling"] == PropLabel.MODEL_QUALIFIED_HOLD.value

    def test_label_ceiling_set_on_row(self):
        row = _row()
        enr = {
            "role_timestamp":   _ts(200),
            "status_timestamp": _ts(200),
        }
        run(row, enr, now=_now())
        assert row.get("label_ceiling") == PropLabel.MODEL_QUALIFIED_HOLD.value


class TestRoleTimestampUnknown:
    def test_missing_role_timestamp_is_unknown(self):
        row = _row()
        enr = {"status_timestamp": _ts(30)}
        result = run(row, enr, now=_now())
        assert result["role_staleness"] == "UNKNOWN"
        assert result["ceiling"] == PropLabel.MODEL_QUALIFIED_HOLD.value
        assert result["code"] == "ROLE_TIMESTAMP_UNKNOWN"

    def test_both_missing_is_unknown(self):
        row = _row()
        result = run(row, {}, now=_now())
        assert result["role_staleness"] == "UNKNOWN"
        assert result["status_staleness"] == "UNKNOWN"
        assert result["ceiling"] is not None

    def test_age_override_used_when_provided(self):
        row = _row()
        enr = {
            "role_confirmation_age_minutes": 45,
            "status_timestamp": _ts(30),
        }
        result = run(row, enr, now=_now())
        assert result["role_confirmation_age_minutes"] == 45.0
        assert result["role_staleness"] == "FRESH"


class TestTeammateTimestamp:
    def test_no_teammate_ts_is_na(self):
        row = _row()
        enr = {
            "role_timestamp":   _ts(30),
            "status_timestamp": _ts(30),
        }
        result = run(row, enr, now=_now())
        assert result["teammate_staleness"] == "N/A"

    def test_teammate_ts_graded(self):
        row = _row()
        enr = {
            "role_timestamp":                   _ts(30),
            "status_timestamp":                 _ts(30),
            "primary_teammate_status_timestamp": _ts(50),
        }
        result = run(row, enr, now=_now())
        assert result["teammate_staleness"] in ("FRESH", "RECHECK")
