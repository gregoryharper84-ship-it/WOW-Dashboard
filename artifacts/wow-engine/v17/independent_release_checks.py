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
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[3]   # protected main checkout in the workflows
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_identity_policy import (  # noqa: E402
    QA_CHECK, RELEASE_CHECK, REQUIRED_REGRESSION_CHECKS, gh_fetch, governance_trust_roots, is_trust_root,
)

SOURCE_REVIEW_SCRIPT = REPO_ROOT / ".github/scripts/independent_source_review_receipt.py"
_source_review_spec = importlib.util.spec_from_file_location(
    "_independent_source_review_receipt", SOURCE_REVIEW_SCRIPT
)
_source_review_module = importlib.util.module_from_spec(_source_review_spec)
assert _source_review_spec and _source_review_spec.loader
_source_review_spec.loader.exec_module(_source_review_module)

GITHUB_ACTIONS_APP_ID = 15368  # only source trusted for workflow evidence checks
CHANGE_IMPACT_CHECK = "WOW V17 change impact gate"
TRUSTED_GOVERNANCE_CHECK = "Trusted exact-head engineering governance"
QA_EVIDENCE_CHECKS = (*REQUIRED_REGRESSION_CHECKS, CHANGE_IMPACT_CHECK)
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


def gather(
    repo: str,
    pr_number: int,
    expected_head: str,
    *,
    fetch: Fetch = gh_fetch,
    source_review_resolver: Callable[..., dict[str, Any]] | None = None,
    source_review_data: dict[str, Any] | None = None,
    qa_review_policy: dict[str, Any] | None = None,
    current_review_active: bool = False,
    independent_review_active: bool = True,
    zero_credit_enabled: bool = False,
    source_review_producer_context: bool = False,
    source_review_run_id: int | None = None,
    source_review_run_attempt: int | None = None,
) -> dict[str, Any]:
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
        "head_ref": (pr.get("head") or {}).get("ref"),
        "head_repo": ((pr.get("head") or {}).get("repo") or {}).get("full_name"),
        "base_ref": (pr.get("base") or {}).get("ref"),
        "author": (pr.get("user") or {}).get("login"),
        "author_id": (pr.get("user") or {}).get("id"),
        "changed_files": pr.get("changed_files"),
    }
    files = _paged(fetch, f"repos/{repo}/pulls/{pr_number}/files", None, errors, "FILES")
    # Both ends of a rename are security-relevant. A protected source path
    # renamed outside its protected prefix must still require owner approval.
    # Track the number of PR file records separately: including rename origins
    # must not disguise an incomplete paginated file inventory.
    ev["files"] = []
    ev["source_review_paths"] = []
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str) or not entry["filename"]:
            errors.append("FILES_PATH_INVALID")
            continue
        ev["files"].append(entry["filename"])
        ev["source_review_paths"].append(entry["filename"])
        if entry.get("status") == "renamed" or "previous_filename" in entry:
            origin = entry.get("previous_filename")
            if isinstance(origin, str) and origin:
                ev["files"].append(origin)
            else:
                errors.append("FILES_RENAME_ORIGIN_MISSING")
    if isinstance(ev["pr"]["changed_files"], int) and len(files) < ev["pr"]["changed_files"]:
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
    ev["qa_review_policy"] = qa_review_policy
    ev["current_review_active"] = current_review_active
    ev["zero_credit_enabled"] = zero_credit_enabled
    if qa_review_policy is not None:
        mode = qa_review_policy.get("mode") if isinstance(qa_review_policy, dict) else None
        if mode not in {"legacy", "independent", "canary", "zero_credit"}:
            errors.append("QA_REVIEW_POLICY_MODE_INVALID")
        else:
            independent_review_active = mode in {"independent", "canary"}
            source = qa_review_policy.get("source") or {}
            try:
                source_review_run_id = int(source.get("run_id"))
                source_review_run_attempt = source.get("run_attempt")
            except (TypeError, ValueError):
                errors.append("QA_REVIEW_POLICY_RUN_ID_INVALID")
    ev["independent_review_active"] = independent_review_active
    if independent_review_active:
        if source_review_data is not None:
            ev["source_review"] = {
                **source_review_data,
                "producer_context": source_review_producer_context,
            }
        else:
            try:
                resolver = source_review_resolver or _source_review_module.resolve_current_source_review
                ev["source_review"] = resolver(
                    repository=repo,
                    pr_number=pr_number,
                    head_sha=str(ev["pr"]["head_sha"] or ""),
                    head_ref=str(ev["pr"]["head_ref"] or ""),
                    token=os.environ.get("GH_TOKEN", ""),
                    expected_file_paths=ev["source_review_paths"],
                    expected_workflow_run_id=source_review_run_id,
                    expected_workflow_run_attempt=source_review_run_attempt,
                )
            except _source_review_module.SourceReviewError as exc:
                ev["source_review"] = None
                errors.append(f"SOURCE_REVIEW_UNAVAILABLE:{exc}")
    else:
        ev["source_review"] = None
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
            status, live = fetch(f"repos/{repo}/pulls/{number}")
            head = (live or {}).get("head") or {}
            base = (live or {}).get("base") or {}
            if status != 200 or not isinstance(live, dict):
                errors.append(f"TARGET_PR_UNREADABLE:{number}:{status}")
                continue
            if (
                live.get("number") != number
                or head.get("sha") != sha
                or (head.get("repo") or {}).get("full_name") != repo
                or base.get("ref") != "main"
                or (base.get("repo") or {}).get("full_name") != repo
            ):
                errors.append(f"TARGET_PR_IDENTITY_MISMATCH:{number}")
                continue
            out.append({"pr": number, "head": sha, "head_ref": head.get("ref")})
    elif pr is not None:
        if not str(pr).isdigit():
            errors.append("TARGET_PR_INVALID")
        else:
            status, body = fetch(f"repos/{repo}/pulls/{int(pr)}")
            sha = ((body or {}).get("head") or {}).get("sha") if status == 200 and isinstance(body, dict) else None
            if not SHA_RE.match(str(sha or "")):
                errors.append(f"TARGET_PR_UNREADABLE:{status}")
            elif (
                body.get("number") != int(pr)
                or ((body.get("head") or {}).get("repo") or {}).get("full_name") != repo
                or (body.get("base") or {}).get("ref") != "main"
                or ((body.get("base") or {}).get("repo") or {}).get("full_name") != repo
            ):
                errors.append("TARGET_PR_IDENTITY_MISMATCH")
            else:
                out.append({"pr": int(pr), "head": sha, "head_ref": (body.get("head") or {}).get("ref")})
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
                    out.append({"pr": item.get("number"), "head": sha,
                                "head_ref": ((item.get("head") or {}).get("ref"))})
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
    if governance_files is None:
        governance_files = frozenset(governance_trust_roots(REPO_ROOT))
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
    review_active = ev.get("independent_review_active", True)
    if not isinstance(review_active, bool):
        findings.append("QA_REVIEW_POLICY_INVALID")
    source_review = ev.get("source_review")
    if review_active and not isinstance(source_review, dict):
        findings.append("QA_INDEPENDENT_SOURCE_REVIEW_MISSING")
    elif review_active and isinstance(source_review, dict):
        try:
            if not isinstance(source_review.get("artifact_id"), int) or isinstance(
                source_review.get("artifact_id"), bool
            ):
                raise _source_review_module.SourceReviewError("SOURCE_REVIEW_ARTIFACT_ID_INVALID")
            artifact = source_review.get("artifact")
            target = artifact.get("target") if isinstance(artifact, dict) else None
            diff = artifact.get("diff") if isinstance(artifact, dict) else None
            author = target.get("author") if isinstance(target, dict) else None
            artifact_files = diff.get("file_paths") if isinstance(diff, dict) else None
            if (
                not isinstance(target, dict)
                or target.get("pr_number") != ev.get("pr_number")
                or not isinstance(author, dict)
                or author.get("login") != (pr.get("author") or "")
                or author.get("id") != pr.get("author_id")
                or artifact_files != sorted(ev.get("source_review_paths") or [])
            ):
                raise _source_review_module.SourceReviewError("SOURCE_REVIEW_TARGET_OR_DIFF_MISMATCH")
            _source_review_module.verify_artifact(
                artifact,
                run=source_review.get("run") or {},
                expected_repository=str(ev.get("repository") or ""),
                expected_pr_number=int(ev.get("pr_number") or 0),
                expected_head_sha=head,
                expected_head_ref=str(pr.get("head_ref") or ""),
                producer_context=source_review.get("producer_context") is True,
            )
            review = artifact.get("review") if isinstance(artifact, dict) else None
            if isinstance(review, dict) and review.get("decision") == "REJECT":
                findings.append("QA_INDEPENDENT_SOURCE_REVIEW_REJECTED")
        except _source_review_module.SourceReviewError as exc:
            findings.append(f"QA_INDEPENDENT_SOURCE_REVIEW_INVALID:{exc}")
    files = ev.get("files") or []
    if not files:
        findings.append("QA_FILES_EMPTY")
    policy = ev.get("qa_review_policy")
    if policy is not None:
        mode = policy.get("mode") if isinstance(policy, dict) else None
        target = policy.get("target") if isinstance(policy, dict) else None
        scope = policy.get("canary_scope") if isinstance(policy, dict) else None
        if mode not in {"legacy", "independent", "canary", "zero_credit"}:
            findings.append("QA_REVIEW_POLICY_MODE_INVALID")
        elif mode == "zero_credit":
            # Reduced-assurance evidence checks only: not adversarial source review.
            if ev.get("zero_credit_enabled") is not True:
                findings.append("QA_ZERO_CREDIT_POLICY_NOT_ENABLED")
            if ev.get("current_review_active") is True or ev.get("independent_review_active") is not False:
                findings.append("QA_ZERO_CREDIT_POLICY_CONFLICT")
            if ev.get("source_review") is not None:
                findings.append("QA_ZERO_CREDIT_SOURCE_REVIEW_UNEXPECTED")
            if not isinstance(target, dict) or (target.get("pr_number") != ev.get("pr_number")
                    or target.get("head_sha") != head
                    or target.get("head_ref") != (pr.get("head_ref") or "")):
                findings.append("QA_ZERO_CREDIT_POLICY_TARGET_MISMATCH")
        elif mode == "legacy":
            if ev.get("independent_review_active") is not False:
                findings.append("QA_REVIEW_POLICY_MODE_CONFLICT")
            if ev.get("current_review_active") is True:
                findings.append("QA_REVIEW_POLICY_LEGACY_AFTER_ACTIVATION")
        else:
            if ev.get("independent_review_active") is not True:
                findings.append("QA_REVIEW_POLICY_MODE_CONFLICT")
            if not isinstance(target, dict) or (
                target.get("pr_number") != ev.get("pr_number")
                or target.get("head_sha") != head
                or target.get("head_ref") != (pr.get("head_ref") or "")
            ):
                findings.append("QA_REVIEW_POLICY_TARGET_MISMATCH")
            if mode == "canary":
                if scope != {"pr_number": ev.get("pr_number"), "head_sha": head}:
                    findings.append("QA_REVIEW_CANARY_SCOPE_MISMATCH")
                if pr.get("author") == ev.get("owner_login"):
                    findings.append("QA_REVIEW_CANARY_OWNER_AUTHORED")
                if any(is_trust_root(str(path), governance_files) for path in files):
                    findings.append("QA_REVIEW_CANARY_TRUST_ROOT")
    runs = ev.get("check_runs") or []
    expected_checks = QA_EVIDENCE_CHECKS
    trust_root_change = any(is_trust_root(str(f), governance_files) for f in files)
    if review_active is False and not trust_root_change and (policy or {}).get("mode") != "zero_credit":
        expected_checks = (*expected_checks, TRUSTED_GOVERNANCE_CHECK)
    for name in expected_checks:
        run = _latest(runs, name, head, GITHUB_ACTIONS_APP_ID)
        if run is None:
            findings.append(f"QA_EVIDENCE_MISSING:{name}")
        elif run.get("conclusion") != "success":
            findings.append(f"QA_EVIDENCE_FAILED:{name}")
    if trust_root_change:
        # Trust roots: .github/, .agents/ and every file a governance workflow executes
        # or reads (including this module). Never autonomous: the sole owner's approval
        # of this exact head is required, and an owner-authored PR can never be
        # self-approved. Receipt minting for this class is the trust-root bootstrap
        # path; this QA decision stays independent of that receipt.
        if pr.get("author") == ev.get("owner_login"):
            findings.append("QA_TRUST_ROOT_OWNER_AUTHORED")
        elif not _owner_approved_at_head(ev, head):
            findings.append("QA_TRUST_ROOT_OWNER_APPROVAL_MISSING")
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
    policy = ev.get("qa_review_policy")
    if not isinstance(policy, dict):
        findings.append("RELEASE_QA_REVIEW_POLICY_MISSING")
    elif policy.get("mode") not in {"legacy", "independent", "canary", "zero_credit"}:
        findings.append("RELEASE_QA_REVIEW_POLICY_INVALID")
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
    no_api = (ev.get("qa_review_policy") or {}).get("mode") == "zero_credit"
    return {
        "check_name": QA_CHECK if kind == "qa" else RELEASE_CHECK,
        "head_sha": head,
        "conclusion": "success" if passed else "failure",
        "findings": findings,
        "title": ("PASS" if passed else f"HOLD: {len(findings)} finding(s)") + f" at {str(head)[:12]}",
        "summary": (("ZERO-CREDIT DETERMINISTIC QA ONLY; no semantic/adversarial source review performed. Exact-head evidence satisfied. No merge/deploy authority. can_execute=false." if no_api else
                     "All exact-head conditions satisfied. Evidence only: not merge, deployment or "
                     "probability authority. can_execute=false.") if passed else
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
    g.add_argument("--legacy-review-policy", action="store_true")
    g.add_argument("--zero-credit-policy-enabled", action="store_true")
    g.add_argument("--source-review-json", type=Path)
    g.add_argument("--source-review-run-json", type=Path)
    g.add_argument("--source-review-artifact-id", type=int)
    g.add_argument("--source-review-run-id", type=int)
    g.add_argument("--source-review-run-attempt", type=int)
    g.add_argument("--source-review-producer-context", action="store_true")
    g.add_argument("--qa-policy-json", type=Path)
    g.add_argument("--current-review-active", action="store_true")
    policy = sub.add_parser("resolve-qa-policy")
    policy.add_argument("--repo", required=True)
    policy.add_argument("--run-id", required=True, type=int)
    policy.add_argument("--run-attempt", required=True, type=int)
    policy.add_argument("--pr", type=int)
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
    if args.cmd == "resolve-qa-policy":
        try:
            resolved = _source_review_module.resolve_qa_review_policy(
                repository=args.repo,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
                expected_pr_number=args.pr,
                token=os.environ.get("GH_TOKEN", ""),
            )
        except _source_review_module.SourceReviewError as exc:
            raise SystemExit(str(exc)) from exc
        print(json.dumps(resolved, sort_keys=True))
        return 0
    if args.cmd == "gather":
        review_args = (
            args.source_review_json,
            args.source_review_run_json,
            args.source_review_artifact_id,
        )
        source_review_data = None
        qa_review_policy = None
        if args.qa_policy_json is not None:
            qa_review_policy = json.loads(args.qa_policy_json.read_text(encoding="utf-8"))
        if any(value is not None for value in review_args):
            if not all(value is not None for value in review_args):
                raise SystemExit("SOURCE_REVIEW_EVIDENCE_ARGUMENTS_INCOMPLETE")
            source_review_data = {
                "artifact_id": args.source_review_artifact_id,
                "artifact": json.loads(args.source_review_json.read_text(encoding="utf-8")),
                "run": json.loads(args.source_review_run_json.read_text(encoding="utf-8")),
            }
        print(json.dumps(gather(
            args.repo,
            args.pr,
            args.expected_head,
            source_review_data=source_review_data,
            qa_review_policy=qa_review_policy,
            current_review_active=args.current_review_active,
            independent_review_active=not args.legacy_review_policy,
            zero_credit_enabled=args.zero_credit_policy_enabled,
            source_review_producer_context=args.source_review_producer_context,
            source_review_run_id=args.source_review_run_id,
            source_review_run_attempt=args.source_review_run_attempt,
        ), indent=2))
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
