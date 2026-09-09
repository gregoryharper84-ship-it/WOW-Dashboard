"""WOW V17 postmortem / engineering-fix record manager.

This module is intentionally local-file only. It does not call betting, market,
auth, database, or deployment APIs. It creates deterministic incident records
for the WOW V17 multi-agent engineering recovery team.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .nightly_engineering_team_contract import (
        CLOSURE_RELEASE_STATUSES,
        REPRODUCTION_STATUSES,
        SUBSYSTEMS,
        initial_team_fields,
        route_subsystem,
        transition_record,
        validate_team_record,
    )
except ImportError:  # direct-script execution: python v17/nightly_incident_records.py
    from nightly_engineering_team_contract import (
        CLOSURE_RELEASE_STATUSES,
        REPRODUCTION_STATUSES,
        SUBSYSTEMS,
        initial_team_fields,
        route_subsystem,
        transition_record,
        validate_team_record,
    )

ROOT = Path(__file__).resolve().parent
LEDGER = ROOT / "incident-ledger.json"
POSTMORTEMS = ROOT / "postmortems"
FIXES = ROOT / "engineering-fixes"
PM_RE = re.compile(r"^PM-(\d{4}-\d{2}-\d{2})-(\d{3})$")
FIX_RE = re.compile(r"^FIX-(\d{4}-\d{2}-\d{2})-(\d{3})$")
ALLOWED_STATES = {
    "OPEN",
    "DIAGNOSED",
    "FIX_IN_PROGRESS",
    "HUMAN_REVIEW_REQUIRED",
    "DEPLOYED_PENDING_VERIFY",
    "VERIFIED_CLOSED",
    "CLOSED_SUPERSEDED",
    "BLOCKED_HARD_BOUNDARY",
    "ROLLBACK_REQUIRED",
}
RISK_CLASSES = {"R0", "R1", "R2-restorative", "R2-repair-policy", "R3"}


@dataclass(frozen=True)
class RecordPaths:
    postmortem: Path
    engineering_fix: Path | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT.parent.parent.parent))


def _load_ledger() -> dict[str, Any]:
    data = json.loads(LEDGER.read_text())
    if data.get("runtime_generation") != "V17_ACTIVE":
        raise ValueError("incident ledger must remain V17_ACTIVE")
    if data.get("terminal_authority") != "V17_TERMINAL_REDUCER":
        raise ValueError("incident ledger terminal authority drift")
    if data.get("can_execute") is not False:
        raise ValueError("incident ledger can_execute must be false")
    if not isinstance(data.get("records"), list):
        raise ValueError("incident ledger records must be a list")
    return data


def _save_ledger(data: dict[str, Any]) -> None:
    data["updated_utc"] = _utc_now()
    LEDGER.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")


def _find_record(ledger: dict[str, Any], postmortem_id: str) -> dict[str, Any]:
    row = next((r for r in ledger["records"] if r.get("postmortem_id") == postmortem_id), None)
    if row is None:
        raise ValueError(f"unknown postmortem_id: {postmortem_id}")
    return row


def _next_id(prefix: str, date: str, records: list[dict[str, Any]]) -> str:
    key = "postmortem_id" if prefix == "PM" else "engineering_fix_id"
    pattern = PM_RE if prefix == "PM" else FIX_RE
    numbers: list[int] = []
    for row in records:
        value = row.get(key)
        if not isinstance(value, str):
            continue
        match = pattern.match(value)
        if match and match.group(1) == date:
            numbers.append(int(match.group(2)))
        if prefix == "FIX":
            for fix_id in row.get("engineering_fix_ids", []):
                match = FIX_RE.match(str(fix_id))
                if match and match.group(1) == date:
                    numbers.append(int(match.group(2)))
    return f"{prefix}-{date}-{(max(numbers, default=0) + 1):03d}"


def _slug(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return clean[:72] or "incident"


def _team_enabled(row: dict[str, Any]) -> bool:
    return row.get("team_contract_version") is not None and row.get("legacy_imported") is not True


def create_postmortem(
    *,
    title: str,
    severity: str,
    domain: str,
    evidence: str,
    state: str = "OPEN",
    reported_by: str = "operator",
    subsystem: str | None = None,
) -> RecordPaths:
    ledger = _load_ledger()
    if state not in ALLOWED_STATES:
        raise ValueError(f"invalid state: {state}")
    date = _today()
    pm_id = _next_id("PM", date, ledger["records"])
    path = POSTMORTEMS / f"{pm_id}__{_slug(title)}.md"
    created = _utc_now()
    team = initial_team_fields(domain=domain, reported_by=reported_by, explicit_subsystem=subsystem)
    body = f"""# {pm_id} — {title}\n\n- status: {state}\n- severity: {severity}\n- domain: {domain}\n- primary_subsystem: {team['primary_subsystem']}\n- workflow_stage: REPORTER_INTAKE\n- current_owner: REPORTER_AGENT\n- reported_by: {reported_by}\n- created_utc: {created}\n- runtime_generation: V17_ACTIVE\n- terminal_authority: V17_TERMINAL_REDUCER\n- can_execute: false\n\n## Expected Behavior\n\nPending Reporter intake.\n\n## Actual Behavior\n\nPending Reporter intake.\n\n## Impact\n\nPending structured assessment.\n\n## Evidence\n\n{evidence}\n\n## Reproduction\n\nPending Research/Triage deterministic reproduction or explicit evidence-only classification.\n\n## Root Cause\n\nPending Research/Triage.\n\n## Acceptance Criteria\n\nPending Research/Triage. Engineering may not start a speculative repair without acceptance criteria.\n\n## Governance Classification\n\nPreserve exact V17 typed failure semantics. Research/Engineering/QA do not substitute for the controlling sporting specialist or V17_TERMINAL_REDUCER.\n\n## Linked Engineering Fixes\n\nNone yet.\n\n## Closure Criteria\n\nA new team-governed incident cannot be VERIFIED_CLOSED until root cause, acceptance criteria, independent review, required System Architect review, QA, release/production verification when applicable, and Reporter FIXED_VERIFIED closure all pass.\n"""
    path.write_text(body)
    row = {
        "postmortem_id": pm_id,
        "title": title,
        "severity": severity,
        "domain": domain,
        "state": state,
        "postmortem_path": _relative(path),
        "engineering_fix_ids": [],
        "created_utc": created,
        "updated_utc": created,
        **team,
    }
    ledger["records"].append(row)
    ledger["schema_version"] = "2.0"
    _save_ledger(ledger)
    return RecordPaths(postmortem=path)


