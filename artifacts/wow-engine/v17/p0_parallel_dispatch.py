"""Select up to three non-conflicting RAPID P0 implementation leases.

This selector is engineering orchestration only. It does not score sports,
publish probabilities, or grant wager execution authority.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

STREAM_ORDER = ("GOVERNANCE", "A", "B", "C")
MAX_PARALLEL_WRITERS = 3
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


def _keys(record: dict[str, Any]) -> set[str]:
    return {str(k).strip().upper() for k in record.get("conflict_keys") or [] if str(k).strip()}


def _eligible(records: list[dict[str, Any]], stream: str) -> list[dict[str, Any]]:
    rows = [
        row
        for row in records
        if str(row.get("severity") or "").upper() == "P0"
        and str(row.get("execution_lane") or "").upper() == "RAPID"
        and str(row.get("rapid_stream") or "").upper() == stream
        and str(row.get("state") or "").upper() == "OPEN"
    ]
    return sorted(
        rows,
        key=lambda row: (
            int(row.get("priority_rank") or 9999),
            str(row.get("updated_utc") or ""),
            str(row.get("incident_id") or ""),
        ),
    )


def _incidents_with_open_prs(open_prs: list[dict[str, Any]]) -> set[str]:
    """Only direct repair ownership prevents dispatch, not arbitrary references.

    Explicit Incident: N or closing keywords identify implementation ownership.
    Mere issue mentions, dependency links and review discussions do not.
    """
    pending: set[str] = set()
    for pr in open_prs:
        if not isinstance(pr, dict) or not isinstance(pr.get("number"), int):
            raise ValueError("OPEN_PR_INVENTORY_INVALID")
        content = str(pr.get("title") or "") + "\n" + str(pr.get("body") or "")
        for line in content.splitlines():
            incident = re.match(r"(?i)^\\s*Incident\\s*:\\s*`?#?([0-9]+)`?(?=\\b|$)", line)
            if incident:
                pending.add(incident.group(1))
            for claim in re.finditer(r"(?i)\\b(?:fixes|closes|resolves)\\s+#([0-9]+)\\b", line):
                pending.add(claim.group(1))
    return pending


_ACTIVE_ENGINEERING_PATHS = frozenset({
    ".github/workflows/wow-v17-chatgpt-engineering-worker.yml",
    ".github/workflows/wow-v17-claude-engineering-worker.yml",
    ".github/workflows/wow-v17-engineering-provider-dispatcher.yml",
})


def _active_ownership(
    records: list[dict[str, Any]], active_runs: list[dict[str, Any]],
) -> tuple[set[str], set[str], bool]:
    """Bind provenance to workflow path and main; hold all lanes for GLOBAL writers.

    AUTO/GLOBAL writers may mutate any incident, and without a trusted target
    receipt their conflict set is unknown. A typed all-lane hold is safer than
    racing another implementation writer or crashing the dispatcher.
    """
    global_writer_unresolved = False
    indexed = {str(row["incident_id"]): row for row in records}
    active_leases: set[str] = set()
    active_keys: set[str] = set()
    for run in active_runs:
        if not isinstance(run, dict):
            raise ValueError("ACTIVE_WORKFLOW_INVENTORY_INVALID")
        path = str(run.get("path") or "").split("@", 1)[0]
        if path not in _ACTIVE_ENGINEERING_PATHS:
            continue
        title = str(run.get("display_title") or run.get("name") or "")
        if path.endswith("wow-v17-engineering-provider-dispatcher.yml"):
            identity = re.fullmatch(
                r"WOW V17 provider source=.+ lease=([A-Za-z0-9_-]+) incident=([0-9]+|AUTO)",
                title,
            )
        else:
            workflow_name = path.rsplit("/", 1)[-1].removesuffix(".yml")
            identity = re.fullmatch(
                re.escape(workflow_name)
                + r" lease=([A-Za-z0-9_-]+) incident=([0-9]+|AUTO)",
                title,
            )
        if not identity or run.get("head_branch") != "main":
            raise ValueError("ACTIVE_WORKER_IDENTITY_UNRESOLVED")
        lease, incident = identity.groups()
        if lease.upper() == "GLOBAL":
            # GLOBAL has no safe domain boundary, including exact P1 workers.
            global_writer_unresolved = True
            continue
        if incident == "AUTO":
            raise ValueError("ACTIVE_WORKER_DOMAIN_IDENTITY_UNRESOLVED")
        row = indexed.get(incident)
        if row is None or str(row.get("lease_group") or "").upper() != lease.upper():
            raise ValueError("ACTIVE_WORKER_MANIFEST_IDENTITY_UNRESOLVED")
        active_leases.add(lease.upper())
        active_keys.update(_keys(row))
    return active_leases, active_keys, global_writer_unresolved


def select_parallel(
    records: list[dict[str, Any]],
    *,
    open_prs: list[dict[str, Any]] | None = None,
    active_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    pending_pr_incidents = _incidents_with_open_prs(open_prs or [])
    active_leases, active_keys, global_writer_unresolved = _active_ownership(records, active_runs or [])

    selected: list[dict[str, Any]] = []
    claimed_keys: set[str] = set()
    claimed_incidents: set[str] = set()
    claimed_leases: set[str] = set()
    skipped: list[dict[str, Any]] = []

    for stream in STREAM_ORDER:
        eligible = _eligible(records, stream)
        if len(selected) >= MAX_PARALLEL_WRITERS:
            skipped.extend(
                {
                    "incident_id": str(row.get("incident_id") or "UNKNOWN"),
                    "rapid_stream": stream,
                    "reason": "MAX_PARALLEL_WRITERS_REACHED",
                }
                for row in eligible
            )
            continue
        for row in eligible:
            if global_writer_unresolved:
                skipped.append({
                    "incident_id": str(row.get("incident_id") or "UNKNOWN"),
                    "rapid_stream": stream,
                    "reason": "ACTIVE_GLOBAL_WORKER_TARGET_UNRESOLVED",
                })
                continue
            incident = str(row.get("incident_id") or "")
            lease_group = str(row.get("lease_group") or "").upper()
            keys = _keys(row)
            if not incident or not lease_group or not keys:
                skipped.append(
                    {
                        "incident_id": incident or "UNKNOWN",
                        "rapid_stream": stream,
                        "reason": "MISSING_EXPLICIT_LEASE_OR_CONFLICT_METADATA",
                    }
                )
                continue
            if incident in pending_pr_incidents:
                skipped.append({
                    "incident_id": incident,
                    "rapid_stream": stream,
                    "reason": "EXISTING_OPEN_PR_REQUIRES_REVIEW",
                })
                continue
            if incident in claimed_incidents:
                skipped.append({"incident_id": incident, "rapid_stream": stream, "reason": "DUPLICATE_INCIDENT"})
                continue
            if lease_group in active_leases:
                skipped.append({
                    "incident_id": incident, "rapid_stream": stream,
                    "reason": "ACTIVE_WORKER_LEASE_CONFLICT",
                })
                continue
            active_overlap = sorted(keys & active_keys)
            if active_overlap:
                skipped.append({
                    "incident_id": incident, "rapid_stream": stream,
                    "reason": "ACTIVE_WORKER_CONFLICT_KEYS_OVERLAP",
                    "overlap": active_overlap,
                })
                continue
            if lease_group in claimed_leases:
                skipped.append({"incident_id": incident, "rapid_stream": stream, "reason": "LEASE_GROUP_ALREADY_CLAIMED"})
                continue
            overlap = sorted(keys & claimed_keys)
            if overlap:
                skipped.append(
                    {
                        "incident_id": incident,
                        "rapid_stream": stream,
                        "reason": "CONFLICT_KEYS_OVERLAP",
                        "overlap": overlap,
                    }
                )
                continue

            selected.append(
                {
                    "incident_id": incident,
                    "priority_rank": int(row.get("priority_rank") or 9999),
                    "rapid_stream": stream,
                    "lease_group": lease_group,
                    "conflict_keys": sorted(keys),
                    "can_execute": False,
                    "terminal_authority": TERMINAL_AUTHORITY,
                }
            )
            claimed_incidents.add(incident)
            claimed_leases.add(lease_group)
            claimed_keys.update(keys)
            break

    return {
        "schema_version": "1.0",
        "selected": selected,
        "selected_count": len(selected),
        "skipped": skipped,
        "max_parallel_writers": MAX_PARALLEL_WRITERS,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", required=True)
    parser.add_argument("--open-prs", required=True)
    parser.add_argument("--active-runs", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    queue = json.loads(Path(args.queue).read_text())
    if queue.get("can_execute") is not False:
        raise SystemExit("queue must preserve can_execute=false")
    if queue.get("terminal_authority") != TERMINAL_AUTHORITY:
        raise SystemExit("queue must preserve V17_TERMINAL_REDUCER")
    open_prs = json.loads(Path(args.open_prs).read_text())
    if not isinstance(open_prs, list) or len(open_prs) >= 200:
        raise SystemExit("OPEN_PR_INVENTORY_INCOMPLETE")
    active_runs = json.loads(Path(args.active_runs).read_text())
    if not isinstance(active_runs, list):
        raise SystemExit("ACTIVE_WORKFLOW_INVENTORY_INCOMPLETE")
    try:
        result = select_parallel(list(queue.get("records") or []), open_prs=open_prs, active_runs=active_runs)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
