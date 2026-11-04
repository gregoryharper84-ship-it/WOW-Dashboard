"""Negative/adversarial tests for the out-of-band V17 maintenance recoverer."""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest

HERE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location("release_breakglass", HERE / "release_breakglass.py")
recovery = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(recovery)
SPECW = importlib.util.spec_from_file_location("release_breakglass_witness", HERE / "release_breakglass_witness.py")
import sys
sys.modules["release_breakglass"] = recovery
witness = importlib.util.module_from_spec(SPECW)
assert SPECW.loader
SPECW.loader.exec_module(witness)

HEAD = "a" * 40
MAIN = "b" * 40

def baseline():
    return {"name": "wow-v17-main-agent-separation", "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]}},
            "bypass_actors": [], "rules": [
                {"type": "deletion"}, {"type": "non_fast_forward"},
                {"type": "pull_request", "parameters": {"required_approving_review_count": 0}},
                {"type": "required_status_checks", "parameters": {
                    "strict_required_status_checks_policy": False,
                    "required_status_checks": [dict(context=n, integration_id=a) for n,a in recovery.CHECK_CONTEXTS]}}
            ]}


def intent():
    return dict(schema="WOW_BREAKGLASS_V1", repo=recovery.CANONICAL_REPO,
                ruleset_id=recovery.RULESET_ID, pr=1932, head_sha=HEAD,
                main_sha=MAIN, incident=1930, reviewer_login="security-reviewer",
                expires_utc="2026-10-22T12:00:00Z",
                allowed_files=[".github/workflows/wow-v17-independent-qa-check.yml"],
                baseline_ruleset_sha256=recovery.sha256(baseline()),
                removed_check=list(recovery.CHECK_CONTEXTS[-1]), window_seconds=60)


def now():
    return dt.datetime(2026, 10, 22, 11, 30, tzinfo=dt.timezone.utc)


def checks():
    return [dict(name=n, app={"id":a}, head_sha=HEAD, id=100+i,
                 status="completed", conclusion="success") for i,(n,a) in enumerate(recovery.CHECK_CONTEXTS[:-1])]


def reviews():
    return [dict(id=100, user={"login":"gregoryharper84-ship-it"},state="APPROVED",commit_id=HEAD),
            dict(id=101, user={"login":"security-reviewer"},state="APPROVED",commit_id=HEAD,
                 body="SIRT_SECURITY_PASS " + HEAD + " 24907422 " + recovery.sha256(baseline()))]


class FakeGitHub:
    def __init__(self):
        self.ruleset = baseline()
        self.head = HEAD
        self.main = MAIN
        self.review_rows = reviews()
        self.check_rows = checks()
        self.file_rows = [{"filename":".github/workflows/wow-v17-independent-qa-check.yml","status":"modified"}]
        self.other_prs = []
        self.calls = []
        self.fail_merge = False
        self.merged = False
    def get(self, suffix):
        self.calls.append(("get",suffix))
        if suffix.startswith("rulesets/"):
            return copy.deepcopy(self.ruleset)
        if suffix == "git/ref/heads/main":
            return {"object":{"sha":self.main}}
        if suffix.startswith("pulls/"):
            return {"state":"open","draft":False,"merged":self.merged,
                    "head":{"sha":self.head,"repo":{"full_name":recovery.CANONICAL_REPO}},
                    "base":{"ref":"main"},"user":{"login":getattr(self,"author","Copilot")},
                    "changed_files":len(self.file_rows)}
        raise AssertionError(suffix)
    def pages(self, suffix, collection=None):
        if suffix.endswith("/files"):
            return copy.deepcopy(self.file_rows)
        if suffix.endswith("/reviews"):
            return copy.deepcopy(self.review_rows)
        if "/check-runs" in suffix:
            return copy.deepcopy(self.check_rows)
        if suffix == "pulls?state=open":
            return copy.deepcopy(self.other_prs)
        raise AssertionError(suffix)
    def put(self, suffix, payload):
        self.calls.append(("put",suffix))
        self.ruleset = copy.deepcopy(payload)
        return self.ruleset
    def merge(self, pr, head):
        self.calls.append(("merge",pr,head))
        if self.fail_merge:
            raise recovery.RecoveryHold("MERGE_FAILURE_TEST")
        self.merged = True
        return {"merged":True,"sha": "c"*40}