def triage_postmortem(
    *,
    postmortem_id: str,
    reproduction_status: str,
    root_cause: str,
    root_cause_confidence: str,
    acceptance_criteria: list[str],
    risk_class: str,
    handoff_evidence: str,
    subsystem: str | None = None,
    protected_contracts: list[str] | None = None,
) -> None:
    ledger = _load_ledger()
    row = _find_record(ledger, postmortem_id)
    if not _team_enabled(row):
        raise ValueError("triage command applies only to team-governed incident records")
    if reproduction_status not in REPRODUCTION_STATUSES - {"PENDING"}:
        raise ValueError(f"invalid reproduction_status: {reproduction_status}")
    if not root_cause.strip():
        raise ValueError("root cause is required")
    if not acceptance_criteria or not all(item.strip() for item in acceptance_criteria):
        raise ValueError("at least one non-empty acceptance criterion is required")
    if risk_class not in RISK_CLASSES:
        raise ValueError(f"invalid risk class: {risk_class}")

    if row.get("workflow_stage") == "REPORTER_INTAKE":
        updated = transition_record(row, to_stage="RESEARCH_TRIAGE", evidence="Reporter intake packet accepted by Research/Triage")
        row.clear()
        row.update(updated)
    if row.get("workflow_stage") != "RESEARCH_TRIAGE":
        raise ValueError(f"triage requires RESEARCH_TRIAGE stage, got {row.get('workflow_stage')}")

    row["primary_subsystem"] = route_subsystem(row.get("domain", ""), explicit=subsystem) if subsystem else row.get("primary_subsystem")
    row["reproduction_status"] = reproduction_status
    row["root_cause_status"] = "CONFIRMED"
    row["root_cause"] = root_cause.strip()
    row["root_cause_confidence"] = root_cause_confidence.strip()
    row["acceptance_criteria"] = [item.strip() for item in acceptance_criteria]
    row["risk_class"] = risk_class
    row["protected_contracts_touched"] = protected_contracts or []
    row["state"] = "DIAGNOSED"
    updated = transition_record(row, to_stage="ENGINEERING", evidence=handoff_evidence)
    row.clear()
    row.update(updated)
    row["updated_utc"] = _utc_now()
    _save_ledger(ledger)


