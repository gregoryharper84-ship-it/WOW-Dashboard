from pathlib import Path


SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "v17"
    / "sql"
    / "20260903_v17_repeat_safe_source_attempts.sql"
)
SQL = SQL_PATH.read_text()


def test_shared_environment_attempt_order_is_monotonic_not_fixed_one():
    assert "wow_v17_hydrate_shared_environmental_evidence" in SQL
    assert "max(a.attempt_order),0)+1" in SQL
    assert "a.evidence_kind='WEATHER_STATUS'" in SQL
    assert "$q$1,v_now,'SUCCESS',v_url,false$q$" in SQL
    assert "$q$1,v_now,'ERROR',v_url,false$q$" in SQL
    assert "$q$1,v_now,'UNAVAILABLE',v_url,false$q$" in SQL


def test_canonical_hydrator_attempt_order_is_monotonic_per_kind():
    assert "wow_v17_hydrate_mlb_event_governance_evidence" in SQL
    assert "a.evidence_kind=k" in SQL
    assert "source_name,(select coalesce(max(a.attempt_order),0)+1" in SQL


def test_repeat_safe_patch_never_enables_execution():
    assert "can_execute" in SQL
    assert "can_execute=true" not in SQL
    assert "can_execute',true" not in SQL
