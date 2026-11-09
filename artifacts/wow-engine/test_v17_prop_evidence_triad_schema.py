"""Schema contract for durable prop triad reports before deployment.

These are source-level assertions, not a claim that a production database
has already applied the migration. Live schema parity must be proved separately.
"""
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parent
    / "migrations"
    / "20261109_prop_prediction_evidence_triad.sql"
)


def test_universal_forward_new_evidence_fields_have_a_schema_migration():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "alter table public.wow_predictions" in sql
    assert "add column if not exists model_evidence_triad jsonb" in sql
    assert "add column if not exists evidence_triad_required boolean" in sql
    assert "add column if not exists evidence_triad_status text" in sql
    assert "add column if not exists triad_blockers text[]" in sql


def test_incomplete_triad_is_durable_nonpublishable_not_hidden_in_a_fake_model_failure():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "chk_wow_predictions_incomplete_triad_not_publishable" in sql
    assert "probability_publishable = false" in sql
    assert "'incomplete'" in sql
    assert "'identity_unresolved'" in sql
    assert "'evidence_unavailable'" in sql
    assert "report_id" in sql
    assert "report_digest" in sql


def test_schema_change_is_additive_and_has_no_public_grant_or_historical_edit():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    # Comments mention prohibited operations but no SQL statements perform them.
    statements = [
        line.strip()
        for line in sql.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    assert not any(line.startswith(("grant ", "revoke ", "update ", "delete ", "truncate "))
                   for line in statements)
    assert "add column if not exists" in sql