def create_fix(*, postmortem_id: str, title: str, risk: str, root_cause: str, allowed_files: str) -> RecordPaths:
    ledger = _load_ledger()
    row = _find_record(ledger, postmortem_id)
    if not PM_RE.match(postmortem_id):
        raise ValueError("malformed postmortem id")
    if _team_enabled(row):
        if row.get("workflow_stage") != "ENGINEERING":
            raise ValueError("team-governed fix creation requires ENGINEERING workflow stage")
        if row.get("root_cause_status") != "CONFIRMED" or not row.get("acceptance_criteria"):
            raise ValueError("team-governed fix requires confirmed root cause and acceptance criteria")
    date = _today()
    fix_id = _next_id("FIX", date, ledger["records"])
    path = FIXES / f"{fix_id}__{_slug(title)}.md"
    created = _utc_now()
    body = f"""# {fix_id} — {title}\n\n- status: FIX_IN_PROGRESS\n- linked_postmortem: {postmortem_id}\n- implementation_owner: ENGINEERING_AGENT\n- risk: {risk}\n- created_utc: {created}\n- runtime_generation: V17_ACTIVE\n- terminal_authority: V17_TERMINAL_REDUCER\n- can_execute: false\n\n## Root Cause Being Repaired\n\n{root_cause}\n\n## Acceptance Criteria\n\n{chr(10).join('- ' + item for item in row.get('acceptance_criteria', [])) or 'Legacy incident: see linked postmortem.'}\n\n## Allowed Files\n\n{allowed_files}\n\n## Implementation\n\nPending. Use the smallest complete change. Do not refactor unrelated code.\n\n## Regression Test\n\nPending. Prefer a deterministic test that fails under the pre-fix behavior and passes after repair.\n\n## Independent Review\n\nPending INDEPENDENT_REVIEW_AGENT. Engineering cannot self-approve.\n\n## QA\n\nPending QA_VERIFICATION_AGENT after Review PASS.\n\n## Validation Gates\n\n- targeted reproduction\n- relevant unit/integration tests\n- neighboring failure-path/contract tests\n- full applicable WOW regressions\n- OpenAPI/schema validation when applicable\n- can_execute=false and dry-run invariants\n- V17_TERMINAL_REDUCER authority\n- diff-boundary verification\n- required GitHub checks\n\n## Deployment\n\nPending RELEASE_OBSERVABILITY_AGENT after QA PASS.\n\n## Production Verification\n\nPending. Do not mark VERIFIED_CLOSED until fresh applicable production evidence confirms the original defect is absent and no new P0/P1 issue appears.\n\n## Rollback\n\nPending reversible rollback reference.\n"""
    path.write_text(body)
    row.setdefault("engineering_fix_ids", []).append(fix_id)
    row["state"] = "FIX_IN_PROGRESS"
    row["updated_utc"] = created
    row.setdefault("engineering_fixes", {})[fix_id] = {
        "title": title,
        "risk": risk,
        "state": "FIX_IN_PROGRESS",
        "path": _relative(path),
        "created_utc": created,
        "updated_utc": created,
        "implementation_owner": "ENGINEERING_AGENT",
        "review_status": "PENDING",
        "qa_status": "PENDING",
        "release_status": "PENDING",
    }
    _save_ledger(ledger)
    return RecordPaths(postmortem=ROOT.parent.parent.parent / row["postmortem_path"], engineering_fix=path)


