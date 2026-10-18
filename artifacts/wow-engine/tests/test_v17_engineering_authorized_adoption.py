"""P0 #1845 — adoption must not forge approvals or overwrite unleased refs."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
FILE = ROOT / "artifacts/wow-engine/v17/engineering_authorized_adoption.py"
WF = ROOT / ".github/workflows/wow-v17-engineering-authorized-adoption.yml"
spec = spec_from_file_location("wow_engineering_authorized_adoption", FILE)
mod = module_from_spec(spec)
spec.loader.exec_module(mod)

def fixtures():
    t = {"number":1834, "state":"open", "head":{
        "sha":mod.EXPECTED_TARGET, "ref":mod.TARGET_BRANCH,
        "repo":{"full_name":mod.REPO}}, "base":{"ref":"main"}}
    s = {"number":1846, "state":"open", "head":{
        "sha":mod.EXPECTED_SOURCE, "ref":"repair/p0-1834-combined-release-safety-fence",
        "repo":{"full_name":mod.REPO}}, "base":{"ref":mod.TARGET_BRANCH}}
    cmp = {"status":"ahead", "ahead_by":21, "behind_by":0, "too_large":False,
           "files":[{"filename":p} for p in sorted(mod.EXPECTED_PATHS)]}
    return t, s, cmp

def call(t, s, c):
    return mod.validate_prs(t, s, c, target_sha=mod.EXPECTED_TARGET,
                            source_sha=mod.EXPECTED_SOURCE, branch=mod.TARGET_BRANCH)

def test_adoptable_exact_branch_and_fixed_scope():
    assert call(*fixtures()) == "ADOPTABLE"

def test_already_adopted_is_idempotent():
    t,s,c=fixtures()
    t["head"]["sha"]=mod.EXPECTED_SOURCE
    assert call(t,s,c) == "ALREADY_ADOPTED_NO_ACTION"

@pytest.mark.parametrize("mutation,code", [
    ("stale_target","TARGET_HEAD_STALE_OR_OTHER_WRITER_HOLD"),
    ("foreign_target","PR_REPOSITORY_OR_STATE_UNTRUSTED_HOLD"),
    ("foreign_source","PR_REPOSITORY_OR_STATE_UNTRUSTED_HOLD"),
    ("source_drift","SOURCE_PR_PROVENANCE_HOLD"),
    ("non_ff","NON_FAST_FORWARD_OR_MISSING_ANCESTRY_HOLD"),
    ("behind","NON_FAST_FORWARD_OR_MISSING_ANCESTRY_HOLD"),
    ("extra_path","UNEXPECTED_SOURCE_SCOPE_HOLD"),
    ("missing_path","UNEXPECTED_SOURCE_SCOPE_HOLD"),
    ("wrong_target_branch","ENGINEERING_TARGET_PROVENANCE_HOLD"),
    ("wrong_base","ENGINEERING_TARGET_PROVENANCE_HOLD"),
])
def test_unsafe_integrations_hold(mutation,code):
    t,s,c=fixtures()
    if mutation=="stale_target": t["head"]["sha"]="c"*40
    if mutation=="foreign_target": t["head"]["repo"]["full_name"]="attacker/repo"
    if mutation=="foreign_source": s["head"]["repo"]["full_name"]="attacker/repo"
    if mutation=="source_drift": s["head"]["sha"]="d"*40
    if mutation=="non_ff": c["status"]="diverged"
    if mutation=="behind": c["behind_by"]=1
    if mutation=="extra_path": c["files"].append({"filename":".github/workflows/unexpected.yml"})
    if mutation=="missing_path": c["files"].pop()
    if mutation=="wrong_target_branch": t["head"]["ref"]="other"
    if mutation=="wrong_base": t["base"]["ref"]="other"
    with pytest.raises(mod.AdoptionHold,match=code):
        call(t,s,c)

def test_caller_cannot_change_registered_repo_or_pr(monkeypatch):
    monkeypatch.setattr(mod,"gh",lambda p: (_ for _ in ()).throw(AssertionError("untrusted API call")))
    kwargs={"repo":mod.REPO, "target_pr":1834, "source_pr":1846,
            "expected_target":mod.EXPECTED_TARGET, "expected_source":mod.EXPECTED_SOURCE,
            "target_branch":mod.TARGET_BRANCH}
    for changed in [{"repo":"attacker/repo"}, {"target_pr":999},
                    {"source_pr":1000}, {"target_branch":"main"}]:
        with pytest.raises(mod.AdoptionHold,match="UNREGISTERED_PR_OR_BRANCH_HOLD"):
            mod.plan(**(kwargs|changed))

def test_no_app_token_prevents_mutation(monkeypatch):
    monkeypatch.delenv("APP_TOKEN",raising=False)
    monkeypatch.delenv("GH_TOKEN",raising=False)
    with pytest.raises(mod.AdoptionHold,match="AUTHENTICATED_ENGINEERING_APP_TOKEN_MISSING_HOLD"):
        mod.adopt(repo=mod.REPO,target_pr=1834,source_pr=1846,
                  expected_target=mod.EXPECTED_TARGET,
                  expected_source=mod.EXPECTED_SOURCE,target_branch=mod.TARGET_BRANCH)

def test_trust_boundary_and_release_gates_unchanged():
    text=WF.read_text()
    workflow=yaml.safe_load(text)
    triggers=workflow.get("on",workflow.get(True,{}))
    assert set(triggers)=={"schedule","workflow_dispatch"}
    job=workflow["jobs"]["adopt"]
    assert job["environment"]=="wow-engineering"
    assert workflow["concurrency"]["group"]=="wow-v17-engineering-implementation-single-writer"
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert "vars.WOW_1834_ENGINEERING_ADOPTION_AUTHORIZED == 'true'" in job["if"]
    assert "github.ref == 'refs/heads/main'" in job["if"]
    assert workflow["permissions"]=={"contents":"read","pull-requests":"read","issues":"read"}
    assert ".github/actions/wow-engineering-token" in text
    assert "vars.WOW_ENGINEERING_APP_ID" in text
    assert "secrets.WOW_ENGINEERING_APP_PRIVATE_KEY" in text
    assert "secrets.WOW_QA_APP_PRIVATE_KEY" not in text
    assert "secrets.WOW_RELEASE_APP_PRIVATE_KEY" not in text
    assert "RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY" in text
    assert "ALREADY_ADOPTED_NO_ACTION" in text
    assert "workflow_run:" not in text
    assert "pull_request_target:" not in text
    assert "issues: write" not in text
    controller=FILE.read_text()
    assert '"push", "origin"' in controller
    assert "force-with-lease" not in controller
    assert "git push -f" not in controller
    assert "git reset --hard" not in controller
    assert "PR_REPOSITORY_OR_STATE_UNTRUSTED_HOLD" in controller
