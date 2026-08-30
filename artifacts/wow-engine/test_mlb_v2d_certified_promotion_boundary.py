from pathlib import Path


MIGRATION = Path(__file__).parent / "migrations" / "20260830_mlb_v2d_certified_promotion_boundary.sql"


def test_research_v2d_cannot_promote_runtime_capability_available():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "MLB_CERTIFIED_EVENT_ARTIFACT_UNAVAILABLE" in sql
    assert "required_next_gate" in sql
    assert "CERTIFIED_MLB_EVENT_FITTED_ARTIFACT_AND_EVENT_CALIBRATOR" in sql
    assert "runtime_capability_status', 'UNAVAILABLE'" in sql
    assert "probability_publishable', false" in sql
    assert "can_execute', false" in sql
    # This hardened function must not contain the former promotion mutation.
    assert "set capability_status='AVAILABLE'" not in sql
    assert 'set capability_status = \'AVAILABLE\'' not in sql
