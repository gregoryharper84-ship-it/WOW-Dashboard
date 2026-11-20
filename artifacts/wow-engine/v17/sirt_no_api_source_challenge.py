"""Zero-API read-only exact-head SIRT source challenge. Never merge/release approval."""
import argparse,hashlib,json
from pathlib import Path
REPO="gregoryharper84-ship-it/WOW-Dashboard"
HEAD="2e40b78c6aa7e5cecc363b5b607f6e61227dfd7d"
BASE="7f5d327bf3a00869e1e0cce8faf0ded19c4713f7"
WORKFLOW=".github/workflows/wow-v17-independent-qa-check.yml"
TEST=".github/scripts/tests/test_independent_source_review_receipt.py"
CHECKS={"WOW required-three regression":15368,"WOW governed probability backend":15368,
        "WOW additional required regression":15368,"WOW Independent QA exact-head":5245832,
        "WOW Release Authority exact-head":5245853}
def assess(data):
    failures=[]
    def check(ok,code):
        if not ok: failures.append(code)
    pr=data.get("pr") or {};main=data.get("main") or {}
    rs=data.get("ruleset") or {};files=data.get("files") or []
    ci=data.get("checks") or {};app=data.get("sirt_app_id")
    check(pr.get("number")==1932 and pr.get("state")=="open" and pr.get("draft") is False
          and pr.get("merged_at") is None and pr.get("changed_files")==2,"PR_NOT_ELIGIBLE")
    check((pr.get("head") or {}).get("sha")==HEAD and
          ((pr.get("head") or {}).get("repo") or {}).get("full_name")==REPO,"HEAD_NOT_PINNED")
    check((pr.get("base") or {}).get("ref")=="main" and
          (pr.get("base") or {}).get("sha")==BASE and
          ((pr.get("base") or {}).get("repo") or {}).get("full_name")==REPO,"BASE_NOT_PINNED")
    check((main.get("commit") or {}).get("sha")==BASE and main.get("protected") is True,
          "MAIN_MOVED_OR_UNPROTECTED")
    check(type(app) is int and app>0 and app not in {15368,5245832,5245853},
          "SIRT_APP_ID_NOT_SEPARATE")
    check(data.get("global_review_active") is False,"GLOBAL_REVIEW_NOT_OFF")
    check(rs.get("id")==24907422 and rs.get("enforcement")=="active" and
          rs.get("target")=="branch" and rs.get("bypass_actors")==[],
          "RULESET_IDENTITY_CHANGED")
    check((rs.get("conditions") or {}).get("ref_name")==
          {"include":["~DEFAULT_BRANCH"],"exclude":[]},"RULESET_SCOPE_CHANGED")
    rules=rs.get("rules") or []
    check(sorted(r.get("type") for r in rules)==
          sorted(["deletion","non_fast_forward","pull_request","required_status_checks"]),
          "RULESET_STRUCTURE_CHANGED")
    st=next((r.get("parameters") or {} for r in rules
             if r.get("type")=="required_status_checks"),{})
    expected=[{"context":k,"integration_id":v} for k,v in CHECKS.items()]
    actual=st.get("required_status_checks")
    check(st.get("strict_required_status_checks_policy") is False and
          st.get("do_not_enforce_on_create") is False and isinstance(actual,list) and
          sorted(actual,key=lambda x:x.get("context",""))==
          sorted(expected,key=lambda x:x["context"]),"PINNED_CHECKS_CHANGED")
    check(isinstance(files,list) and len(files)==2 and
          {f.get("filename") for f in files if isinstance(f,dict)}=={WORKFLOW,TEST} and
          all(f.get("status")=="modified" for f in files if isinstance(f,dict)),
          "CHANGED_FILES_SCOPE_INVALID")
    wf=next((f for f in files if f.get("filename")==WORKFLOW),{})
    lines=str(wf.get("patch","")).splitlines()
    added=[x for x in lines if x.startswith("+") and not x.startswith("+++")]
    removed=[x for x in lines if x.startswith("-") and not x.startswith("---")]
    check(added==["+run-name: wow-v17-independent-qa-check"] and
          removed==["-run-name: wow-v17-independent-qa-check event=${{ github.event_name }} pr=${{ inputs.pr_number || 'AUTO' }}"],
          "WORKFLOW_PATCH_NOT_DISPLAY_ONLY")
    tp=str(next((f.get("patch","") for f in files if f.get("filename")==TEST),""))
    check("test_qa_policy_accepts_static_workflow_identity_for_github_run_events" in tp and
          "QA_POLICY_RUN_PROVENANCE_MISMATCH" in tp and
          "test_qa_policy_rejects_foreign_failed_or_replayed_producer_run" in tp,
          "ADVERSARIAL_TESTS_NOT_PRESENT")
    cr=ci.get("check_runs") or []
    check(type(ci.get("total_count")) is int and ci["total_count"]<=100,
          "CI_INVENTORY_INCOMPLETE")
    for name in list(CHECKS)[:3]:
        items=[c for c in cr if c.get("name")==name]
        latest=max(items,key=lambda c:(c.get("completed_at") or "",c.get("id",0)),default={})
        check((latest.get("app") or {}).get("id")==15368 and
              latest.get("head_sha")==HEAD and latest.get("status")=="completed" and
              latest.get("conclusion")=="success","CI_NOT_VERIFIED:"+name)
    digest=hashlib.sha256(json.dumps(data,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    return {"schema":"wow-sirt-source-v1","pr":1932,"head_sha":HEAD,"base_sha":BASE,
            "evidence_sha256":digest,
            "source_disposition":"SOURCE_EVIDENCE_HOLD" if failures else "SOURCE_EVIDENCE_PASS",
            "reasons":failures,"independent_sirt_security_approval":False,
            "ruleset_change_authorized":False,"release_approved":False,
            "merge_authorized":False,"deployment_authorized":False,"can_execute":False}
def main():
    p=argparse.ArgumentParser();p.add_argument("--bundle",required=True,type=Path)
    p.add_argument("--output",required=True,type=Path);args=p.parse_args()
    try: result=assess(json.loads(args.bundle.read_text()))
    except Exception as e:
        result={"schema":"wow-sirt-source-v1","source_disposition":"SOURCE_EVIDENCE_HOLD",
                "reasons":["EVIDENCE_INVALID:"+type(e).__name__],
                "independent_sirt_security_approval":False,"ruleset_change_authorized":False,
                "release_approved":False,"merge_authorized":False,
                "deployment_authorized":False,"can_execute":False}
    args.output.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
    print(result["source_disposition"])
    return 0 if result["source_disposition"]=="SOURCE_EVIDENCE_PASS" else 1
if __name__=="__main__":raise SystemExit(main())