def handoff(*, postmortem_id: str, to_stage: str, evidence: str) -> None:
    ledger = _load_ledger()
    row = _find_record(ledger, postmortem_id)
    if not _team_enabled(row):
        raise ValueError("handoff command applies only to team-governed incident records")
    updated = transition_record(row, to_stage=to_stage, evidence=evidence)
    row.clear()
    row.update(updated)
    row["updated_utc"] = _utc_now()
    _save_ledger(ledger)


def mark_review(*, postmortem_id: str, status: str, architect_status: str, evidence: str) -> None:
    ledger = _load_ledger()
    row = _find_record(ledger, postmortem_id)
    if row.get("workflow_stage") != "INDEPENDENT_REVIEW":
        raise ValueError("review can be recorded only at INDEPENDENT_REVIEW stage")
    if status not in {"PASS", "REJECT"}:
        raise ValueError("review status must be PASS or REJECT")
    if architect_status not in {"PASS", "NOT_APPLICABLE", "PENDING", "REJECT"}:
        raise ValueError("invalid architect status")
    row["review"] = {"status": status, "system_architect_status": architect_status, "evidence": evidence}
    latest_fix = row.get("engineering_fix_ids", [])[-1] if row.get("engineering_fix_ids") else None
    if latest_fix:
        row["engineering_fixes"][latest_fix]["review_status"] = status
    target = "QA_VERIFICATION" if status == "PASS" else "ENGINEERING"
    updated = transition_record(row, to_stage=target, evidence=evidence)
    row.clear()
    row.update(updated)
    row["updated_utc"] = _utc_now()
    _save_ledger(ledger)


def mark_qa(*, postmortem_id: str, status: str, evidence: str) -> None:
    ledger = _load_ledger()
    row = _find_record(ledger, postmortem_id)
    if row.get("workflow_stage") != "QA_VERIFICATION":
        raise ValueError("QA can be recorded only at QA_VERIFICATION stage")
    if status not in {"PASS", "FAIL"}:
        raise ValueError("QA status must be PASS or FAIL")
    row["qa"] = {"status": status, "evidence": evidence}
    latest_fix = row.get("engineering_fix_ids", [])[-1] if row.get("engineering_fix_ids") else None
    if latest_fix:
        row["engineering_fixes"][latest_fix]["qa_status"] = status
    target = "RELEASE_OBSERVABILITY" if status == "PASS" else "ENGINEERING"
    updated = transition_record(row, to_stage=target, evidence=evidence)
    row.clear()
    row.update(updated)
    row["updated_utc"] = _utc_now()
    _save_ledger(ledger)


def mark_release(
    *,
    postmortem_id: str,
    status: str,
    evidence: str,
    merge_commit: str | None = None,
    deployed_commit: str | None = None,
    deployment_id: str | None = None,
) -> None:
    ledger = _load_ledger()
    row = _find_record(ledger, postmortem_id)
    if row.get("workflow_stage") != "RELEASE_OBSERVABILITY":
        raise ValueError("release can be recorded only at RELEASE_OBSERVABILITY stage")
    if status not in CLOSURE_RELEASE_STATUSES | {"FAILED"}:
        raise ValueError("invalid release status")
    row["release"] = {
        "status": status,
        "production_verified": status == "PRODUCTION_VERIFIED",
        "evidence": evidence,
        "merge_commit": merge_commit,
        "deployed_commit": deployed_commit,
        "deployment_id": deployment_id,
        "verified_utc": _utc_now() if status == "PRODUCTION_VERIFIED" else None,
    }
    latest_fix = row.get("engineering_fix_ids", [])[-1] if row.get("engineering_fix_ids") else None
    if latest_fix:
        fix = row["engineering_fixes"][latest_fix]
        fix["release_status"] = status
        if merge_commit:
            fix["merge_commit"] = merge_commit
        if deployment_id:
            fix["production_deploy"] = deployment_id
    if status in CLOSURE_RELEASE_STATUSES:
        row["state"] = "DEPLOYED_PENDING_VERIFY" if status == "PRODUCTION_VERIFIED" else row.get("state")
        target = "REPORTER_CLOSURE"
    else:
        target = "ENGINEERING"
    updated = transition_record(row, to_stage=target, evidence=evidence)
    row.clear()
    row.update(updated)
    row["updated_utc"] = _utc_now()
    _save_ledger(ledger)


