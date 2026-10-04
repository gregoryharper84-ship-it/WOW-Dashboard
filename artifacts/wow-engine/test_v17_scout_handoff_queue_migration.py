from pathlib import Path


ROOT = Path(__file__).resolve().parent
MIGRATION = ROOT / "migrations" / "20261003_v17_scout_handoff_queue.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_scout_queue_is_private_non_executable_and_lease_claimed():
    sql = _sql()
    assert "can_execute boolean not null default false check (can_execute = false)" in sql
    assert "alter table public.wow_scout_handoff_jobs enable row level security;" in sql
    assert "alter table public.wow_scout_handoff_state_events enable row level security;" in sql
    assert "revoke all on table public.wow_scout_handoff_jobs from public, anon, authenticated;" in sql
    assert "grant select, insert, update on table public.wow_scout_handoff_jobs to service_role;" in sql
    assert "security invoker" in sql.lower()
    assert "for update of j skip locked" in sql.lower()


def test_scout_queue_persists_required_lifecycle_and_row_block_terminal():
    sql = _sql()
    for state in (
        "DISCOVERED",
        "RESEARCH_INTEREST",
        "RED_TEAM_PASSED",
        "SPECIALIST_HANDOFF_QUEUED",
        "SPECIALIST_PROCESSING",
        "MODEL_EVALUATED",
        "V17_QUALIFIED",
        "HANDOFF_BLOCKED",
    ):
        assert state in sql
    assert "DETERMINISTIC_HANDOFF_HYGIENE_PASS" in sql
    assert "SPECIALIST_MODEL_EVALUATED" in sql
    assert "V17_GOVERNED_ADMISSION_PROVEN" in sql


def test_batch_enqueue_is_one_database_rpc_surface_not_per_candidate_http():
    sql = _sql()
    assert "wow_enqueue_scout_handoff_batch(p_jobs jsonb)" in sql
    assert "jsonb_array_elements(p_jobs)" in sql
    assert "wow_enqueue_scout_handoff_job(" in sql
    assert "'candidate_jobs', v_total" in sql
    assert "'handoff_blocked', v_blocked" in sql


def test_claim_reclaims_only_expired_processing_lease_and_records_recovery():
    sql = _sql()
    assert "j.current_state='SPECIALIST_PROCESSING'" in sql
    assert "j.lease_expires_at is not null" in sql
    assert "j.lease_expires_at <= now()" in sql
    assert "SPECIALIST_LEASE_RECLAIMED" in sql
    assert "previous_worker_id" in sql


def test_database_revalidates_probability_and_red_team_envelopes():
    sql = _sql()
    assert "SCOUT_PROBABILITY_AUTHORITY_VIOLATION" in sql
    assert "SCOUT_HANDOFF_RED_TEAM_RULE_UNVERIFIED" in sql
    assert "SCOUT_HANDOFF_RED_TEAM_ENVELOPE_CONTRADICTORY" in sql
    assert "SCOUT_HANDOFF_BLOCK_CODE_REQUIRED" in sql


def test_expired_lease_reclaim_is_bounded_and_retry_safe_only():
    sql = _sql()
    assert "SCOUT_HANDOFF_AMBIGUOUS_RETRY_PROHIBITED" in sql
    assert "SCOUT_HANDOFF_RETRY_EXHAUSTED" in sql
    assert "lease_reclaim_budget" in sql
    assert "r.attempt_count >= 2" in sql
    assert "upper(coalesce(r.request_payload->>'sport',''))='MLB'" in sql


def test_database_derives_v17_qualification_from_receipt_not_caller_boolean():
    sql = _sql()
    assert "wow_finish_scout_handoff_job(\n    p_job_id uuid,\n    p_worker_id text,\n    p_specialist_receipt jsonb\n)" in sql
    assert "p_v17_qualified" not in sql
    assert "v_v17_qualified" in sql
    assert "card_admission_eligible','false'" in sql
