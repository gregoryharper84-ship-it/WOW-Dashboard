"""Structural governance checks for proposed Gemini Postgres ledger migration."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SQL=(ROOT/"migrations"/"20261010_v17_gemini_budget_reservation_ledger.sql").read_text()

def test_no_budget_is_activated_by_migration():
    assert "enabled boolean NOT NULL DEFAULT false" in SQL
    assert "cap_microusd bigint NOT NULL DEFAULT 0" in SQL
    assert "max_single_request_microusd bigint NOT NULL DEFAULT 0" in SQL
    assert "CREATE OR REPLACE FUNCTION wow_gemini_internal.wow_gemini_reserve_spend" in SQL
    assert "CREATE OR REPLACE FUNCTION wow_gemini_internal.wow_gemini_record_spend_stage" in SQL
    assert "SECURITY DEFINER SET search_path = pg_catalog, pg_temp" in SQL
    assert "CREATE OR REPLACE FUNCTION public.wow_gemini_reserve_spend" in SQL
    assert "SECURITY INVOKER SET search_path = pg_catalog, pg_temp" in SQL

def test_reservation_has_atomic_control_lock_and_conflict_guard():
    assert "FOR UPDATE" in SQL
    assert "GEMINI_DUPLICATE_MANIFEST" in SQL
    assert "GEMINI_DAILY_BUDGET_EXCEEDED" in SQL
    assert "UNIQUE (project_key, manifest_sha256)" in SQL
    assert "GEMINI_REQUEST_BUDGET_EXCEEDED" in SQL
    assert "GEMINI_BUDGET_DISABLED" in SQL
    assert "can_execute=false" in SQL or "can_execute = false" in SQL

def test_no_anonymous_mutating_api_permissions():
    assert "REVOKE ALL ON public.wow_gemini_budget_controls FROM PUBLIC, anon, authenticated, service_role" in SQL
    assert "GRANT SELECT ON public.wow_gemini_budget_controls TO service_role" in SQL
    assert "GRANT EXECUTE ON FUNCTION public.wow_gemini_reserve_spend" in SQL
    assert "FROM PUBLIC, anon, authenticated" in SQL
    for table in ("wow_gemini_budget_controls", "wow_gemini_spend_reservations",
                  "wow_gemini_spend_events"):
        assert "GRANT INSERT ON public." + table not in SQL
        assert "GRANT UPDATE ON public." + table not in SQL
    assert "REVOKE ALL ON SCHEMA wow_gemini_internal FROM PUBLIC, anon, authenticated" in SQL
    assert "GRANT USAGE ON SCHEMA wow_gemini_internal TO service_role" in SQL

def test_append_only_receipts_and_typed_state_transitions():
    assert "gemini_events_no_update" not in SQL or "wow_gemini_events_guard" in SQL
    assert "BEFORE UPDATE OR DELETE OR TRUNCATE" in SQL
    assert "'OUTCOME_UNKNOWN'" in SQL and "'RECONCILED'" in SQL
    assert "GEMINI_STAGE_TRANSITION_REJECTED" in SQL
