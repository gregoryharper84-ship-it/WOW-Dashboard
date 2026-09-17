from pathlib import Path


SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "v17"
    / "sql"
    / "20260917_v17_mlb_publication_deadlock_repair.sql"
)
SQL = SQL_PATH.read_text()


def test_repair_removes_only_global_publication_prerequisite_from_hydrator():
    assert "wow_v17_hydrate_mlb_event_governance_evidence" in SQL
    assert "or not coalesce((deployment->>''probability_publishable'')::boolean,false)" in SQL
    assert "replace(ddl, needle, '')" in SQL
    assert "governed_probability_capability" in SQL
    assert "MLB_GOVERNED_DEPLOYMENT_CAPABILITY_UNAVAILABLE" in SQL


def test_repair_is_self_verifying_and_execution_invariant_is_retained():
    assert "V17_REPAIR_VERIFY_FAILED: global publication dependency still present" in SQL
    assert "V17_REPAIR_VERIFY_FAILED: capability gate missing" in SQL
    assert "V17_REPAIR_VERIFY_FAILED: execution invariant missing" in SQL
    assert "can_execute" in SQL


def test_repair_is_idempotent():
    assert "idempotent: already repaired" in SQL
    assert "V17_REPAIR_SIGNATURE_MISMATCH" in SQL
