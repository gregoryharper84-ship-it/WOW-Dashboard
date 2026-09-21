from pathlib import Path


def test_pick_request_state_is_service_role_only_and_non_executable():
    root = Path(__file__).resolve().parents[1]
    sql = (root / "migrations" / "20260920_pick_request_durable_run_state.sql").read_text()
    lowered = sql.lower()

    assert "create table if not exists public.wow_pick_request_runs" in lowered
    assert "create table if not exists public.wow_pick_request_row_states" in lowered
    assert "create table if not exists public.wow_pick_request_row_transitions" in lowered
    assert lowered.count("enable row level security") == 3
    assert "revoke all on table public.wow_pick_request_runs from public, anon, authenticated" in lowered
    assert "revoke all on table public.wow_pick_request_row_states from public, anon, authenticated" in lowered
    assert "revoke all on table public.wow_pick_request_row_transitions from public, anon, authenticated" in lowered
    assert "grant select, insert, update on table public.wow_pick_request_runs to service_role" in lowered
    assert "grant select, insert, update on table public.wow_pick_request_row_states to service_role" in lowered
    assert "grant select, insert on table public.wow_pick_request_row_transitions to service_role" in lowered
    assert lowered.count("check (can_execute = false)") == 3
    assert "prediction_id uuid references public.wow_predictions(prediction_id)" in lowered


def test_pick_request_state_machine_is_explicit_and_monotonic():
    root = Path(__file__).resolve().parents[1]
    sql = (root / "migrations" / "20260920_pick_request_durable_run_state.sql").read_text()
    for stage in (
        "INGESTED",
        "IDENTITY_VERIFIED",
        "MODEL_INPUTS_READY",
        "MODEL_COMPUTED",
        "RECEIPT_PERSISTED",
        "GOVERNANCE_AUDITED",
        "PUBLICATION_AUTHORIZED",
    ):
        assert f"'{stage}'" in sql
    assert "stage_seq between 0 and 6" in sql
    assert "unique (run_id, row_key, stage_seq)" in sql
