"""Predeploy negative and positive controls (fake RPC; not a live DB migration test)."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "v17" / "nba_verification_schema_predeploy.py"
spec = importlib.util.spec_from_file_location("nba_schema_guard", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Db:
    def __init__(self, value=None, exception=None):
        self.value, self.exception, self.calls = value, exception, []

    def rpc(self, name):
        self.calls.append(name)
        if self.exception:
            raise self.exception
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.value))


def proof(**updates):
    data = {
        "contract_version": module.CONTRACT,
        "compatible": True,
        "probability_publishable": False,
        "can_execute": False,
    }
    data.update(updates)
    return data


def test_old_schema_fails_closed():
    db = Db(exception=RuntimeError("RPC not found / old schema"))
    assert not module.check_schema(db)
    assert db.calls == [module.RPC]


def test_new_compatible_schema_admits_predeploy_only():
    db = Db(proof())
    assert module.check_schema(db)
    assert db.calls == [module.RPC]


def test_false_or_ambiguous_proof_rejected():
    for value in (None, [], {"compatible": True}, proof(compatible="true"),
                  proof(contract_version="V0"), proof(can_execute=True),
                  proof(probability_publishable=True), proof(compatible=False)):
        assert not module.check_schema(Db(value)), value


def test_missing_credentials_rejected_without_logging_secrets(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://something.supabase.co")
    assert module.main() == 1
    assert capsys.readouterr().err.strip() == module.HOLD


def test_migration_is_immutable_restricted_and_non_certifying():
    sql = (ROOT / "migrations" / "20261110_nba_team_event_verification_receipts.sql").read_text().lower()
    assert "before update or delete" in sql
    assert "enable row level security" in sql
    assert "grant select, insert" in sql
    assert "security invoker" in sql
    assert "calibrator_fit_end timestamptz not null" in sql
    assert "probability_publishable = false" in sql
    assert "can_execute = false" in sql
    assert "create function public.wow_nba_team_event_verification_receipt_schema_ready()" in sql
    assert "insert into public.wow_team_event_specialist_certifications" not in sql


def _migration_sql_without_comments():
    """Read real migration DDL, not prose or example/comment mentions."""
    import re
    sql = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "migrations").glob("*.sql"))
    )
    return re.sub(r"--[^\n]*", "", sql)


def test_wiring_ledger_is_backed_by_an_actual_migration():
    """Red until the reported NBA pre-fit verifier and schema agree by name."""
    import re
    sql = _migration_sql_without_comments()
    assert re.search(
        r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"(?:public\.)?wow_independent_verification_receipts\b",
        sql, flags=re.IGNORECASE,
    ), (
        "NBA_VERIFICATION_LEDGER_MISSING: draft migration creates "
        "wow_nba_team_event_verification_receipts, but new NBA pre-fit wiring "
        "requires wow_independent_verification_receipts; do not enable predeploy."
    )


def test_wiring_calibrator_evidence_columns_have_real_ddl():
    """Neither column exists in the validated database's base table."""
    import re
    sql = _migration_sql_without_comments()
    for col in ("calibration_sha256", "calibration_fit_end"):
        assert re.search(
            r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?"
            r"(?:public\.)?wow_basketball_team_event_calibrators\b"
            r"[^;]*\bADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?"
            + col + r"\b",
            sql, flags=re.IGNORECASE | re.DOTALL,
        ), (
            "NBA_VERIFICATION_CALIBRATOR_EVIDENCE_MISSING: "
            + col + " has no additive migration; do not enable predeploy."
        )
