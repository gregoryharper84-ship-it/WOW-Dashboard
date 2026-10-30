#!/usr/bin/env python3
"""Route typed QA/Release HOLD findings to repair or to the owner (#1550/#1663 item 3).

Pure decision logic: no network I/O. The caller supplies the typed findings from
`independent_release_checks.decision()` for a wow-engineering[bot] PR, plus how many
repair attempts have already been dispatched for that PR, and this module decides
whether to dispatch another repair, declare the PR waiting on the owner, or do nothing.

Non-repairable trust-root findings are never retried. Owner-authored trusted
source needs an independent source author, not owner self-approval; owner approval
gaps need the real CODEOWNER, not an invented reviewer.
can_execute=false throughout; this module never merges, dispatches a deploy, or grants
bypass. It only classifies.
"""
from __future__ import annotations

import argparse
import json
import re
from typing import Any

try:
    from . import gate_capability_matrix as _gate_matrix
except ImportError:  # Direct protected-main CLI invocation
    import gate_capability_matrix as _gate_matrix

_FINDING_CODE_RE = re.compile(r"`([A-Z][A-Z0-9_]*(?::[^`\r\n]{1,160})?)`")

MAX_REPAIR_ATTEMPTS = 3
NEEDS_OWNER_LABEL = "needs-owner"

# Matches the typed findings produced by independent_release_checks.py / existing-PR
# governance review: QA_TRUST_ROOT_OWNER_AUTHORED, QA_TRUST_ROOT_OWNER_APPROVAL_MISSING,
# RELEASE_TRUST_ROOT_OWNER_APPROVAL_MISSING, EXISTING_PR_R3_CERTIFICATION_DENIED-style
# owner-approval gaps, etc. These are ownership/approval facts, not repairable code defects.
NON_REPAIRABLE_PREFIXES = ("QA_TRUST_ROOT_",)
NON_REPAIRABLE_SUFFIXES = ("_OWNER_APPROVAL_MISSING",)
# A missing upstream check is not a code defect. Wait for that check to finish,
# then independent QA/Release will publish a fresh exact-head verdict.
EVIDENCE_PENDING_PREFIXES = (
    "QA_EVIDENCE_MISSING:", "QA_EVIDENCE_INCOMPLETE:",
    "RELEASE_QA_EVIDENCE_MISSING:", "RELEASE_QA_EVIDENCE_INCOMPLETE:",
)
EVIDENCE_PENDING_CODES = frozenset({"RELEASE_QA_MISSING", "RELEASE_QA_FAILED"})


def _owner_authored_gate_route() -> dict[str, Any]:
    """Consult the read-only matrix for an exact-head QA authorship HOLD.

    Only the existing adoption worker can re-propose source. Its byte-for-byte
    App copy is never proof of independent authorship or semantic review.
    """
    report = _gate_matrix.assess(
        change_class="S", author_is_owner=True, touches_trust_root=True
    )
    matches = [
        entry for entry in report["unreachable"]
        if entry["gate"] == "independent_qa"
        and entry["reason"] == "QA_TRUST_ROOT_OWNER_AUTHORED"
    ]
    if (len(matches) != 1
            or matches[0]["recovery_class"] != "POLICY_UNREACHABLE"
            or not matches[0].get("actor")
            or not matches[0].get("action")
            or report.get("can_execute") is not False
            or report.get("grants_merge") is not False
            or report.get("merge_blocked_scope") != "STRUCTURAL_PINNED_CI_ONLY"
            or report.get("independent_review_status") != "UNVERIFIED"):
        raise ValueError("GATE_CAPABILITY_MATRIX_INCOMPLETE_HOLD")
    return matches[0]


def parse_findings_from_summary(summary: str) -> list[str]:
    """Extract typed finding codes from a published check-run summary.

    `independent_release_checks.decision()` renders failures as
    "Typed findings:\\n- `CODE_ONE`\\n- `CODE_TWO`" — every backtick-quoted
    SHOUTING_CASE token in the summary is a finding code.
    """
    return _FINDING_CODE_RE.findall(summary)


def is_repairable_finding(code: str) -> bool:
    if any(code.startswith(prefix) for prefix in NON_REPAIRABLE_PREFIXES):
        return False
    if any(code.endswith(suffix) for suffix in NON_REPAIRABLE_SUFFIXES):
        return False
    return True


