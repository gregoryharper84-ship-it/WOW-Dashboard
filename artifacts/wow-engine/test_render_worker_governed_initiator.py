"""Deterministic at-most-once Render integration tests."""
import importlib.util
from pathlib import Path
import sys
import pytest

BASE = Path(__file__).parent / "v17"
for name in ("render_worker_lease_adapter", "render_worker_governed_initiator"):
    spec = importlib.util.spec_from_file_location(name, BASE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
initiator = sys.modules["render_worker_governed_initiator"]

class DB:
    def __init__(self, claim):
        self.claim = claim
        self.commits = 0
        self.rollbacks = 0
        self.operations = []
    def cursor(self):
        db = self
        class Cursor:
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def execute(self, sql, args):
                db.operations.append(("sql", sql, args))
                self.method = sql
            def fetchall(self):
                if "claim_render_worker_deploy" in self.method: return [db.claim]
                return [(True,)]
        return Cursor()
    def commit(self):
        self.commits += 1
        self.operations.append(("commit",))
    def rollback(self):
        self.rollbacks += 1

SHA = "a" * 40
def call(db, post, listing=lambda: []):
    return initiator.initiate_once(connection=db, target_sha=SHA, current_main_sha=SHA,
       owner_run_id="workflow-123", primary_release_result="success",
       list_deploys=listing, post_deploy=post)

def test_claim_must_commit_before_post():
    db = DB(("INITIATING", 1, True))
    def post():
        assert db.commits == 1
        db.operations.append(("post",))
        return {"deploy": {"id": "dep-example"}}
    receipt = call(db, post)
    assert receipt.state == "DEPLOYING"
    assert receipt.lease_token == 1
    assert db.commits == 2
    assert [o[0] for o in db.operations].count("post") == 1

def test_denied_claim_never_posts():
    db = DB(("ACK_AMBIGUOUS", 1, False))
    assert call(db, lambda: pytest.fail("duplicate post")).state == "RECONCILIATION_REQUIRED"
    assert db.commits == 1

def test_ambiguous_post_never_retries():
    db = DB(("INITIATING", 2, True))
    posts = []
    def post():
        posts.append(1)
        raise TimeoutError("lost response")
    with pytest.raises(TimeoutError):
        call(db, post)
    assert len(posts) == 1
    assert any("ACK_AMBIGUOUS" in repr(x) for x in db.operations)

def test_existing_exact_sha_never_posts():
    db = DB(("INITIATING", 1, True))
    result = call(db, lambda: pytest.fail("duplicate post"),
        listing=lambda: [{"id":"dep-present","commit":{"id":SHA},"status":"live"}])
    assert result.deploy_id == "dep-present"
    assert db.commits == 0

def test_stale_main_fails_without_db_or_render():
    db = DB(("INITIATING", 1, True))
    with pytest.raises(RuntimeError, match="NOT_AUTHORIZED"):
        initiator.initiate_once(connection=db, target_sha=SHA,
            current_main_sha="b"*40, owner_run_id="workflow-123",
            primary_release_result="success",
            list_deploys=lambda: pytest.fail("render accessed"),
            post_deploy=lambda: pytest.fail("post accessed"))
    assert db.commits == 0
