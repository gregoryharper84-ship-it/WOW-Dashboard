from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "v17" / "sql" / "20260922_v17_event_status_alias_canonicalization.sql"
SQL = SQL_PATH.read_text(encoding="utf-8")
NORMALIZED = " ".join(SQL.upper().split())


def test_pregame_aliases_normalize_to_existing_canonical_status():
    assert "UPPER(COALESCE(NEW.EVENT_STATUS,'')) IN ('PRE-GAME','PREGAME')" in NORMALIZED
    assert "NEW.EVENT_STATUS := 'SCHEDULED'" in NORMALIZED


def test_strict_event_status_constraint_is_not_weakened():
    assert "DROP CONSTRAINT" not in NORMALIZED
    assert "ALTER TABLE PUBLIC.WOW_EVENT_PREDICTIONS" not in NORMALIZED


def test_existing_governance_fail_closed_behavior_is_preserved():
    assert "NEW.CONTROLLING_SPECIALIST := V_SPECIALIST" in NORMALIZED
    assert "NEW.GOVERNED_PROBABILITY_CAPABILITY := V_CAPABILITY" in NORMALIZED
    assert "NEW.RANK_ELIGIBLE := FALSE" in NORMALIZED
    assert "NEW.PROBABILITY_PUBLISHABLE := FALSE" in NORMALIZED
    assert "CAN_EXECUTE := TRUE" not in NORMALIZED
