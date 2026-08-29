from pathlib import Path


def test_prop_evidence_table_is_backend_only_and_rls_protected():
    sql = Path("prop_runtime_contract.sql").read_text().lower()
    assert "alter table public.wow_prop_evidence_snapshots enable row level security" in sql
    assert "revoke all on table public.wow_prop_evidence_snapshots from anon, authenticated" in sql
    assert "grant all on table public.wow_prop_evidence_snapshots to service_role" in sql
    assert "revoke all on function public.wow_prop_evidence_snapshot" in sql
    assert "grant execute on function public.wow_prop_evidence_snapshot" in sql


def test_prop_registry_never_activates_capability_by_itself():
    sql = Path("prop_fitted_model_registry.sql").read_text()
    lower = sql.lower()
    assert "wow_prop_fitted_model_artifacts" in lower
    assert "wow_prop_certified_model_artifact" in lower
    assert "wow_prop_fitted_model_v1" in lower
    assert "probability_publishable boolean not null default false" in lower
    assert "can_execute boolean not null default false" in lower
    assert "capability_status = 'unavailable'" in lower
    assert "no_certified_prop_model_artifact" in lower
