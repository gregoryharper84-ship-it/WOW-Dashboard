"""Owner-only emergency Release recovery: positive and adversarial contracts."""
from __future__ import annotations
import copy
import datetime as dt
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import owner_release_recovery as recovery
import owner_release_watchdog as watchdog

HEAD = "a"*40
MAIN = "b"*40
FILES = [".github/workflows/wow-v17-independent-qa-check.yml",
         ".github/scripts/tests/test_independent_source_review_receipt.py"]

def baseline():
    return {"name":"wow-v17-main-agent-separation","target":"branch","enforcement":"active",
            "conditions":{"ref_name":{"exclude":[],"include":["~DEFAULT_BRANCH"]}},
            "bypass_actors":[],
            "rules":[{"type":"deletion"},{"type":"non_fast_forward"},
                     {"type":"pull_request","parameters":{"required_approving_review_count":0}},
                     {"type":"required_status_checks","parameters":{
                       "strict_required_status_checks_policy":False,
                       "required_status_checks":[{"context":n,"integration_id":a}
                          for n,a in recovery.CHECK_CONTEXTS]}}]}

def intent():
    return {"schema":recovery.MODE,"repo":recovery.CANONICAL_REPO,
        "ruleset_id":recovery.RULESET_ID,"pr":1932,"head_sha":HEAD,
        "main_sha":MAIN,"incident":1930,"allowed_files":FILES,
        "baseline_ruleset_sha256":recovery.sha256(baseline()),
        "expires_utc":(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=25)).isoformat().replace("+00:00","Z"),
        "window_seconds":75,"removed_check":list(recovery.CHECK_CONTEXTS[-1]),
        "owner_risk_acceptance":recovery.RISK}

def checks():
    return [{"name":n,"app":{"id":a},"id":i+1,"head_sha":HEAD,
             "status":"completed","conclusion":"success"}
            for i,(n,a) in enumerate(recovery.CHECK_CONTEXTS[:-1])]

class FakeAPI:
    def __init__(self):
        self.rules=baseline()
        self.rows=checks()
        self.review=[{"id":30,"user":{"login":recovery.OWNER},
                      "state":"APPROVED","commit_id":HEAD}]
        self.main=MAIN
        self.head=HEAD
        self.files=[{"filename":f} for f in FILES]
        self.armed=False
        self.merged=False
        self.merge_fail=False
        self.puts=[]
    def get(self,suffix):
        if suffix=="":
            return {"permissions":{"admin":True}}
        if suffix.startswith("pulls/1932"):
            return {"state":"open" if not self.merged else "closed",
                    "draft":False,"merged":self.merged,"changed_files":len(self.files),
                    "head":{"sha":self.head,"repo":{"full_name":recovery.CANONICAL_REPO}},
                    "base":{"ref":"main"}}
        if suffix=="git/ref/heads/main":return {"object":{"sha":self.main}}
        if suffix==f"rulesets/{recovery.RULESET_ID}":return copy.deepcopy(self.rules)
        raise AssertionError("Unexpected GET "+suffix)
    def pages(self,suffix,collection=None):
        if suffix.startswith("pulls/1932/files"):return copy.deepcopy(self.files)
        if suffix.startswith("pulls/1932/reviews"):return copy.deepcopy(self.review)
        if suffix.startswith("commits/"):return copy.deepcopy(self.rows)
        if suffix.startswith("pulls?state=open"):
            return [{"number":777,"auto_merge":{"enabled":True}}] if self.armed else []
        raise AssertionError("Unexpected pages "+suffix)
    def put(self,suffix,body):
        self.rules=copy.deepcopy(body)
        self.puts.append(copy.deepcopy(body))
        return body
    def merge(self,pr,sha):
        assert pr==1932 and sha==HEAD
        if self.merge_fail:raise recovery.RecoveryHold("SIMULATED_MERGE_FAILURE")
        self.merged=True
        return {"merged":True,"sha":"c"*40}

def test_one_owner_intent_valid_without_second_human():
    assert recovery.parse_intent(intent())["owner_risk_acceptance"]==recovery.RISK
    assert "reviewer_login" not in intent()
    assert "sirt_signature" not in intent()

@pytest.mark.parametrize("change",[
    {"owner_risk_acceptance":"NO"},
    {"removed_check":["WOW Independent QA exact-head",5245832]},
    {"window_seconds":121},
    {"allowed_files":[".github/workflows/../secrets.yml"]},
    {"allowed_files":["src/model.py"]},
    {"repo":"another/repository"},
    {"ruleset_id":1234},
])
def test_signed_intent_invalid_modifications(change):
    x=intent();x.update(change)
    with pytest.raises(recovery.RecoveryHold):
        recovery.parse_intent(x)

def test_owner_only_preflight_passes_with_real_owner_review_and_four_app_checks():
    api=FakeAPI()
    assert recovery.preflight(api,intent())==baseline()

def test_wrong_app_or_latest_failure_cannot_pass():
    api=FakeAPI()
    api.rows[0]["app"]={"id":100}
    with pytest.raises(recovery.RecoveryHold):
        recovery.preflight(api,intent())
    api=FakeAPI()
    api.rows.append({**api.rows[0],"id":999,"conclusion":"failure"})
    with pytest.raises(recovery.RecoveryHold):
        recovery.preflight(api,intent())

def test_older_owner_approval_superseded_by_reject_fails():
    api=FakeAPI()
    api.review.append({"id":40,"user":{"login":recovery.OWNER},
                       "state":"CHANGES_REQUESTED","commit_id":HEAD})
    with pytest.raises(recovery.RecoveryHold):
        recovery.preflight(api,intent())