def test_prepare_accepts_only_finite_narrow_signed_intent():
    assert recovery.parse_intent(intent(), now()) == intent()


@pytest.mark.parametrize("field,value,code",[
    ("head_sha", "a"*39, "INTENT_HEAD_SHA_INVALID"),
    ("main_sha", "b"*39, "INTENT_MAIN_SHA_INVALID"),
    ("removed_check",["WOW Independent QA exact-head",5245832],"INTENT_REMOVED_CHECK_NOT_ALLOWED"),
    ("window_seconds",121,"INTENT_WINDOW_INVALID"),
    ("window_seconds",True,"INTENT_WINDOW_INVALID"),
    ("reviewer_login","gregoryharper84-ship-it","INTENT_REVIEWER_NOT_INDEPENDENT"),
    ("reviewer_login","Copilot","INTENT_REVIEWER_NOT_INDEPENDENT"),
    ("allowed_files",["../../release.py"],"INTENT_FILES_OUTSIDE_MAINTENANCE_SCOPE"),
    ("repo","other/repo","INTENT_IDENTITY_INVALID"),
    ("expires_utc","2026-10-22T14:00:00Z","INTENT_EXPIRED_OR_TOO_LONG"),
 ])
def test_invalid_intent(field, value, code):
    raw=intent();raw[field]=value
    with pytest.raises(recovery.RecoveryHold,match=code):
        recovery.parse_intent(raw, now())


def test_extra_unsigned_field_rejected():
    x=intent();x["commands"]=["rm -rf /"]
    with pytest.raises(recovery.RecoveryHold,match="INTENT_SCHEMA_MISMATCH"):
        recovery.parse_intent(x,now())


def test_temp_rules_remove_only_release_and_preserve_independent_qa():
    b=baseline();t=recovery.temporary_ruleset(b)
    assert recovery.checks_from_ruleset(b) == set(recovery.CHECK_CONTEXTS)
    assert recovery.checks_from_ruleset(t) == set(recovery.CHECK_CONTEXTS[:-1])
    assert t["bypass_actors"] == []
    assert t["rules"][0] == b["rules"][0]


@pytest.mark.parametrize("corrupt",["bypass","missing_qa","duplicate_release"])
def test_ruleset_drift_rejected(corrupt):
    b=baseline()
    if corrupt == "bypass": b["bypass_actors"]=[{"actor_id":1}]
    elif corrupt=="missing_qa": b["rules"][-1]["parameters"]["required_status_checks"].pop(3)
    else: b["rules"][-1]["parameters"]["required_status_checks"].append(dict(context=recovery.CHECK_CONTEXTS[-1][0],integration_id=5245853))
    with pytest.raises(recovery.RecoveryHold):
        recovery.temporary_ruleset(b)


def test_preflight_is_readonly_with_authentic_reviews():
    api=FakeGitHub()
    assert recovery.sha256(recovery.preflight(api,intent())) == intent()["baseline_ruleset_sha256"]
    assert all(call[0]=="get" for call in api.calls)


@pytest.mark.parametrize("change,reason",[
    ("head","PR_HEAD_MOVED_OR_FORK"),
    ("main","MAIN_MOVED"),
    ("no_review","NATIVE_INDEPENDENT_REVIEW_MISSING"),
    ("review_rejected","NATIVE_INDEPENDENT_SECURITY_APPROVAL_INVALID"),
    ("check_failed","LATEST_REQUIRED_APP_CHECK_NOT_SUCCESS"),
    ("check_wrong_app","REQUIRED_APP_CHECK_MISSING"),
    ("auto_merge","OTHER_PR_AUTO_MERGE_ARMED"),
    ("ruleset","RULESET_DIGEST_DRIFT"),
    ("file_scope","PR_FILE_SCOPE_CHANGED"),
])
def test_preflight_rejects_unsafe_inputs(change,reason):
    api=FakeGitHub()
    if change=="head":api.head="d"*40
    if change=="main":api.main="d"*40
    if change=="no_review":api.review_rows=api.review_rows[:1]
    if change=="review_rejected":api.review_rows[-1]["state"]="COMMENTED"
    if change=="check_failed":api.check_rows[0]["conclusion"]="failure"
    if change=="check_wrong_app":api.check_rows[0]["app"]["id"]=34
    if change=="auto_merge":api.other_prs=[{"number":999,"auto_merge":{"enabled":True}}]
    if change=="ruleset":api.ruleset["name"]="unexpected"
    if change=="file_scope":api.file_rows[0]["filename"]=".github/malicious"
    with pytest.raises(recovery.RecoveryHold,match=reason):
        recovery.preflight(api,intent())
    assert not any(c[0]=="put" or c[0]=="merge" for c in api.calls)


