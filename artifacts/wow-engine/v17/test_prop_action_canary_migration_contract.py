from pathlib import Path


def test_action_canary_ledger_is_service_role_only_and_non_execution():
    root = Path(__file__).resolve().parents[1]
    sql = (root / "migrations" / "20260917_prop_action_canary_receipts.sql").read_text()
    lowered = sql.lower()
    assert "enable row level security" in lowered
    assert "revoke all on table public.wow_prop_action_canary_receipts from public, anon, authenticated" in lowered
    assert "grant select, insert on table public.wow_prop_action_canary_receipts to service_role" in lowered
    assert "check (can_execute = false)" in lowered
    assert "references public.wow_predictions(prediction_id)" in lowered
    assert "reconciliation_status" in lowered and "'pass'" in lowered
    assert "scorewowpickrequest" in lowered
