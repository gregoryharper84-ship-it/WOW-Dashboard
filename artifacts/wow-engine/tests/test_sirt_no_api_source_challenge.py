"""Adversarial fixtures for the zero-credit, source-only SIRT reviewer."""
import importlib.util
from pathlib import Path
import pytest

FILE=Path(__file__).resolve().parents[1]/"v17"/"sirt_no_api_source_challenge.py"
spec=importlib.util.spec_from_file_location("sirt_no_api_source_challenge",FILE)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def sample():
    return {
      "pr":{"number":1932,"state":"open","draft":False,"merged_at":None,
            "changed_files":2,
            "head":{"sha":mod.HEAD,"repo":{"full_name":mod.REPO}},
            "base":{"sha":mod.BASE,"ref":"main","repo":{"full_name":mod.REPO}}},
      "main":{"protected":True,"commit":{"sha":mod.BASE}},
      "ruleset":{"id":24907422,"enforcement":"active","target":"branch",
          "bypass_actors":[],"conditions":{"ref_name":{"include":["~DEFAULT_BRANCH"],"exclude":[]}},
          "rules":[{"type":"deletion"},{"type":"non_fast_forward"},{"type":"pull_request"},
                   {"type":"required_status_checks","parameters":{
                       "strict_required_status_checks_policy":False,
                       "do_not_enforce_on_create":False,
                       "required_status_checks":[{"context":k,"integration_id":v}
                                                   for k,v in mod.CHECKS.items()]}}]},
      "files":[{"filename":mod.WORKFLOW,"status":"modified",
          "patch":"@@ -1,4 +1,4 @@\n-run-name: wow-v17-independent-qa-check event=${{ github.event_name }} pr=${{ inputs.pr_number || 'AUTO' }}\n+run-name: wow-v17-independent-qa-check\n"},
          {"filename":mod.TEST,"status":"modified",
           "patch":"+def test_qa_policy_accepts_static_workflow_identity_for_github_run_events(event):\n"
              "+ assert 'QA_POLICY_RUN_PROVENANCE_MISMATCH'\n"
              " def test_qa_policy_rejects_foreign_failed_or_replayed_producer_run():\n"}],
      "checks":{"total_count":3,"check_runs":[{
         "id":100+i,"name":name,"app":{"id":15368},"head_sha":mod.HEAD,
         "status":"completed","conclusion":"success","completed_at":"2026-10-21T20:00:00Z"}
         for i,name in enumerate(list(mod.CHECKS)[:3])]},
      "sirt_app_id":9999999,"global_review_active":False
    }

def test_positive_is_never_approval():
    r=mod.assess(sample())
    assert r["source_disposition"]=="SOURCE_EVIDENCE_PASS",r
    assert len(r["evidence_sha256"])==64
    for key in ("independent_sirt_security_approval","ruleset_change_authorized",
                "release_approved","merge_authorized","deployment_authorized","can_execute"):
        assert r[key] is False

@pytest.mark.parametrize(("path","value","reason"),[
    (("pr","head","sha"),"f"*40,"HEAD_NOT_PINNED"),
    (("pr","base","sha"),"f"*40,"BASE_NOT_PINNED"),
    (("pr","draft"),True,"PR_NOT_ELIGIBLE"),
    (("pr","head","repo","full_name"),"foreign/repo","HEAD_NOT_PINNED"),
    (("main","protected"),False,"MAIN_MOVED_OR_UNPROTECTED"),
    (("ruleset","bypass_actors"),[{"actor_id":5}],"RULESET_IDENTITY_CHANGED"),
    (("ruleset","enforcement"),"disabled","RULESET_IDENTITY_CHANGED"),
    (("ruleset","rules",3,"parameters","required_status_checks",4,"integration_id"),1,"PINNED_CHECKS_CHANGED"),
    (("files",0,"patch"),"malicious patch","WORKFLOW_PATCH_NOT_DISPLAY_ONLY"),
    (("files",1,"patch"),"no tests","ADVERSARIAL_TESTS_NOT_PRESENT"),
    (("checks","check_runs",0,"conclusion"),"failure","CI_NOT_VERIFIED:WOW required-three regression"),
    (("sirt_app_id",),5245832,"SIRT_APP_ID_NOT_SEPARATE"),
    (("global_review_active",),True,"GLOBAL_REVIEW_NOT_OFF"),
])
def test_fail_closed_cases(path,value,reason):
    data=sample()
    cursor=data
    for part in path[:-1]:cursor=cursor[part]
    cursor[path[-1]]=value
    result=mod.assess(data)
    assert result["source_disposition"]=="SOURCE_EVIDENCE_HOLD"
    assert reason in result["reasons"]
    assert result["merge_authorized"] is False

def test_later_bad_check_overrides_prior_green():
    data=sample()
    data["checks"]["check_runs"].append({
      "id":200,"name":"WOW required-three regression","app":{"id":15368},
      "head_sha":mod.HEAD,"status":"completed","conclusion":"failure",
      "completed_at":"2026-10-21T21:00:00Z"})
    data["checks"]["total_count"]=4
    assert "CI_NOT_VERIFIED:WOW required-three regression" in mod.assess(data)["reasons"]

def test_incomplete_inventory_fails_closed():
    data=sample()
    data["checks"]["total_count"]=101
    assert "CI_INVENTORY_INCOMPLETE" in mod.assess(data)["reasons"]
