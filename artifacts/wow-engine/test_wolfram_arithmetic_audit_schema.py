from pathlib import Path


MIGRATION = Path(__file__).parent / "migrations" / "20260902_wolfram_arithmetic_audit_ledger.sql"


def test_wolfram_audit_ledger_is_prediction_bound_immutable_and_non_executable():
    sql = MIGRATION.read_text().lower()
    assert "references public.wow_predictions(prediction_id)" in sql
    assert "before update on public.wow_wolfram_arithmetic_audits" in sql
    assert "before delete on public.wow_wolfram_arithmetic_audits" in sql
    assert "check (blocks_model_probability = false)" in sql
    assert "check (can_execute = false)" in sql
    assert "enable row level security" in sql
    assert "revoke all on public.wow_wolfram_arithmetic_audits from anon, authenticated" in sql
    assert "revoke all on function public.wow_block_wolfram_arithmetic_audit_mutation()" in sql
    assert "grant select, insert on public.wow_wolfram_arithmetic_audits to service_role" in sql
    assert "revoke update, delete, truncate on public.wow_wolfram_arithmetic_audits from service_role" in sql


def test_wolfram_audit_ledger_accepts_only_governed_provider_verdicts():
    sql = MIGRATION.read_text()
    for verdict in (
        "PASS",
        "WOLFRAM_AUDIT_INPUT_INVALID",
        "WOLFRAM_AUDIT_UNAVAILABLE",
        "WOLFRAM_OUTPUT_INVALID",
        "WOLFRAM_CALCULATION_MISMATCH",
    ):
        assert f"'{verdict}'" in sql
    assert "WOLFRAM_AUDIT_LEDGER_WRITE_UNPROVEN" not in sql
