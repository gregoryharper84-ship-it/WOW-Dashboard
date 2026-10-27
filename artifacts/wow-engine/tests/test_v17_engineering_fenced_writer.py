"""Adversarial source-level tests for issue #1739's single write boundary."""
from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from v17.engineering_fenced_writer import FencingHold, Gateway, Intent, GitHubAppWriter, SupabaseRpcStore


ID = "68e69cd5-7f55-4baa-a2a2-8ab5b4c35313"
ID2 = "57a7a33e-b5bb-459b-a7b6-594714776a39"


def make(*, epoch=1, incident_id=1739, mutation_id=ID, operation="CREATE_BRANCH"):
    branch = f"wow-engineering/fenced-{incident_id}-{epoch}-{mutation_id[:8]}"
    return Intent(mutation_id, incident_id, "github-actions:worker:123", epoch,
                  "GLOBAL", operation, branch, "a" * 40,
                  "" if operation in {"CREATE_BRANCH", "PUSH_REF"} else "Immutable source and governance receipt")


class Store:
    """Test double models one atomic DB transaction per claim/begin/confirm."""
    def __init__(self):
        self.lock = threading.RLock()
        self.owners = {1739: ("github-actions:worker:123", 1, "GLOBAL")}
        self.intents = {}

    def begin(self, intent):
        with self.lock:
            current = self.owners.get(intent.incident_id)
            row = self.intents.get(intent.mutation_id)
            if row:
                if row[0] != intent:
                    return "DENIED"
                return row[1]
            if current != (intent.owner_id, intent.epoch, intent.lease_group):
                return "DENIED"
            if any(i.incident_id == intent.incident_id and state == "PENDING"
                   for i, state in self.intents.values()):
                return "DENIED"
            if any(inc != intent.incident_id and
                   (group == "GLOBAL" or intent.lease_group == "GLOBAL" or group == intent.lease_group)
                   for inc, (_, _, group) in self.owners.items()):
                return "DENIED"
            if any(i.incident_id != intent.incident_id and state == "PENDING"
                   and (i.lease_group == "GLOBAL" or intent.lease_group == "GLOBAL"
                        or i.lease_group == intent.lease_group)
                   for i, state in self.intents.values()):
                return "DENIED"
            self.intents[intent.mutation_id] = (intent, "PENDING")
            return "STARTED"

    def confirm(self, intent):
        with self.lock:
            if self.intents.get(intent.mutation_id) != (intent, "PENDING"):
                return False
            self.intents[intent.mutation_id] = (intent, "CONFIRMED")
            return True

    def takeover(self, incident, owner, epoch):
        with self.lock:
            if any(i.incident_id == incident and state == "PENDING"
                   for i, state in self.intents.values()):
                return False
            group = self.owners[incident][2]
            self.owners[incident] = (owner, epoch, group)
            return True


class GitHub:
    def __init__(self):
        self.accepted = set()
        self.writes = []
        self.started = threading.Event()
        self.resume = threading.Event()
        self.pause = False
        self.raise_after_write = False

    def create_branch(self, intent):
        self._write(intent)

    def create_pr(self, intent):
        self._write(intent)

    def push_ref(self, intent):
        self._write(intent)

    def _write(self, intent):
        if self.pause:
            self.started.set()
            assert self.resume.wait(2)
        self.writes.append(intent.mutation_id)
        self.accepted.add(intent)
        if self.raise_after_write:
            raise TimeoutError("unknown POST status")

    def exact_readback(self, intent):
        return intent in self.accepted


def test_normal_commit_exact_readback_and_idempotent_replay():
    s, g = Store(), GitHub()
    i = make()
    assert Gateway(s, g).deliver(i) == "EXACT_GITHUB_WRITE_CONFIRMED"
    assert Gateway(s, g).deliver(i) == "ALREADY_CONFIRMED"
    assert len(g.writes) == 1


def test_late_writer_after_takeover_cannot_write():
    s, g = Store(), GitHub()
    assert s.takeover(1739, "github-actions:worker:456", 2)
    with pytest.raises(FencingHold, match="LEASE_STALE_OR_CONFLICTING"):
        Gateway(s, g).deliver(make())
    assert g.writes == []


def test_paused_external_write_prevents_lease_reassignment():
    s, g = Store(), GitHub()
    g.pause = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(Gateway(s, g).deliver, make())
        assert g.started.wait(2)
        assert s.takeover(1739, "github-actions:worker:456", 2) is False
        g.resume.set()
        assert future.result(timeout=3) == "EXACT_GITHUB_WRITE_CONFIRMED"
    assert s.takeover(1739, "github-actions:worker:456", 2) is True
    with pytest.raises(FencingHold, match="LEASE_STALE_OR_CONFLICTING"):
        Gateway(s, g).deliver(make(mutation_id=ID2))
    assert len(g.writes) == 1


