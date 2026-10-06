from pathlib import Path


ROOT = Path(__file__).resolve().parent
MIGRATION = ROOT / "migrations/20261006_v17_reliability_receipts.sql"


def test_reliability_receipt_ledger_is_append_only_and_fail_closed():
    text = MIGRATION.read_text()
    assert "create table if not exists public.wow_v17_reliability_receipts" in text.lower()
    assert "before update or delete" in text.lower()
    assert "WOW_RELIABILITY_RECEIPTS_APPEND_ONLY" in text
    assert "dry_run_header_present boolean not null check (dry_run_header_present is true)" in text
    assert "can_execute_header_false boolean not null check (can_execute_header_false is true)" in text
    assert "can_execute boolean not null default false check (can_execute is false)" in text
    assert "raw_response_body_base64 text not null" in text
    assert "raw_response_headers_base64 text not null" in text
    assert "execution_trace_base64 text not null" in text
    assert "sentinel_signature text primary key check" in text
