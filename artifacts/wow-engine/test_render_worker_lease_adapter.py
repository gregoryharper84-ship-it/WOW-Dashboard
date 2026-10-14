"""Mocked DB adapter contract tests; no production credentials."""
import importlib.util
import sys
from pathlib import Path
import pytest

PATH = Path(__file__).parent / "v17/render_worker_lease_adapter.py"
spec = importlib.util.spec_from_file_location("render_worker_lease_adapter", PATH)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def execute(self, sql, params):
        self.executed.append((sql, params))
    def fetchall(self):
        return self.rows

class FakeConnection:
    def __init__(self, rows):
        self.obj = FakeCursor(rows)
    def cursor(self):
        return self.obj

def test_single_atomic_claim():
    db = FakeConnection([("INITIATING", 7, True)])
    claimed = module.claim(db, service_id=module.ALLOWED_SERVICE,
                           target_sha="a" * 40, owner_run_id="run1")
    assert claimed.can_initiate is True and claimed.token == 7
    assert "claim_render_worker_deploy" in db.obj.executed[0][0]

def test_denied_claim_never_grants_initiation():
    db = FakeConnection([("ACK_AMBIGUOUS", 7, False)])
    claimed = module.claim(db, service_id=module.ALLOWED_SERVICE,
                           target_sha="a" * 40, owner_run_id="run2")
    assert claimed.can_initiate is False

def test_inconsistent_database_receipt_is_rejected():
    db = FakeConnection([("HOLD", 7, True)])
    with pytest.raises(module.DeploymentLeaseError, match="INCONSISTENT"):
        module.claim(db, service_id=module.ALLOWED_SERVICE,
                     target_sha="a" * 40, owner_run_id="run2")

def test_cross_service_rejected_before_database_call():
    db = FakeConnection([("INITIATING", 7, True)])
    with pytest.raises(module.DeploymentLeaseError, match="NOT_ALLOWLISTED"):
        module.claim(db, service_id="srv-other", target_sha="a" * 40,
                     owner_run_id="run3")
    assert not db.obj.executed

def test_fenced_receipt_requires_db_true():
    db = FakeConnection([(True,)])
    module.record(db, service_id=module.ALLOWED_SERVICE, target_sha="a" * 40,
                  owner_run_id="run1", token=4, state="DEPLOYING", deploy_id="dep-abc")
    assert "record_render_worker_deploy" in db.obj.executed[0][0]
    denied = FakeConnection([(False,)])
    with pytest.raises(module.DeploymentLeaseError, match="NOT_ACKNOWLEDGED"):
        module.record(denied, service_id=module.ALLOWED_SERVICE, target_sha="a" * 40,
                      owner_run_id="run1", token=4, state="HOLD")