def test_drift_and_auto_merge_refused():
    api=FakeAPI();api.main="d"*40
    with pytest.raises(recovery.RecoveryHold):recovery.preflight(api,intent())
    api=FakeAPI();api.armed=True
    with pytest.raises(recovery.RecoveryHold):recovery.preflight(api,intent())
    api=FakeAPI();api.files.append({"filename":"README.md"})
    with pytest.raises(recovery.RecoveryHold):recovery.preflight(api,intent())

def test_owner_signer_fingerprint_rejects_fake_key(tmp_path):
    p=tmp_path/"signers"
    p.write_text('wow-owner namespaces="wow-breakglass" ssh-ed25519 '+("A"*50)+' impostor\n')
    with pytest.raises(recovery.RecoveryHold):recovery.owner_signer_file(p)

class Proc:
    def poll(self):return None

def test_success_exact_merge_restores_five_check_policy(tmp_path,monkeypatch):
    api=FakeAPI()
    monkeypatch.setattr(recovery,"authenticate_admin",lambda x: None)
    monkeypatch.setattr(recovery,"_watchdog_process",lambda *a: Proc())
    recovery.execute(api,intent(),tmp_path/"source",tmp_path/"journal")
    assert api.merged
    assert len(api.puts)==2
    assert recovery.checks_from_ruleset(api.puts[0])==recovery.expected_check_set()-{recovery.CHECK_CONTEXTS[-1]}
    assert api.rules==baseline()
    assert (tmp_path/"journal"/"merge-receipt.json").exists()

def test_merge_failure_restores_five_check_policy(tmp_path,monkeypatch):
    api=FakeAPI();api.merge_fail=True
    monkeypatch.setattr(recovery,"authenticate_admin",lambda x: None)
    monkeypatch.setattr(recovery,"_watchdog_process",lambda *a: Proc())
    with pytest.raises(recovery.RecoveryHold,match="MERGE_FAILED"):
        recovery.execute(api,intent(),tmp_path/"source",tmp_path/"journal")
    assert api.rules==baseline() and len(api.puts)==2

def test_ruleset_put_uncertain_restoration(tmp_path,monkeypatch):
    api=FakeAPI()
    orig=api.put
    count={"n":0}
    def ambiguous(s,b):
        count["n"]+=1
        v=orig(s,b)
        if count["n"]==1:raise recovery.RecoveryHold("PUT_TIMEOUT_AFTER_WRITE")
        return v
    api.put=ambiguous
    monkeypatch.setattr(recovery,"authenticate_admin",lambda x: None)
    monkeypatch.setattr(recovery,"_watchdog_process",lambda *a: Proc())
    with pytest.raises(recovery.RecoveryHold,match="MERGE_FAILED"):
        recovery.execute(api,intent(),tmp_path/"source",tmp_path/"journal")
    assert api.rules==baseline()

def test_witness_restores_if_operator_dies(tmp_path):
    api=FakeAPI()
    state={"tick":0}
    original_get=api.get
    def switched_get(s):
        if s.startswith("rulesets/"):
            state["tick"]+=1
            if state["tick"]>=2 and state["tick"]<6:
                return recovery.temporary_ruleset(baseline())
        return original_get(s)
    api.get=switched_get
    times=iter([0,0,100])
    watchdog.watch(api,intent(),baseline(),tmp_path/"ready","f"*64,
                   period=0,clock=lambda: next(times),sleep=lambda _:None)
    assert (tmp_path/"ready").exists()
    assert api.rules==baseline()

def test_watchdog_does_not_arm_on_wrong_baseline(tmp_path):
    api=FakeAPI()
    x=intent();x["baseline_ruleset_sha256"]="f"*64
    with pytest.raises(recovery.RecoveryHold):
        watchdog.watch(api,x,baseline(),tmp_path/"ready","f"*64)
    assert not (tmp_path/"ready").exists()


def test_actual_owner_signature_and_tamper(tmp_path, monkeypatch):
    """Exercise real OpenSSH signature verification with a disposable test key."""
    import subprocess, shutil, base64, hashlib
    if not shutil.which("ssh-keygen"):
        pytest.skip("OpenSSH signing binary unavailable")
    key=tmp_path/"owner-test"
    subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",str(key)],
                   check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    pub=key.with_suffix(".pub").read_text().split()
    digest="SHA256:"+base64.b64encode(hashlib.sha256(base64.b64decode(pub[1])).digest()).decode().rstrip("=")
    monkeypatch.setattr(recovery,"PINNED_OWNER_KEY_FINGERPRINT",digest)
    signers=tmp_path/"allowed_signers"
    signers.write_text('wow-owner namespaces="wow-breakglass" '+pub[0]+' '+pub[1]+' owner-test\\n')
    payload=tmp_path/"intent.json"
    payload.write_bytes(recovery.canonical(intent()))
    subprocess.run(["ssh-keygen","-Y","sign","-n","wow-breakglass","-f",str(key),str(payload)],
                   check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    sig=tmp_path/"intent.json.sig"
    recovery.verify_owner_signature(payload.read_bytes(),sig,signers)
    with pytest.raises(recovery.RecoveryHold):
        recovery.verify_owner_signature(payload.read_bytes()+b"wrong",sig,signers)

def test_pinned_owner_signer_rejects_unregistered_namespace(tmp_path):
    p=tmp_path/"allowed"
    p.write_text('wow-owner namespaces="wow-release" ssh-ed25519 AAAA owner\\n')
    with pytest.raises(recovery.RecoveryHold):
        recovery.owner_signer_file(p)