def test_latest_qa_failure_overrides_old_success():
    api=FakeGitHub(); old=copy.deepcopy(api.check_rows[-1]);new=copy.deepcopy(old)
    old["id"]=199;new["id"]=200;new["conclusion"]="failure"
    api.check_rows += [old,new]
    with pytest.raises(recovery.RecoveryHold,match="LATEST_REQUIRED_APP_CHECK_NOT_SUCCESS"):
        recovery.preflight(api,intent())


def test_revoked_review_overrides_old_approval():
    api=FakeGitHub();api.review_rows.append({"id":500,"user":{"login":"security-reviewer"},
                                              "state":"CHANGES_REQUESTED","commit_id":HEAD})
    with pytest.raises(recovery.RecoveryHold,match="NATIVE_INDEPENDENT_SECURITY_APPROVAL_INVALID"):
        recovery.preflight(api,intent())


def test_failed_merge_always_restores_all_five_checks(tmp_path):
    api=FakeGitHub();api.fail_merge=True
    with patch.object(recovery,"save_private", wraps=recovery.save_private):
        with pytest.raises(recovery.RecoveryHold,match="MERGE_UNCONFIRMED"):
            recovery.execute(api,intent(),tmp_path/"attempt",verify_witness=lambda: None)
    assert recovery.checks_from_ruleset(api.ruleset)==set(recovery.CHECK_CONTEXTS)
    assert (tmp_path/"attempt/original-ruleset.json").exists()
    calls=[x[0] for x in api.calls if x[0] in {"put","merge"}]
    assert calls==["put","merge","put"]


def test_successful_merge_restores_before_reporting(tmp_path):
    api=FakeGitHub()
    recovery.execute(api,intent(),tmp_path/"attempt",verify_witness=lambda: None)
    assert api.merged and recovery.checks_from_ruleset(api.ruleset)==set(recovery.CHECK_CONTEXTS)
    assert (tmp_path/"attempt/merge-receipt.json").exists()


def test_witness_restores_after_operator_crash():
    api=FakeGitHub(); ticks=[0.0,1.0,4.0,61.0];nowtick=iter(ticks)
    def clk():return next(nowtick)
    reads=0
    def sleeper(_):
        nonlocal reads
        reads+=1
        if reads==1:api.ruleset=recovery.temporary_ruleset(baseline())
    messages=[]
    witness.watch(api,intent(),baseline(),period=0.01,clock=clk,sleep=sleeper,ready=messages.append)
    assert recovery.checks_from_ruleset(api.ruleset)==set(recovery.CHECK_CONTEXTS)
    assert any("RESTORE_EXECUTED" in v for v in messages)


def test_witness_rejects_started_too_late():
    api=FakeGitHub();api.ruleset=recovery.temporary_ruleset(baseline())
    with pytest.raises(recovery.RecoveryHold,match="WITNESS_WAS_NOT_ARMED_BEFORE_RULESET_CHANGE"):
        witness.watch(api,intent(),baseline())


def test_author_cannot_be_sirt_reviewer():
    api=FakeGitHub();api.author="security-reviewer"
    with pytest.raises(recovery.RecoveryHold,match="PR_AUTHOR_CANNOT_BE_SIRT_REVIEWER"):
        recovery.preflight(api,intent())


def test_missing_pull_request_or_default_branch_protection_rejected():
    b=baseline();b["rules"]=[r for r in b["rules"] if r["type"]!="pull_request"]
    with pytest.raises(recovery.RecoveryHold,match="RULESET_PROTECTION_TYPES_MISSING"):
        recovery.temporary_ruleset(b)
    b=baseline();b["conditions"]["ref_name"]["include"]=["refs/heads/feature"]
    with pytest.raises(recovery.RecoveryHold,match="RULESET_BRANCH_SCOPE_CHANGED"):
        recovery.temporary_ruleset(b)

def test_unsigned_witness_readiness_blocks_even_with_valid_pr(tmp_path):
    api=FakeGitHub()
    with pytest.raises(recovery.RecoveryHold, match="WITNESS_ATTESTATION_REQUIRED"):
        recovery.execute(api,intent(),tmp_path/"attempt")
    assert not any(x[0] in {"put","merge"} for x in api.calls)