def close_verified(*, postmortem_id: str, evidence: str, preventive_control: str | None = None) -> None:
    ledger = _load_ledger()
    row = _find_record(ledger, postmortem_id)
    if row.get("workflow_stage") != "REPORTER_CLOSURE":
        raise ValueError("verified closure requires REPORTER_CLOSURE stage")
    row["reporter_closure"] = {"status": "FIXED_VERIFIED", "evidence": evidence, "closed_utc": _utc_now()}
    if preventive_control:
        row.setdefault("learning", {})["preventive_control"] = preventive_control
    row["state"] = "VERIFIED_CLOSED"
    latest_fix = row.get("engineering_fix_ids", [])[-1] if row.get("engineering_fix_ids") else None
    if latest_fix:
        row["engineering_fixes"][latest_fix]["state"] = "VERIFIED_CLOSED"
        row["engineering_fixes"][latest_fix]["updated_utc"] = _utc_now()
    errors = validate_team_record(row, enforce_closure=True)
    if errors:
        raise ValueError("closure blocked: " + "; ".join(errors))
    row["updated_utc"] = _utc_now()
    _save_ledger(ledger)


def validate() -> None:
    ledger = _load_ledger()
    seen_pm: set[str] = set()
    seen_fix: set[str] = set()
    team_records = 0
    for row in ledger["records"]:
        pm_id = row.get("postmortem_id")
        if not isinstance(pm_id, str) or not PM_RE.match(pm_id):
            raise ValueError(f"invalid postmortem_id: {pm_id!r}")
        if pm_id in seen_pm:
            raise ValueError(f"duplicate postmortem_id: {pm_id}")
        seen_pm.add(pm_id)
        if row.get("state") not in ALLOWED_STATES:
            raise ValueError(f"invalid incident state for {pm_id}: {row.get('state')}")
        pm_path = ROOT.parent.parent.parent / row["postmortem_path"]
        if not pm_path.exists():
            raise ValueError(f"missing postmortem file for {pm_id}: {pm_path}")
        if _team_enabled(row):
            team_records += 1
            errors = validate_team_record(row, enforce_closure=True)
            if errors:
                raise ValueError(f"team contract invalid for {pm_id}: {'; '.join(errors)}")
        for fix_id in row.get("engineering_fix_ids", []):
            if not isinstance(fix_id, str) or not FIX_RE.match(fix_id):
                raise ValueError(f"invalid engineering fix id: {fix_id!r}")
            if fix_id in seen_fix:
                raise ValueError(f"engineering fix linked more than once: {fix_id}")
            seen_fix.add(fix_id)
            fix_meta = row.get("engineering_fixes", {}).get(fix_id)
            if not fix_meta:
                raise ValueError(f"missing engineering_fixes metadata for {fix_id}")
            fix_path = ROOT.parent.parent.parent / fix_meta["path"]
            if not fix_path.exists():
                raise ValueError(f"missing engineering fix file for {fix_id}: {fix_path}")
    print(
        f"validated incident ledger: {len(seen_pm)} postmortems, "
        f"{len(seen_fix)} fixes, {team_records} team-governed records"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")

    pm = sub.add_parser("create-postmortem")
    pm.add_argument("--title", required=True)
    pm.add_argument("--severity", required=True)
    pm.add_argument("--domain", required=True)
    pm.add_argument("--evidence", required=True)
    pm.add_argument("--state", default="OPEN", choices=sorted(ALLOWED_STATES))
    pm.add_argument("--reported-by", default="operator")
    pm.add_argument("--subsystem", choices=sorted(SUBSYSTEMS))

    triage = sub.add_parser("triage")
    triage.add_argument("--postmortem-id", required=True)
    triage.add_argument("--reproduction-status", required=True, choices=sorted(REPRODUCTION_STATUSES - {"PENDING"}))
    triage.add_argument("--root-cause", required=True)
    triage.add_argument("--root-cause-confidence", required=True)
    triage.add_argument("--acceptance-criterion", action="append", required=True)
    triage.add_argument("--risk-class", required=True, choices=sorted(RISK_CLASSES))
    triage.add_argument("--handoff-evidence", required=True)
    triage.add_argument("--subsystem", choices=sorted(SUBSYSTEMS))
    triage.add_argument("--protected-contract", action="append", default=[])

    fx = sub.add_parser("create-fix")
    fx.add_argument("--postmortem-id", required=True)
    fx.add_argument("--title", required=True)
    fx.add_argument("--risk", required=True, choices=["R0", "R1", "R2", "R3", "R2-restorative", "R2-repair-policy"])
    fx.add_argument("--root-cause", required=True)
    fx.add_argument("--allowed-files", required=True)

    ho = sub.add_parser("handoff")
    ho.add_argument("--postmortem-id", required=True)
    ho.add_argument("--to-stage", required=True)
    ho.add_argument("--evidence", required=True)

    review = sub.add_parser("mark-review")
    review.add_argument("--postmortem-id", required=True)
    review.add_argument("--status", required=True, choices=["PASS", "REJECT"])
    review.add_argument("--architect-status", required=True, choices=["PASS", "NOT_APPLICABLE", "PENDING", "REJECT"])
    review.add_argument("--evidence", required=True)

    qa = sub.add_parser("mark-qa")
    qa.add_argument("--postmortem-id", required=True)
    qa.add_argument("--status", required=True, choices=["PASS", "FAIL"])
    qa.add_argument("--evidence", required=True)

    rel = sub.add_parser("mark-release")
    rel.add_argument("--postmortem-id", required=True)
    rel.add_argument("--status", required=True, choices=sorted(CLOSURE_RELEASE_STATUSES | {"FAILED"}))
    rel.add_argument("--evidence", required=True)
    rel.add_argument("--merge-commit")
    rel.add_argument("--deployed-commit")
    rel.add_argument("--deployment-id")

    close = sub.add_parser("close-verified")
    close.add_argument("--postmortem-id", required=True)
    close.add_argument("--evidence", required=True)
    close.add_argument("--preventive-control")

    args = parser.parse_args()
    if args.command == "validate":
        validate()
    elif args.command == "create-postmortem":
        paths = create_postmortem(
            title=args.title,
            severity=args.severity,
            domain=args.domain,
            evidence=args.evidence,
            state=args.state,
            reported_by=args.reported_by,
            subsystem=args.subsystem,
        )
        print(paths.postmortem)
    elif args.command == "triage":
        triage_postmortem(
            postmortem_id=args.postmortem_id,
            reproduction_status=args.reproduction_status,
            root_cause=args.root_cause,
            root_cause_confidence=args.root_cause_confidence,
            acceptance_criteria=args.acceptance_criterion,
            risk_class=args.risk_class,
            handoff_evidence=args.handoff_evidence,
            subsystem=args.subsystem,
            protected_contracts=args.protected_contract,
        )
        print(args.postmortem_id)
    elif args.command == "create-fix":
        paths = create_fix(
            postmortem_id=args.postmortem_id,
            title=args.title,
            risk=args.risk,
            root_cause=args.root_cause,
            allowed_files=args.allowed_files,
        )
        print(paths.engineering_fix)
    elif args.command == "handoff":
        handoff(postmortem_id=args.postmortem_id, to_stage=args.to_stage, evidence=args.evidence)
        print(args.postmortem_id)
    elif args.command == "mark-review":
        mark_review(
            postmortem_id=args.postmortem_id,
            status=args.status,
            architect_status=args.architect_status,
            evidence=args.evidence,
        )
        print(args.postmortem_id)
    elif args.command == "mark-qa":
        mark_qa(postmortem_id=args.postmortem_id, status=args.status, evidence=args.evidence)
        print(args.postmortem_id)
    elif args.command == "mark-release":
        mark_release(
            postmortem_id=args.postmortem_id,
            status=args.status,
            evidence=args.evidence,
            merge_commit=args.merge_commit,
            deployed_commit=args.deployed_commit,
            deployment_id=args.deployment_id,
        )
        print(args.postmortem_id)
    else:
        close_verified(
            postmortem_id=args.postmortem_id,
            evidence=args.evidence,
            preventive_control=args.preventive_control,
        )
        print(args.postmortem_id)


if __name__ == "__main__":
    main()
