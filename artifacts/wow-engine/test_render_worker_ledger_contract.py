"""Contract tests for staged atomic Render deployment ledger."""
from pathlib import Path
import re

LEDGER = Path(__file__).parent / "v17/sql/render_worker_deploy_intent_staging.sql"

def test_ledger_is_private_and_row_locked():
    sql = LEDGER.read_text()
    assert "create schema if not exists wow_release_control" in sql
    assert "enable row level security" in sql
    assert "security invoker" in sql
    assert "for update" in sql
    assert "on conflict do nothing" in sql
    assert "revoke all on schema wow_release_control from public, anon, authenticated" in sql

def test_lease_fencing_rejects_stale_writer_and_unsafe_retries():
    sql = LEDGER.read_text()
    assert "lease_token=d.lease_token+1" in sql
    assert "WORKER_DEPLOY_FENCING_REJECTED" in sql
    assert "v.lease_token is distinct from p_lease_token" in sql
    assert "v.owner_run_id is distinct from p_owner_run_id" in sql
    assert "v.state = 'PENDING'" in sql
    assert "INITIATING/ACK_AMBIGUOUS to revert to PENDING" in sql
    assert "p_state='LIVE_VERIFIED'" in sql
    assert "WORKER_DEPLOY_LIVE_RECEIPT_MISSING" in sql

def test_only_allowlisted_worker_service_is_claimable():
    assert "p_service_id <> 'srv-dapkbc0473hc73c1978g'" in LEDGER.read_text()
