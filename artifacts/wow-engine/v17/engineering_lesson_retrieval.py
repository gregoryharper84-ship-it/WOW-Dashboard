"""Read-only verified incident lesson retrieval for WOW V17 Engineering/AMRT.

This module neither alters the incident ledger nor grants repair, review, model,
release, or betting authority. Historical incident prose is untrusted advice.
Only genuine, fully verified team-governed incident closures are eligible.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

DEFAULT_LEDGER = Path(__file__).with_name("incident-ledger.json")
MAX_RESULTS = 20


def _required_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value else None


def _is_verified_closure(row: Mapping[str, Any]) -> bool:
    """Prove the original team closure, rather than trusting a state label alone."""
    review = row.get("review")
    qa = row.get("qa")
    release = row.get("release")
    reporter = row.get("reporter_closure")
    return (
        row.get("state") == "VERIFIED_CLOSED"
        and row.get("root_cause_status") == "CONFIRMED"
        and isinstance(review, dict)
        and review.get("status") == "PASS"
        and isinstance(qa, dict)
        and qa.get("status") == "PASS"
        and isinstance(release, dict)
        and release.get("status") == "PRODUCTION_VERIFIED"
        and release.get("production_verified") is True
        and _required_text(release.get("deployed_commit")) is not None
        and isinstance(reporter, dict)
        and reporter.get("status") == "FIXED_VERIFIED"
    )


def retrieve_verified_lessons(
    ledger: Mapping[str, Any],
    *,
    failure_category: str | None = None,
    subsystem: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Search only verified closure lessons by exact category and/or subsystem.

    Match keys are deterministic and intentionally narrow; retrieval never
    infers authority or silently rewrites a failure classification. An empty
    result is safe and never blocks normal Engineering triage.
    """
    if ledger.get("runtime_generation") != "V17_ACTIVE":
        raise ValueError("unsupported incident ledger runtime")
    if ledger.get("terminal_authority") != "V17_TERMINAL_REDUCER":
        raise ValueError("incident ledger terminal authority mismatch")
    if ledger.get("can_execute") is not False:
        raise ValueError("incident ledger execution invariant mismatch")
    records = ledger.get("records")
    if not isinstance(records, list):
        raise ValueError("incident ledger records must be a list")
    category = _required_text(failure_category)
    owning_subsystem = _required_text(subsystem)
    if not category and not owning_subsystem:
        raise ValueError("provide failure_category and/or subsystem")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
        raise ValueError("limit must be 1..20")

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in records:
        if not isinstance(row, dict) or not _is_verified_closure(row):
            continue
        incident_id = _required_text(row.get("postmortem_id"))
        learning = row.get("learning")
        if incident_id is None or incident_id in seen or not isinstance(learning, dict):
            continue
        failure = _required_text(learning.get("failure_category"))
        owner = _required_text(row.get("primary_subsystem"))
        architecture = _required_text(learning.get("architecture_lesson"))
        prevention = _required_text(learning.get("preventive_control"))
        regression = _required_text(learning.get("regression_test_added"))
        if not all((failure, owner, architecture, prevention, regression)):
            continue
        if category and category.casefold() != failure.casefold():
            continue
        if owning_subsystem and owning_subsystem.casefold() != owner.casefold():
            continue
        seen.add(incident_id)
        results.append({
            "incident_id": incident_id,
            "failure_category": failure,
            "primary_subsystem": owner,
            "architecture_lesson": architecture,
            "preventive_control": prevention,
            "regression_test_added": regression,
            "updated_utc": _required_text(row.get("updated_utc")),
            "production_verified_commit": row["release"]["deployed_commit"],
            "source_postmortem_path": _required_text(row.get("postmortem_path")),
            "advisory_only": True,
            "automatic_adoption_allowed": False,
        })
    results.sort(key=lambda x: (x["updated_utc"] or "", x["incident_id"]), reverse=True)
    return results[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only WOW V17 verified engineering lessons")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--failure-category")
    parser.add_argument("--subsystem")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
        if not isinstance(ledger, dict):
            raise ValueError("incident ledger must be an object")
        lessons = retrieve_verified_lessons(
            ledger, failure_category=args.failure_category,
            subsystem=args.subsystem, limit=args.limit
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"lesson lookup unavailable: {type(exc).__name__}")
    print(json.dumps({"lessons": lessons, "count": len(lessons), "advisory_only": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
