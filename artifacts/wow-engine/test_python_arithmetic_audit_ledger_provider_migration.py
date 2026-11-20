"""Regression for the P0 Python arithmetic audit ledger provider mismatch.

This is a source/schema contract test. It is NOT a substitute for an isolated
Postgres migration replay or a production insert/readback acceptance receipt.
"""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
MIGRATION = ROOT / "migrations" / "20261021_python_arithmetic_audit_ledger_provider_verdicts.sql"
LEGACY = ROOT / "migrations" / "20260902_wolfram_arithmetic_audit_ledger.sql"


def _allowed_verdicts() -> dict[str, set[str]]:
    sql = MIGRATION.read_text()
    pairs = re.findall(
        r"provider\s*=\s*'([^']+)'\s+and\s+verdict\s+in\s*\(([^)]*)\)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return {
        provider: set(re.findall(r"'([^']+)'", values))
        for provider, values in pairs
    }


def test_arithmetic_ledger_migration_keeps_strict_provider_scoped_verdicts():
    allowed = _allowed_verdicts()
    assert allowed == {
        "WOLFRAM_ALPHA": {
            "PASS",
            "WOLFRAM_AUDIT_INPUT_INVALID",
            "WOLFRAM_AUDIT_UNAVAILABLE",
            "WOLFRAM_OUTPUT_INVALID",
            "WOLFRAM_CALCULATION_MISMATCH",
        },
        "PYTHON_PRIMARY": {
            "PASS",
            "PYTHON_ARITHMETIC_AUDIT_INPUT_INVALID",
            "PYTHON_ARITHMETIC_OUTPUT_INVALID",
            "COMPUTATION_VERIFICATION_CONFLICT",
        },
    }
    # Provider/verdict association is explicit: the old Wolfram failure
    # vocabulary cannot be passed off as a Python-primary audit and vice versa.
    assert "COMPUTATION_VERIFICATION_CONFLICT" not in allowed["WOLFRAM_ALPHA"]
    assert "WOLFRAM_AUDIT_UNAVAILABLE" not in allowed["PYTHON_PRIMARY"]
    assert "PYTHON_ARITHMETIC_AUDIT_LEDGER_WRITE_UNPROVEN" not in set.union(*allowed.values())


def test_forward_migration_does_not_relax_immutable_prediction_ledger_guards():
    sql = MIGRATION.read_text().lower()
    assert sql.count("drop constraint if exists") == 2
    assert "wow_wolfram_arithmetic_audits_provider_check" in sql
    assert "wow_wolfram_arithmetic_audits_verdict_check" in sql
    assert "add constraint wow_wolfram_arithmetic_audits_provider_check" in sql
    assert "add constraint wow_wolfram_arithmetic_audits_verdict_check" in sql
    assert "begin;" in sql and "commit;" in sql
    for forbidden in (
        "drop trigger", "disable trigger", "disable row level security",
        "drop policy", "update public.wow_predictions", "update public.wow_wolfram_arithmetic_audits",
        "delete from", "truncate", "drop table",
        "grant update", "grant delete", "grant all",
    ):
        assert forbidden not in sql


def test_legacy_append_only_guards_and_runtime_provider_still_match_contract():
    legacy = LEGACY.read_text().lower()
    runtime = (ROOT / "python_arithmetic_auditor.py").read_text()
    assert "references public.wow_predictions(prediction_id)" in legacy
    assert "before update on public.wow_wolfram_arithmetic_audits" in legacy
    assert "before delete on public.wow_wolfram_arithmetic_audits" in legacy
    assert "check (blocks_model_probability = false)" in legacy
    assert "check (can_execute = false)" in legacy
    assert "enable row level security" in legacy
    assert "grant select, insert on public.wow_wolfram_arithmetic_audits to service_role" in legacy
    assert 'PROVIDER = "PYTHON_PRIMARY"' in runtime
    assert 'LEDGER_WRITE_UNPROVEN = "PYTHON_ARITHMETIC_AUDIT_LEDGER_WRITE_UNPROVEN"' in runtime
