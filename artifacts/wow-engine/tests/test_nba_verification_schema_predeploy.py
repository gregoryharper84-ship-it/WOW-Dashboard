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