def test_ambiguous_github_write_reconciles_without_replaying_post():
    s, g = Store(), GitHub()
    g.raise_after_write = True
    with pytest.raises(FencingHold, match="GITHUB_MUTATION_OUTCOME_UNKNOWN"):
        Gateway(s, g).deliver(make())
    assert not s.takeover(1739, "github-actions:worker:456", 2)
    g.raise_after_write = False
    assert Gateway(s, g).deliver(make()) == "EXACT_GITHUB_WRITE_CONFIRMED"
    assert len(g.writes) == 1


def test_no_proof_after_ambiguous_write_fails_closed():
    s, g = Store(), GitHub()
    s.begin(make())
    with pytest.raises(FencingHold, match="GITHUB_MUTATION_RECONCILIATION_REQUIRED"):
        Gateway(s, g).deliver(make())
    assert g.writes == []
    assert not s.takeover(1739, "github-actions:worker:456", 2)


@pytest.mark.parametrize("mutator", [
    lambda i: replace(i, epoch=2),
    lambda i: replace(i, incident_id=7),
    lambda i: replace(i, branch="main"),
    lambda i: replace(i, owner_id="invalid"),
    lambda i: replace(i, mutation_id="invalid"),
    lambda i: replace(i, sha="no-sha"),
])
def test_invalid_identity_or_stale_lease_cannot_mutate(mutator):
    s, g = Store(), GitHub()
    with pytest.raises(FencingHold):
        Gateway(s, g).deliver(mutator(make()))
    assert g.writes == []


def test_different_incidents_in_global_conflict_but_disjoint_domains_can_pass():
    s, g = Store(), GitHub()
    s.owners[1739] = ("github-actions:worker:123", 1, "P0_NFL")
    candidate = replace(make(), lease_group="P0_NFL")
    s.owners[1777] = ("github-actions:worker:123", 1, "GLOBAL")
    with pytest.raises(FencingHold, match="LEASE_STALE_OR_CONFLICTING"):
        Gateway(s, g).deliver(candidate)
    s.owners[1777] = ("github-actions:worker:123", 1, "P0_NBA")
    assert Gateway(s, g).deliver(candidate) == "EXACT_GITHUB_WRITE_CONFIRMED"


def test_create_pr_uses_same_fence():
    s, g = Store(), GitHub()
    i = make(operation="CREATE_PR")
    assert Gateway(s, g).deliver(i) == "EXACT_GITHUB_WRITE_CONFIRMED"
    assert len(g.writes) == 1


def test_missing_store_ack_and_drift_cannot_be_called_verified():
    s, g = Store(), GitHub()
    i = make()
    s.begin(i)
    with pytest.raises(FencingHold, match="GITHUB_MUTATION_RECONCILIATION_REQUIRED"):
        Gateway(s, g).deliver(i)
    assert g.writes == []

def test_expired_pending_writer_blocks_another_global_incident():
    s, g = Store(), GitHub()
    s.begin(make())
    # Old lease is no longer live, but its external POST is still ambiguous.
    s.owners = {1777: ("github-actions:worker:123", 1, "GLOBAL")}
    with pytest.raises(FencingHold, match="LEASE_STALE_OR_CONFLICTING"):
        Gateway(s, g).deliver(make(incident_id=1777, mutation_id=ID2))
    assert not g.writes


def test_sql_contract_guards_expired_pending_and_no_advisory_deadlock():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "migrations" /
            "20261027_writer_mutation_fence_REVIEW_ONLY.sql").read_text()
    assert "pg_try_advisory_xact_lock(901734,1739)" in text
    assert "WRITER_LEASE_TRANSITION_RETRY" in text
    assert "m.state='PENDING'" in text
    assert "y.state='PENDING'" in text
    assert "WRITER_MUTATION_IN_FLIGHT" in text

def test_push_ref_epoch_fence_and_idempotence():
    s, g = Store(), GitHub()
    i = make(operation="PUSH_REF")
    assert Gateway(s, g).deliver(i) == "EXACT_GITHUB_WRITE_CONFIRMED"
    assert Gateway(s, g).deliver(i) == "ALREADY_CONFIRMED"
    assert len(g.writes) == 1


def test_push_ref_lost_ack_never_repeats_git_push():
    s, g = Store(), GitHub()
    i = make(operation="PUSH_REF")
    g.raise_after_write = True
    with pytest.raises(FencingHold, match="GITHUB_MUTATION_OUTCOME_UNKNOWN"):
        Gateway(s, g).deliver(i)
    g.raise_after_write = False
    assert Gateway(s, g).deliver(i) == "EXACT_GITHUB_WRITE_CONFIRMED"
    assert len(g.writes) == 1


def test_push_ref_untrusted_remote_rejected_before_push(monkeypatch):
    import subprocess
    app = GitHubAppWriter("dummy-app-token")
    i = make(operation="PUSH_REF")
    calls = []
    def output(args, **kwargs):
        calls.append(args)
        if args[-2:] == ["rev-parse", "HEAD"]:
            return "a" * 40 + "\n"
        return "https://attacker.example.invalid/project.git\n"
    monkeypatch.setattr(subprocess, "check_output", output)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("unexpected push"))
    with pytest.raises(FencingHold, match="PUSH_REF_REMOTE_UNTRUSTED"):
        app.push_ref(i)
    assert len(calls) == 2


