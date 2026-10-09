"""Phase C of #1550: deterministic Independent QA and Release Authority decisions.

The QA and Release workflows run from protected ``main`` only, never execute PR
code, gather exact-head evidence with a read-only token, decide here, and publish
one check run each with their *own* GitHub App's checks-only token. The ruleset
pins ``WOW Independent QA exact-head`` to the QA App and ``WOW Release Authority
exact-head`` to the Release App, so neither principal can satisfy the other's check.

A passing check is evidence for the ruleset, never merge, deployment or probability
authority. Every non-pass is a typed finding. ``can_execute`` is always false.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_identity_policy import (  # noqa: E402
    QA_CHECK, RELEASE_CHECK, REQUIRED_REGRESSION_CHECKS, gh_fetch, governance_trust_roots, is_trust_root,
)

GITHUB_ACTIONS_APP_ID = 15368  # only source trusted for workflow evidence checks
CHANGE_IMPACT_CHECK = "WOW V17 change impact gate"
TRUSTED_GOVERNANCE_CHECK = "Trusted exact-head engineering governance"
QA_EVIDENCE_CHECKS = (*REQUIRED_REGRESSION_CHECKS, CHANGE_IMPACT_CHECK)
REPO_ROOT = Path(__file__).resolve().parents[3]   # protected main checkout in the workflows
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_PAGES = 10
PER_PAGE = 100

Fetch = Callable[[str], tuple[int, Any]]


# ----------------------------------------------------------------------- evidence

def _paged(fetch: Fetch, path: str, key: str | None, errors: list[str], label: str) -> list[dict]:
    items: list[dict] = []
    sep = "&" if "?" in path else "?"
    for page in range(1, MAX_PAGES + 1):
        status, body = fetch(f"{path}{sep}per_page={PER_PAGE}&page={page}")
        rows = body.get(key) if key and isinstance(body, dict) else body
        if status != 200 or not isinstance(rows, list):
            errors.append(f"{label}_UNREADABLE:{status}")
            return items
        items.extend(rows)
        if len(rows) < PER_PAGE:
            if key and isinstance(body, dict) and int(body.get("total_count") or 0) > len(items):
                errors.append(f"{label}_TRUNCATED")
            return items
    errors.append(f"{label}_TRUNCATED")
    return items


def gather(repo: str, pr_number: int, expected_head: str, *, fetch: Fetch = gh_fetch) -> dict[str, Any]:
    """Read-only exact-head evidence. Unreadable or truncated inputs are typed."""
    errors: list[str] = []
    ev: dict[str, Any] = {"repository": repo, "pr_number": pr_number,
                          "expected_head_sha": expected_head, "evidence_errors": errors}
    status, meta = fetch(f"repos/{repo}")
    ev["owner_login"] = (meta or {}).get("owner", {}).get("login") if status == 200 and isinstance(meta, dict) else None
    if not ev["owner_login"]:
        errors.append(f"REPOSITORY_UNREADABLE:{status}")
    status, pr = fetch(f"repos/{repo}/pulls/{pr_number}")
    if status != 200 or not isinstance(pr, dict) or not (pr.get("head") or {}).get("sha"):
        errors.append(f"PULL_REQUEST_UNREADABLE:{status}")
        pr = {}
    ev["pr"] = {
        "state": pr.get("state"), "draft": pr.get("draft"), "merged": pr.get("merged"),
        "head_sha": (pr.get("head") or {}).get("sha"),
        "head_repo": ((pr.get("head") or {}).get("repo") or {}).get("full_name"),
        "base_ref": (pr.get("base") or {}).get("ref"),
        "author": (pr.get("user") or {}).get("login"),
        "changed_files": pr.get("changed_files"),
    }
    files = _paged(fetch, f"repos/{repo}/pulls/{pr_number}/files", None, errors, "FILES")
    ev["files"] = [f.get("filename") for f in files]
    if isinstance(ev["pr"]["changed_files"], int) and len(ev["files"]) < ev["pr"]["changed_files"]:
        errors.append("FILES_TRUNCATED")
    head = ev["pr"]["head_sha"] or expected_head
    runs = _paged(fetch, f"repos/{repo}/commits/{head}/check-runs", "check_runs", errors, "CHECK_RUNS")
    ev["check_runs"] = [{"id": r.get("id"), "name": r.get("name"), "head_sha": r.get("head_sha"),
                         "status": r.get("status"), "conclusion": r.get("conclusion"),
                         "app_id": (r.get("app") or {}).get("id"), "completed_at": r.get("completed_at")}
                        for r in runs]
    reviews = _paged(fetch, f"repos/{repo}/pulls/{pr_number}/reviews", None, errors, "REVIEWS")
    ev["reviews"] = [{"user": (r.get("user") or {}).get("login"), "state": r.get("state"),
                      "commit_id": r.get("commit_id"), "submitted_at": r.get("submitted_at")} for r in reviews]
    return ev


MAX_TARGETS = 20


def targets(repo: str, *, pr: str | None = None,
            workflow_run_prs: list[dict] | None = None, fetch: Fetch = gh_fetch) -> dict[str, Any]:
    """Resolve (PR, exact head) pairs to evaluate. Bounded; never trusts untyped input."""
    errors: list[str] = []
    out: list[dict] = []
    if workflow_run_prs is not None:
        # Each PR's own head SHA: for pull_request_target-triggered upstream runs,
        # workflow_run.head_sha is the BASE branch SHA, never the PR head.
        for item in workflow_run_prs[:MAX_TARGETS]:
            number, sha = item.get("number"), (item.get("head") or {}).get("sha")
            if not isinstance(number, int) or (item.get("base") or {}).get("ref") != "main":
                continue
            if not SHA_RE.match(str(sha or "")):
                errors.append(f"TARGET_HEAD_INVALID:{number}")
                continue
            out.append({"pr": number, "head": sha})
    elif pr is not None:
        if not str(pr).isdigit():
            errors.append("TARGET_PR_INVALID")
        else:
            status, body = fetch(f"repos/{repo}/pulls/{int(pr)}")
            sha = ((body or {}).get("head") or {}).get("sha") if status == 200 and isinstance(body, dict) else None
            if not SHA_RE.match(str(sha or "")):
                errors.append(f"TARGET_PR_UNREADABLE:{status}")
            else:
                out.append({"pr": int(pr), "head": sha})
    else:
        status, body = fetch(f"repos/{repo}/pulls?state=open&base=main&per_page={PER_PAGE}")
        if status != 200 or not isinstance(body, list):
            errors.append(f"TARGET_SWEEP_UNREADABLE:{status}")
        else:
            for item in body:
                if item.get("draft") or ((item.get("head") or {}).get("repo") or {}).get("full_name") != repo:
                    continue
                sha = (item.get("head") or {}).get("sha")
                if SHA_RE.match(str(sha or "")):
                    out.append({"pr": item.get("number"), "head": sha})
            out = out[:MAX_TARGETS]
    return {"targets": out, "errors": errors}


# ----------------------------------------------------------------------- decisions

def _latest(runs: list[dict], name: str, head: str, app_id: int | str) -> dict | None:
    """Latest completed run of ``name`` at ``head`` from exactly ``app_id``."""
    matches = [r for r in runs if r.get("name") == name and r.get("head_sha") == head
               and str(r.get("app_id")) == str(app_id) and r.get("status") == "completed"]
    return max(matches, key=lambda r: (str(r.get("completed_at") or ""), int(r.get("id") or 0)), default=None)


def _owner_approved_at_head(ev: dict, head: str) -> bool:
    owner = ev.get("owner_login")
    mine = [r for r in ev.get("reviews") or [] if r.get("user") == owner and r.get("state") in
            ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")]
    if not mine:
        return False
    last = max(mine, key=lambda r: str(r.get("submitted_at") or ""))
    return last.get("state") == "APPROVED" and last.get("commit_id") == head


def qa_findings(ev: dict, *, governance_files: frozenset[str] | None = None) -> list[str]:
    findings = [f"QA_EVIDENCE_INCOMPLETE:{e}" for e in ev.get("evidence_errors") or []]
    pr = ev.get("pr") or {}
    head = str(pr.get("head_sha") or "")
    expected = str(ev.get("expected_head_sha") or "")
    if not SHA_RE.match(head) or not SHA_RE.match(expected):
        findings.append("QA_HEAD_INVALID")
    elif head != expected:
        findings.append("QA_HEAD_STALE")
    if pr.get("state") != "open" or pr.get("merged"):
        findings.append("QA_PR_NOT_OPEN")
    if pr.get("draft"):
        findings.append("QA_PR_DRAFT")
    if pr.get("base_ref") != "main":
        findings.append("QA_PR_BASE_NOT_MAIN")
    if pr.get("head_repo") != ev.get("repository"):
        findings.append("QA_PR_HEAD_REPOSITORY_MISMATCH")
    files = ev.get("files") or []
    if not files:
        findings.append("QA_FILES_EMPTY")
    runs = ev.get("check_runs") or []
    for name in QA_EVIDENCE_CHECKS:
        run = _latest(runs, name, head, GITHUB_ACTIONS_APP_ID)
        if run is None:
            findings.append(f"QA_EVIDENCE_MISSING:{name}")
        elif run.get("conclusion") != "success":
            findings.append(f"QA_EVIDENCE_FAILED:{name}")
    if governance_files is None:
        governance_files = frozenset(governance_trust_roots(REPO_ROOT))
    if any(is_trust_root(str(f), governance_files) for f in files):
        # Trust roots: .github/, .agents/ and every file a governance workflow executes
        # or reads (including this module). Never autonomous: the sole owner's approval
        # of this exact head is required, and an owner-authored PR can never be
        # self-approved. The trusted gate rejects .github/ changes by design.
        if pr.get("author") == ev.get("owner_login"):
            findings.append("QA_TRUST_ROOT_OWNER_AUTHORED")
        elif not _owner_approved_at_head(ev, head):
            findings.append("QA_TRUST_ROOT_OWNER_APPROVAL_MISSING")
    else:
        run = _latest(runs, TRUSTED_GOVERNANCE_CHECK, head, GITHUB_ACTIONS_APP_ID)
        if run is None:
            findings.append(f"QA_EVIDENCE_MISSING:{TRUSTED_GOVERNANCE_CHECK}")
        elif run.get("conclusion") != "success":
            findings.append(f"QA_EVIDENCE_FAILED:{TRUSTED_GOVERNANCE_CHECK}")
    return sorted(set(findings))


def release_findings(ev: dict, *, qa_app_id: str, release_app_id: str,
                     governance_files: frozenset[str] | None = None) -> list[str]:
    findings: list[str] = []
    if not str(qa_app_id).isdigit() or not str(release_app_id).isdigit():
        findings.append("RELEASE_APP_IDENTITY_UNBOUND")
    elif str(qa_app_id) == str(release_app_id):
        findings.append("RELEASE_IDENTITY_NOT_DISTINCT")
    # Defence in depth: Release independently re-derives every QA condition.
    findings += [f"RELEASE_{f}" for f in qa_findings(ev, governance_files=governance_files)]
    head = str((ev.get("pr") or {}).get("head_sha") or "")
    runs = ev.get("check_runs") or []
    qa = _latest(runs, QA_CHECK, head, qa_app_id) if str(qa_app_id).isdigit() else None
    if qa is None:
        if any(r.get("name") == QA_CHECK and r.get("head_sha") == head for r in runs):
            findings.append("RELEASE_QA_FOREIGN_SOURCE")
        findings.append("RELEASE_QA_MISSING")
    elif qa.get("conclusion") != "success":
        findings.append("RELEASE_QA_FAILED")
    return sorted(set(findings))


def decision(kind: str, findings: list[str], ev: dict) -> dict[str, Any]:
    # Always published at the SHA that was evaluated. A stale trigger therefore marks
    # only the superseded commit and can never overwrite the current head's check.
    head = ev.get("expected_head_sha")
    passed = not findings
    return {
        "check_name": QA_CHECK if kind == "qa" else RELEASE_CHECK,
        "head_sha": head,
        "conclusion": "success" if passed else "failure",
        "findings": findings,
        "title": ("PASS" if passed else f"HOLD: {len(findings)} finding(s)") + f" at {str(head)[:12]}",
        "summary": ("All exact-head conditions satisfied. Evidence only: not merge, deployment or "
                    "probability authority. can_execute=false." if passed else
                    "Typed findings:\n" + "\n".join(f"- `{f}`" for f in findings) +
                    "\n\nFail-closed. can_execute=false."),
        "merge_or_release_authorized": False,
        "can_execute": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("targets")
    t.add_argument("--repo", required=True)
    t.add_argument("--pr")
    t.add_argument("--workflow-run-prs", help="JSON array from github.event.workflow_run.pull_requests")
    g = sub.add_parser("gather")
    g.add_argument("--repo", required=True)
    g.add_argument("--pr", required=True, type=int)
    g.add_argument("--expected-head", required=True)
    for name in ("decide-qa", "decide-release"):
        d = sub.add_parser(name)
        d.add_argument("--evidence", required=True)
        if name == "decide-release":
            d.add_argument("--qa-app-id", required=True)
            d.add_argument("--release-app-id", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "targets":
        prs = json.loads(args.workflow_run_prs) if args.workflow_run_prs else None
        print(json.dumps(targets(args.repo, pr=args.pr, workflow_run_prs=prs)))
        return 0
    if args.cmd == "gather":
        print(json.dumps(gather(args.repo, args.pr, args.expected_head), indent=2))
        return 0
    ev = json.loads(Path(args.evidence).read_text())
    if args.cmd == "decide-qa":
        out = decision("qa", qa_findings(ev), ev)
    else:
        out = decision("release", release_findings(ev, qa_app_id=args.qa_app_id,
                                                   release_app_id=args.release_app_id), ev)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
