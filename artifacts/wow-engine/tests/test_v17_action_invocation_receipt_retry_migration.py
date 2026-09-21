from pathlib import Path


SQL = (Path(__file__).parents[1] / "migrations" / "20260921_action_invocation_receipt_retry_queue.sql").read_text()


def test_receipt_retry_queue_is_idempotent_and_execution_disabled():
    normalized = " ".join(SQL.split())
    assert "wow_action_invocation_receipt_dead_letters" in SQL
    assert "invocation_id uuid primary key" in normalized
    assert "on conflict (invocation_id) do nothing" in normalized
    assert "can_execute boolean not null default false check (can_execute = false)" in normalized
    assert "wow_recover_action_invocation_receipt_dead_letters" in SQL
    assert "for update skip locked" in normalized
    assert "wow-action-invocation-receipt-recovery" in SQL


def test_dead_letter_payload_is_telemetry_envelope_not_probability_authority():
    lower = SQL.lower()
    normalized = " ".join(SQL.split())
    assert "receipt_payload jsonb" in lower
    assert "can_execute boolean not null default false check (can_execute = false)" in normalized
    for forbidden_field in (
        "model_probability",
        "raw_probability",
        "calibrated_probability",
        "calibrated_lower_bound",
        "calibrated_upper_bound",
    ):
        assert forbidden_field not in lower
