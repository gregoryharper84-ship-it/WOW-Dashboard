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


def test_fenced_lease_renewal_calls_only_privileged_rpc():
    db = FakeConnection([(True,)])
    module.renew(db, service_id=module.ALLOWED_SERVICE,
                 target_sha="a" * 40, owner_run_id="run1",
                 token=9, lease_seconds=300)
    sql, params = db.obj.executed[0]
    assert "renew_render_worker_deploy" in sql
    assert params[3] == 9


def test_failed_renewal_does_not_forge_success():
    db = FakeConnection([(False,)])
    with pytest.raises(module.DeploymentLeaseError, match="RENEW_NOT_ACKNOWLEDGED"):
        module.renew(db, service_id=module.ALLOWED_SERVICE,
                     target_sha="a" * 40, owner_run_id="run1", token=8)


def test_committed_lease_renewal_durably_commits_before_next_poll():
    class Tx(FakeConnection):
        def __init__(self):
            super().__init__([(True,)])
            self.commits = 0
            self.rollbacks = 0
        def commit(self):
            self.commits += 1
        def rollback(self):
            self.rollbacks += 1

    db = Tx()
    module.renew_committed(
        db, service_id=module.ALLOWED_SERVICE,
        target_sha="a" * 40, owner_run_id="run1", token=9,
    )
    assert db.commits == 1 and db.rollbacks == 0
    assert "renew_render_worker_deploy" in db.obj.executed[0][0]


def test_failed_committed_renewal_rolls_back_and_fails_closed():
    class Tx(FakeConnection):
        def __init__(self):
            super().__init__([(False,)])
            self.commits = 0
            self.rollbacks = 0
        def commit(self):
            self.commits += 1
        def rollback(self):
            self.rollbacks += 1

    db = Tx()
    with pytest.raises(module.DeploymentLeaseError, match="RENEW_NOT_ACKNOWLEDGED"):
        module.renew_committed(
            db, service_id=module.ALLOWED_SERVICE,
            target_sha="a" * 40, owner_run_id="run1", token=9,
        )
    assert db.commits == 0 and db.rollbacks == 1



def test_db_identity_attested_with_approved_project_and_limited_role():
    from datetime import datetime, timezone
    approved = datetime(2026, 10, 14, tzinfo=timezone.utc)
    ref = "iczfhsmjrrafhvcpmqhr"
    db = FakeConnection([(
        "wow_release_controller", ref, "production",
        module.ALLOWED_SERVICE, True, "independent-release-authority", approved,
    )])
    module.attest_release_control_identity(db, expected_project_ref=ref)
    sql, params = db.obj.executed[0]
    assert "release_control_identity" in sql
    assert params == ()


@pytest.mark.parametrize("expected,row,reason", [
    ("", None, "PROJECT_IDENTITY_UNCONFIGURED"),
    ("wrong", None, "PROJECT_IDENTITY_UNCONFIGURED"),
    ("iczfhsmjrrafhvcpmqhr", [], "DB_IDENTITY_MISSING"),
    ("iczfhsmjrrafhvcpmqhr", [(
        "postgres", "iczfhsmjrrafhvcpmqhr", "production",
        module.ALLOWED_SERVICE, True, "independent", "2026-10-14",
    )], "DB_ROLE_NOT_RESTRICTED"),
    ("iczfhsmjrrafhvcpmqhr", [(
        "wow_release_controller", "zchzcveqqemuwypifqyx", "production",
        module.ALLOWED_SERVICE, True, "independent", "2026-10-14",
    )], "DB_IDENTITY_MISMATCH"),
    ("iczfhsmjrrafhvcpmqhr", [(
        "wow_release_controller", "iczfhsmjrrafhvcpmqhr", "staging",
        module.ALLOWED_SERVICE, True, "independent", "2026-10-14",
    )], "DB_IDENTITY_MISMATCH"),
    ("iczfhsmjrrafhvcpmqhr", [(
        "wow_release_controller", "iczfhsmjrrafhvcpmqhr", "production",
        module.ALLOWED_SERVICE, False, None, None,
    )], "DB_APPROVAL_MISSING"),
])
def test_db_identity_denies_unapproved_unknown_or_wrong_control_plane(expected, row, reason):
    db = FakeConnection(row or [])
    with pytest.raises(module.DeploymentLeaseError, match=reason):
        module.attest_release_control_identity(db, expected_project_ref=expected)
