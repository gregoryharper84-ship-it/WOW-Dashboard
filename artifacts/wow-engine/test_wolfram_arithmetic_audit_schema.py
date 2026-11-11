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


def test_python_primary_provider_and_verdicts_are_admitted_by_ledger():
    """Regression: PYTHON_ARITHMETIC_AUDIT_LEDGER_WRITE_UNPROVEN root cause."""
    import python_arithmetic_auditor as pa

    sql = (Path(__file__).parent / "migrations" / "20261105_python_arithmetic_audit_ledger_provider.sql").read_text()
    assert f"'{pa.PROVIDER}'" in sql
    for verdict in (pa.PASS, pa.INPUT_INVALID, pa.OUTPUT_INVALID, pa.CALCULATION_MISMATCH):
        assert f"'{verdict}'" in sql
    low = sql.lower()
    assert "grant " not in low and "disable row level security" not in low


def test_arithmetic_ledger_verdict_check_is_bound_to_matching_provider():
    """Regression: a provider cannot use the other auditor's verdict family."""
    sql = (Path(__file__).parent / "migrations" /
           "20261105_python_arithmetic_audit_ledger_provider.sql").read_text()
    assert "check (provider in ('WOLFRAM_ALPHA', 'PYTHON_PRIMARY'))" in sql
    clause = sql.split("add constraint wow_wolfram_arithmetic_audits_verdict_check", 1)[1]
    assert "(provider = 'WOLFRAM_ALPHA' and verdict in (" in clause
    assert "(provider = 'PYTHON_PRIMARY' and verdict in (" in clause
    wolfram = clause.split("(provider = 'WOLFRAM_ALPHA' and verdict in (", 1)[1].split(
        "(provider = 'PYTHON_PRIMARY' and verdict in (", 1)[0]
    python = clause.split("(provider = 'PYTHON_PRIMARY' and verdict in (", 1)[1]
    for code in ("WOLFRAM_AUDIT_INPUT_INVALID", "WOLFRAM_AUDIT_UNAVAILABLE",
                 "WOLFRAM_OUTPUT_INVALID", "WOLFRAM_CALCULATION_MISMATCH"):
        assert f"'{code}'" in wolfram
        assert f"'{code}'" not in python
    for code in ("PYTHON_ARITHMETIC_AUDIT_INPUT_INVALID", "PYTHON_ARITHMETIC_OUTPUT_INVALID",
                 "COMPUTATION_VERIFICATION_CONFLICT"):
        assert f"'{code}'" in python
        assert f"'{code}'" not in wolfram
    assert "'PASS'" in wolfram and "'PASS'" in python
    # Fail-closed migration only changes CHECKs; do not weaken ACLs or immutability.
    assert "grant " not in sql.lower()
    assert "disable row level security" not in sql.lower()


def test_python_audit_checks_are_replaced_in_one_atomic_statement():
    """A per-statement-autocommit runner must never expose an unguarded ledger."""
    sql = (Path(__file__).parent / "migrations" /
           "20261105_python_arithmetic_audit_ledger_provider.sql").read_text()
    executable = "\n".join(line for line in sql.splitlines()
                           if not line.lstrip().startswith("--"))
    statements = [part.strip() for part in executable.split(";") if part.strip()]
    assert len(statements) == 1
    normalized = statements[0].lower()
    assert normalized.count("alter table public.wow_wolfram_arithmetic_audits") == 1
    assert normalized.count("drop constraint if exists") == 2
    assert normalized.count("add constraint wow_wolfram_arithmetic_audits_") == 2


def test_python_audit_ledger_unproven_failure_code_is_registered():
    """A ledger-persistence failure must retain its distinct typed identity."""
    import python_arithmetic_auditor as pa

    registry = (Path(__file__).parent / "docs" / "failure_codes.md").read_text()
    assert f"| `{pa.LEDGER_WRITE_UNPROVEN}` |" in registry