def plan_recovery(
    *,
    pr_number: int,
    findings: list[str],
    attempts: int,
    is_wow_engineering_pr: bool,
) -> dict[str, Any]:
    """Decide the next action for one PR's HOLD outcome.

    `attempts` is how many repairs have already been dispatched for this exact PR
    before this call (0 on the first HOLD). The returned `attempts` is what the caller
    should persist going forward.
    """
    # Trust-root owner-authorship is a cross-repository admission problem,
    # not a reason to silently drop a user-authored PR from recovery triage.
    # This route is advisory only; source adoption requires an independently
    # authenticated agent and positively reconciled canonical writer lease.
    if not is_wow_engineering_pr and "QA_TRUST_ROOT_OWNER_AUTHORED" in findings:
        route = _owner_authored_gate_route()
        return {
            "pr_number": pr_number,
            "action": "WAITING_ON_OWNER",
            "required_actor": route["actor"],
            "next_action": route["action"],
            "gate_recovery_class": route["recovery_class"],
            "independent_review_status": "UNVERIFIED",
            # The existing Engineering App adoption worker owns re-entry.
            # Preserve action for durable queue/backward compatibility only.
            "progression_state": "AUTOMATED_INDEPENDENT_SOURCE_HANDOFF",
            "reentry_trigger": "EXISTING_HOURLY_ADOPTION_AND_QA_HOLD_WORKER",
            "owner_intervention_required": False,
            "unrelated_work_may_progress": True,
            "release_admission": "DENIED_UNTIL_INDEPENDENT_EVIDENCE",
            "findings": list(findings),
            "non_repairable_findings": ["QA_TRUST_ROOT_OWNER_AUTHORED"],
            "attempts": attempts,
            "can_execute": False,
        }
    if not is_wow_engineering_pr:
        return {
            "pr_number": pr_number,
            "action": "IGNORE",
            "reason": "not a wow-engineering[bot] PR; never auto-repaired here",
            "can_execute": False,
        }
    if not findings:
        return {
            "pr_number": pr_number,
            "action": "NONE",
            "reason": "no HOLD findings to repair",
            "attempts": attempts,
            "can_execute": False,
        }

    non_repairable = [f for f in findings if not is_repairable_finding(f)]
    if non_repairable:
        # Retain the existing action for worker compatibility. Diagnose the
        # necessary independent actor without granting write authority or
        # starting a competing repair while the canonical lease is unresolved.
        owner_authored = "QA_TRUST_ROOT_OWNER_AUTHORED" in non_repairable
        route = _owner_authored_gate_route() if owner_authored else None
        return {
            "pr_number": pr_number,
            "action": "WAITING_ON_OWNER",
            "required_actor": (route["actor"] if route else "ACTUAL_CODEOWNER"),
            "next_action": (route["action"] if route else "FRESH_EXACT_HEAD_OWNER_APPROVAL"),
            "gate_recovery_class": route["recovery_class"] if route else None,
            "independent_review_status": "UNVERIFIED" if route else "NOT_EVALUATED",
            "progression_state": ("AUTOMATED_INDEPENDENT_SOURCE_HANDOFF" if owner_authored
                                  else "ACTUAL_CODEOWNER_AUTHORIZATION_REQUIRED"),
            "reentry_trigger": ("EXISTING_HOURLY_ADOPTION_AND_QA_HOLD_WORKER" if owner_authored
                                 else "AUTHENTIC_GITHUB_CODEOWNER_REVIEW_AT_EXACT_SHA"),
            "owner_intervention_required": not owner_authored,
            "unrelated_work_may_progress": True,
            "release_admission": "DENIED_UNTIL_INDEPENDENT_EVIDENCE",
            "findings": list(findings),
            "non_repairable_findings": non_repairable,
            "attempts": attempts,
            "can_execute": False,
        }

    # Do not burn the recovery budget because QA evaluated before CI finished,
    # or because Release observed a not-yet-passing QA check.
    pending = [f for f in findings if f in EVIDENCE_PENDING_CODES or
               any(f.startswith(prefix) for prefix in EVIDENCE_PENDING_PREFIXES)]
    if pending:
        return {
            "pr_number": pr_number,
            "action": "WAITING_ON_EVIDENCE",
            "findings": list(findings),
            "pending_findings": pending,
            "attempts": attempts,
            "can_execute": False,
        }

    if attempts >= MAX_REPAIR_ATTEMPTS:
        return {
            "pr_number": pr_number,
            "action": "NEEDS_OWNER_RETRY_CAP",
            "label": NEEDS_OWNER_LABEL,
            "findings": list(findings),
            "attempts": attempts,
            "can_execute": False,
        }

    return {
        "pr_number": pr_number,
        "action": "DISPATCH_REPAIR",
        "findings": list(findings),
        "attempts": attempts + 1,
        "can_execute": False,
    }


def _cmd_plan(args: argparse.Namespace) -> int:
    if args.summary is not None:
        findings = parse_findings_from_summary(args.summary)
    else:
        findings = json.loads(args.findings_json)
    result = plan_recovery(
        pr_number=args.pr_number,
        findings=findings,
        attempts=args.attempts,
        is_wow_engineering_pr=args.is_wow_engineering_pr,
    )
    print(json.dumps(result))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr-number", required=True, type=int)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--findings-json", help="JSON array of typed finding codes")
    group.add_argument("--summary", help="Published check-run summary text to parse findings from")
    parser.add_argument("--attempts", required=True, type=int)
    parser.add_argument("--is-wow-engineering-pr", action="store_true")
    parser.set_defaults(func=_cmd_plan)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