def test_push_ref_wrong_local_sha_rejected_before_remote_probe(monkeypatch):
    import subprocess
    app = GitHubAppWriter("dummy-app-token")
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: "b" * 40)
    with pytest.raises(FencingHold, match="PUSH_REF_LOCAL_SHA_MISMATCH"):
        app.push_ref(make(operation="PUSH_REF"))


@pytest.mark.parametrize("hostname", [
    "https://attacker.example.invalid", "http://iczfhsmjrrafhvcpmqhr.supabase.co",
    "https://evil.supabase.co", "https://iczfhsmjrrafhvcpmqhr.supabase.co.evil.test",
    "https://iczfhsmjrrafhvcpmqhr.supabase.co/anything",
])
def test_rpc_rejects_unattested_or_insecure_project(hostname):
    with pytest.raises(FencingHold, match="LEASE_STORE_CONFIGURATION_INVALID"):
        SupabaseRpcStore(hostname, "dummy-key", "iczfhsmjrrafhvcpmqhr")


def test_rpc_attests_exact_project():
    store = SupabaseRpcStore("https://iczfhsmjrrafhvcpmqhr.supabase.co", "dummy", "iczfhsmjrrafhvcpmqhr")
    assert store.base_url == "https://iczfhsmjrrafhvcpmqhr.supabase.co"


def test_exact_readback_rejects_foreign_pr_author_or_wrong_draft(monkeypatch):
    app = GitHubAppWriter("dummy")
    i = make(operation="CREATE_PR")
    ref = {"ref": "refs/heads/" + i.branch, "object": {"sha": i.sha}}
    pr = {"body": i.payload, "head": {"sha": i.sha, "ref": i.branch,
          "repo": {"full_name": "gregoryharper84-ship-it/WOW-Dashboard"}},
          "base": {"ref": "main"}, "draft": True,
          "user": {"login": "wow-engineering[bot]"}}
    def mock_api(method, path, payload=None):
        return ref if "git/ref/heads/" in path else [pr]
    monkeypatch.setattr(app, "_call", mock_api)
    assert app.exact_readback(i)
    pr["draft"] = False
    assert not app.exact_readback(i)
    pr["draft"] = True
    pr["user"]["login"] = "external-user"
    assert not app.exact_readback(i)


def test_sql_allows_only_scoped_nonforce_push():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "migrations" /
            "20261027_writer_mutation_fence_REVIEW_ONLY.sql").read_text()
    assert "('CREATE_BRANCH','PUSH_REF','CREATE_PR')" in text


def test_database_branch_identity_matches_gateway_incident_and_epoch():
    """Database must reject a valid-looking branch from a different lease."""
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "migrations" /
           "20261027_writer_mutation_fence_REVIEW_ONLY.sql").read_text()
    assert "p_incident_id::text" in sql
    assert "p_epoch::text" in sql
    assert "p_branch ~ ('^wow-engineering/fenced-'" in sql
    assert "p_branch !~ '^wow-engineering/fenced-[0-9]+-[0-9]+" not in sql
    store, github = Store(), GitHub()
    wrong = replace(make(), branch=make(incident_id=1740).branch)
    with pytest.raises(FencingHold, match="MUTATION_BRANCH_UNBOUND"):
        Gateway(store, github).deliver(wrong)
    assert github.writes == []


def test_exact_existing_engineering_app_replay_requires_source_provenance():
    source = "b" * 40
    replay = replace(make(operation="CREATE_PR"), source_pr=2158,
                     source_sha=source, draft=False,
                     branch="wow-engineering/exact-replay-2158-" + source[:12])
    replay.validate()
    for bad in (
        replace(replay, source_pr=2159),
        replace(replay, source_sha="bad"),
        replace(replay, draft=True),
        replace(replay, operation="CREATE_BRANCH"),
        replace(replay, branch="wow-engineering/exact-replay-2158-" + "c"*12),
    ):
        with pytest.raises(FencingHold, match="MUTATION_REPLAY_PROVENANCE_INVALID"):
            bad.validate()


def test_database_source_protocol_has_one_canonical_rpc_and_matching_grants():
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "migrations" /
           "20261027_writer_mutation_fence_REVIEW_ONLY.sql").read_text()
    assert sql.count("CREATE TABLE IF NOT EXISTS public.wow_engineering_writer_mutation_intents") == 1
    assert sql.count("CREATE OR REPLACE FUNCTION public.wow_engineering_begin_writer_mutation(") == 1
    assert sql.count("CREATE OR REPLACE FUNCTION public.wow_engineering_confirm_writer_mutation(") == 1
    sig = "public.wow_engineering_begin_writer_mutation(uuid,bigint,text,bigint,text,text,text,text,text,bigint,text,boolean)"
    assert sql.count("REVOKE ALL ON FUNCTION " + sig) == 1
    assert sql.count("GRANT EXECUTE ON FUNCTION " + sig) == 1
    assert "m.source_pr IS DISTINCT FROM p_source_pr" in sql
    assert "m.draft IS DISTINCT FROM p_draft" in sql
    assert "p_branch = ('wow-engineering/exact-replay-'" in sql