def test_expired_or_false_witness_identity_rejected(tmp_path):
    signed=recovery.witness_ready_payload(intent(),"another-admin",issued=now(),pid=12)
    ready=tmp_path/"ready.json"
    ready.write_bytes(recovery.canonical(signed))
    sig=tmp_path/"ready.sig";sig.write_text("fake")
    with patch.object(recovery,"verify_signature") as verifier:
        assert recovery.verify_witness_ready(intent(),ready,sig,tmp_path/"signers","owner-admin",
                                              now=now()+dt.timedelta(seconds=10)) == signed
        verifier.assert_called_once()
    with pytest.raises(recovery.RecoveryHold,match="WITNESS_AND_EXECUTOR_NOT_SEPARATE"):
        recovery.verify_witness_ready(intent(),ready,sig,tmp_path/"signers","another-admin",now=now())
    with pytest.raises(recovery.RecoveryHold,match="WITNESS_READY_EXPIRED_OR_FUTURE"):
        recovery.verify_witness_ready(intent(),ready,sig,tmp_path/"signers","owner-admin",
                                      now=now()+dt.timedelta(seconds=80))


def test_mismatched_witness_intent_or_unsigned_schema_fails(tmp_path):
    signed=recovery.witness_ready_payload(intent(),"another-admin",issued=now(),pid=12)
    ready=tmp_path/"ready.json";sig=tmp_path/"ready.sig";sig.write_text("fake")
    signed["baseline_ruleset_sha256"]="0"*64
    ready.write_bytes(recovery.canonical(signed))
    with pytest.raises(recovery.RecoveryHold,match="WITNESS_READY_INTENT_MISMATCH"):
        recovery.verify_witness_ready(intent(),ready,sig,tmp_path/"signers","owner-admin",now=now())
    signed=recovery.witness_ready_payload(intent(),"another-admin",issued=now(),pid=12)
    signed["new_permission"]="admin"
    ready.write_bytes(recovery.canonical(signed))
    with pytest.raises(recovery.RecoveryHold,match="WITNESS_READY_RECEIPT_SCHEMA_INVALID"):
        recovery.verify_witness_ready(intent(),ready,sig,tmp_path/"signers","owner-admin",now=now())


def test_witness_reverified_after_second_preflight(tmp_path):
    api=FakeGitHub();calls=[]
    def confirm():
        calls.append("fresh")
    recovery.execute(api,intent(),tmp_path/"attempt",verify_witness=confirm)
    assert calls==["fresh","fresh"]


def test_real_ssh_signed_witness_startup_and_tamper_detection(tmp_path):
    """Exercise actual ssh-keygen signature verification, not only a mocked API."""
    import subprocess
    import shutil
    if not shutil.which("ssh-keygen"):
        pytest.skip("ssh-keygen not installed in this test environment")
    private = tmp_path / "ephemeral-test-sirt"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private)],
                   check=True, capture_output=True)
    pub = private.with_suffix(".pub").read_text(encoding="utf-8").strip().split()
    signers = tmp_path / "sirt-signers"
    signers.write_text(f'wow-sirt-security-reviewer namespaces="wow-breakglass" {pub[0]} {pub[1]}\n',
                       encoding="utf-8")
    signed = recovery.witness_ready_payload(intent(), "independent-admin", issued=now(), pid=123)
    ready = tmp_path / "ready.json"
    ready.write_bytes(recovery.canonical(signed))
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(private), "-n", "wow-breakglass", str(ready)],
                   check=True, capture_output=True)
    signature = tmp_path / "ready.json.sig"
    result = recovery.verify_witness_ready(intent(), ready, signature, signers, "owner-admin",
                                            now=now() + dt.timedelta(seconds=5))
    assert result["witness_login"] == "independent-admin"
    # Freshness alone is insufficient: editing even one signed byte must HOLD.
    tampered = dict(signed, witness_login="another-admin")
    ready.write_bytes(recovery.canonical(tampered))
    with pytest.raises(recovery.RecoveryHold, match="EXTERNAL_COMMAND_FAILED"):
        recovery.verify_witness_ready(intent(), ready, signature, signers, "owner-admin",
                                      now=now() + dt.timedelta(seconds=5))
