"""Read-only lessons from WOW's canonical verified engineering incidents.

V1 intentionally uses exact failure-category matching. No LLM, network,
production writes, model tuning, or autonomous approvals.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[1] / "v17" / "incident-ledger.json"
REPO = "https://github.com/gregoryharper84-ship-it/WOW-Dashboard"
CATEGORY = re.compile(r"[A-Z][A-Z0-9_]{2,79}\Z")
SHA = re.compile(r"[a-f0-9]{40}\Z")


def load_ledger(path: Path) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or (
        document.get("runtime_generation") != "V17_ACTIVE"
        or document.get("terminal_authority") != "V17_TERMINAL_REDUCER"
        or document.get("can_execute") is not False
        or not isinstance(document.get("records"), list)
    ):
        raise ValueError("invalid canonical V17 incident ledger")
    if any(not isinstance(row, dict) for row in document["records"]):
        raise ValueError("invalid incident record")
    return document["records"]


def verified_lessons(records: list[dict]) -> list[dict]:
    """Only fully evidenced, verified closures may teach future repairs."""
    results = []
    seen_ids = set()
    for row in records:
        if row.get("state") != "VERIFIED_CLOSED" or row.get("root_cause_status") != "CONFIRMED":
            continue
        if any(not isinstance(row.get(field), dict) for field in (
            "learning", "review", "qa", "release", "reporter_closure", "engineering_fixes"
        )):
            continue
        learned = row["learning"]
        release = row["release"]
        if (row["review"].get("status") != "PASS"
            or row["qa"].get("status") != "PASS"
            or row["reporter_closure"].get("status") != "FIXED_VERIFIED"
            or release.get("status") != "PRODUCTION_VERIFIED"
            or release.get("production_verified") is not True):
            continue
        category = learned.get("failure_category")
        if not isinstance(category, str) or not CATEGORY.fullmatch(category):
            continue
        required = ("root_cause", "preventive_control", "regression_test_added", "why_existing_tests_missed_it")
        if not all(isinstance((row if name == "root_cause" else learned).get(name), str)
                   and (row if name == "root_cause" else learned)[name].strip()
                   for name in required):
            continue
        if any(not isinstance(release.get(k), str) or not SHA.fullmatch(release[k])
               for k in ("merge_commit", "deployed_commit")):
            continue
        if not isinstance(release.get("deployment_id"), str) or not release["deployment_id"].strip():
            continue
        incident_id = row.get("postmortem_id")
        fixes = row.get("engineering_fix_ids")
        if (not isinstance(incident_id, str)
            or not re.fullmatch(r"PM-\d{4}-\d{2}-\d{2}-\d{3}", incident_id)
            or incident_id in seen_ids or not isinstance(fixes, list) or not fixes):
            continue
        fix_id = fixes[-1]
        fix = row["engineering_fixes"].get(fix_id)
        if (not isinstance(fix, dict)
            or fix.get("state") != "VERIFIED_CLOSED"
            or fix.get("merge_commit") != release["merge_commit"]):
            continue
        pr = fix.get("pull_request")
        if type(pr) is not int or pr <= 0:
            continue
        seen_ids.add(incident_id)
        results.append({
            "incident_id": incident_id,
            "failure_category": category,
            "root_cause": row["root_cause"].strip(),
            "why_test_missed": learned["why_existing_tests_missed_it"].strip(),
            "regression_test": learned["regression_test_added"].strip(),
            "preventive_control": learned["preventive_control"].strip(),
            "reference_pr": f"{REPO}/pull/{pr}",
            "verified_deployed_sha": release["deployed_commit"],
        })
    return sorted(results, key=lambda lesson: lesson["incident_id"])


def match(lessons: list[dict], category: str) -> dict:
    """Exact deterministic match; never infer a fix from incomplete evidence."""
    if not CATEGORY.fullmatch(category):
        raise ValueError("failure category must be uppercase A-Z, 0-9 and underscore")
    matches = [lesson for lesson in lessons if lesson["failure_category"] == category]
    return {
        "status": "VERIFIED_MATCH" if matches else "NO_VERIFIED_MATCH",
        "category": category,
        "matches": matches,
        "advisory_only": True,
        "production_verified_for_current_incident": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("list", "match"))
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--category", help="Exact registered failure category for match")
    args = parser.parse_args()
    try:
        lessons = verified_lessons(load_ledger(args.ledger))
        if args.command == "match":
            if not args.category:
                parser.error("match requires --category")
            output = match(lessons, args.category)
        else:
            output = {"status": "READ_ONLY_LESSONS", "count": len(lessons), "lessons": lessons,
                      "advisory_only": True}
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"LESSON_LOOKUP_ERROR: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
