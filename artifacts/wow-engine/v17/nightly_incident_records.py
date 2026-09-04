"""WOW V17 postmortem / engineering-fix record manager.

This module is intentionally local-file only. It does not call betting, market,
auth, database, or deployment APIs. It creates deterministic incident records
that can be reviewed and committed by the nightly engineering autopilot.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    "ROLLBACK_REQUIRED",
}


@dataclass(frozen=True)
class RecordPaths:
    postmortem: Path
    engineering_fix: Path | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


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
    LEDGER.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")


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
    return f"{prefix}-{date}-{(max(numbers, default=0) + 1):03d}"


def _slug(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return clean[:72] or "incident"


def create_postmortem(*, title: str, severity: str, domain: str, evidence: str, state: str = "OPEN") -> RecordPaths:
    ledger = _load_ledger()
    if state not in ALLOWED_STATES:
        raise ValueError(f"invalid state: {state}")
    date = _today()
    pm_id = _next_id("PM", date, ledger["records"])
    path = POSTMORTEMS / f"{pm_id}__{_slug(title)}.md"
    created = _utc_now()
    body = f"""# {pm_id} — {title}\n\n- status: {state}\n- severity: {severity}\n- domain: {domain}\n- created_utc: {created}\n- runtime_generation: V17_ACTIVE\n- terminal_authority: V17_TERMINAL_REDUCER\n- can_execute: false\n\n## Impact\n\nPending structured assessment.\n\n## Evidence\n\n{evidence}\n\n## Reproduction\n\nPending deterministic reproduction or explicit evidence-only classification.\n\n## Root Cause\n\nPending.\n\n## Governance Classification\n\nDo not rewrite an invoked scorer/model failure as MODEL_UNAVAILABLE. Preserve the exact typed V17 failure semantics.\n\n## Linked Engineering Fixes\n\nNone yet.\n\n## Closure Criteria\n\nA linked fix must pass applicable regression, contract, governance, deployment, and production-verification gates before this postmortem may be closed.\n"""
    path.write_text(body)
    ledger["records"].append(
        {
            "postmortem_id": pm_id,
            "title": title,
            "severity": severity,
            "domain": domain,
            "state": state,
            "postmortem_path": str(path.relative_to(ROOT.parent.parent.parent)),
            "engineering_fix_ids": [],
            "created_utc": created,
            "updated_utc": created,
        }
    )
    _save_ledger(ledger)
    return RecordPaths(postmortem=path)


def create_fix(*, postmortem_id: str, title: str, risk: str, root_cause: str, allowed_files: str) -> RecordPaths:
    ledger = _load_ledger()
    row = next((r for r in ledger["records"] if r.get("postmortem_id") == postmortem_id), None)
    if row is None:
        raise ValueError(f"unknown postmortem_id: {postmortem_id}")
    if not PM_RE.match(postmortem_id):
        raise ValueError("malformed postmortem id")
    date = _today()
    fix_id = _next_id("FIX", date, ledger["records"])
    path = FIXES / f"{fix_id}__{_slug(title)}.md"
    created = _utc_now()
    body = f"""# {fix_id} — {title}\n\n- status: FIX_IN_PROGRESS\n- linked_postmortem: {postmortem_id}\n- risk: {risk}\n- created_utc: {created}\n- runtime_generation: V17_ACTIVE\n- terminal_authority: V17_TERMINAL_REDUCER\n- can_execute: false\n\n## Root Cause Being Repaired\n\n{root_cause}\n\n## Allowed Files\n\n{allowed_files}\n\n## Implementation\n\nPending. Use the smallest complete change. Do not refactor unrelated code.\n\n## Regression Test\n\nPending. Prefer a deterministic test that fails before the repair and passes after it.\n\n## Validation Gates\n\n- targeted reproduction\n- relevant unit/integration tests\n- WOW regressions\n- OpenAPI/schema validation when applicable\n- can_execute=false and dry-run invariants\n- diff-boundary verification\n- required GitHub checks\n\n## Deployment\n\nPending. R2/R3 changes require human review unless separately authorized by an explicit governed patch.\n\n## Production Verification\n\nPending. Do not mark VERIFIED_CLOSED until fresh production evidence confirms the original defect is absent and no new P0/P1 issue appears.\n\n## Rollback\n\nPending reversible rollback reference.\n"""
    path.write_text(body)
    row.setdefault("engineering_fix_ids", []).append(fix_id)
    row["state"] = "FIX_IN_PROGRESS"
    row["updated_utc"] = created
    row.setdefault("engineering_fixes", {})[fix_id] = {
        "title": title,
        "risk": risk,
        "state": "FIX_IN_PROGRESS",
        "path": str(path.relative_to(ROOT.parent.parent.parent)),
        "created_utc": created,
        "updated_utc": created,
    }
    _save_ledger(ledger)
    return RecordPaths(postmortem=ROOT.parent.parent.parent / row["postmortem_path"], engineering_fix=path)


def validate() -> None:
    ledger = _load_ledger()
    seen_pm: set[str] = set()
    seen_fix: set[str] = set()
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
    print(f"validated incident ledger: {len(seen_pm)} postmortems, {len(seen_fix)} fixes")


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

    fx = sub.add_parser("create-fix")
    fx.add_argument("--postmortem-id", required=True)
    fx.add_argument("--title", required=True)
    fx.add_argument("--risk", required=True, choices=["R0", "R1", "R2", "R3"])
    fx.add_argument("--root-cause", required=True)
    fx.add_argument("--allowed-files", required=True)

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
        )
        print(paths.postmortem)
    else:
        paths = create_fix(
            postmortem_id=args.postmortem_id,
            title=args.title,
            risk=args.risk,
            root_cause=args.root_cause,
            allowed_files=args.allowed_files,
        )
        print(paths.engineering_fix)


if __name__ == "__main__":
    main()
