"""Build the live WOW engineering dispatch queue from GitHub issue truth plus routing metadata.

The manifest is configuration only: it defines approved active issue IDs, engineering
priority, work-stream membership, and explicit conflict ownership. GitHub remains
authoritative for whether an issue is open. No sporting probability behavior lives here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("can_execute") is not False:
        raise ValueError("dispatch manifest must preserve can_execute=false")
    if manifest.get("terminal_authority") != "V17_TERMINAL_REDUCER":
        raise ValueError("dispatch manifest must preserve V17_TERMINAL_REDUCER")

    seen: set[int] = set()
    for stream_name in ("restoration", "acceleration"):
        entries = manifest.get(stream_name)
        if not isinstance(entries, list):
            raise ValueError(f"{stream_name} must be a list")
        for entry in entries:
            number = int(entry["issue_number"])
            if number in seen:
                raise ValueError(f"duplicate issue_number in dispatch manifest: {number}")
            seen.add(number)
            if str(entry.get("severity") or "").upper() not in {"P0", "P1", "P2", "P3", "P4"}:
                raise ValueError(f"invalid severity for issue {number}")
            if int(entry.get("priority_rank") or 0) < 1:
                raise ValueError(f"priority_rank must be >= 1 for issue {number}")
            lane = str(entry.get("execution_lane") or "STANDARD").upper()
            if lane not in {"RAPID", "STANDARD"}:
                raise ValueError(f"invalid execution_lane for issue {number}: {lane}")
            if str(entry.get("severity") or "").upper() == "P0" and lane != "RAPID":
                raise ValueError(f"P0 issue {number} must use RAPID execution_lane")
            if str(entry.get("severity") or "").upper() == "P0":
                rapid_stream = str(entry.get("rapid_stream") or "").upper()
                lease_group = str(entry.get("lease_group") or "").upper()
                if not rapid_stream or not lease_group:
                    raise ValueError(f"P0 issue {number} requires rapid_stream and lease_group")
            keys = entry.get("conflict_keys")
            if not isinstance(keys, list) or not [k for k in keys if str(k).strip()]:
                raise ValueError(f"explicit conflict_keys required for issue {number}")


def build_queue(manifest: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    _validate_manifest(manifest)
    open_issues = {
        int(issue["number"]): issue
        for issue in issues
        if str(issue.get("state") or "OPEN").upper() == "OPEN"
    }

    records: list[dict[str, Any]] = []
    for stream_name, work_stream in (("restoration", "RESTORATION"), ("acceleration", "ACCELERATION")):
        for entry in manifest[stream_name]:
            number = int(entry["issue_number"])
            issue = open_issues.get(number)
            if issue is None:
                continue
            records.append(
                {
                    "incident_id": str(number),
                    "issue_number": number,
                    "title": str(issue.get("title") or ""),
                    "body": str(issue.get("body") or ""),
                    "severity": str(entry["severity"]).upper(),
                    "priority_rank": int(entry["priority_rank"]),
                    "execution_lane": str(entry.get("execution_lane") or "STANDARD").upper(),
                    "rapid_stream": str(entry.get("rapid_stream") or "").upper(),
                    "lease_group": str(entry.get("lease_group") or "").upper(),
                    "state": "OPEN",
                    "work_stream": work_stream,
                    "conflict_keys": [str(k).strip().upper() for k in entry["conflict_keys"] if str(k).strip()],
                    "updated_utc": str(issue.get("updatedAt") or issue.get("updated_at") or ""),
                    "source": "GITHUB_ISSUE",
                    "can_execute": False,
                    "terminal_authority": "V17_TERMINAL_REDUCER",
                }
            )

    rapid_p0_count = sum(
        1 for record in records
        if record["severity"] == "P0" and record["execution_lane"] == "RAPID"
    )
    return {
        "schema_version": "1.0",
        "source": "GITHUB_ACTIVE_EXECUTION_BOARD",
        "records": records,
        "queue_depth": len(records),
        "rapid_p0_count": rapid_p0_count,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }




def standard_conflicts_with_rapid(queue: dict[str, Any], incident_id: str) -> tuple[str, ...]:
    """Return overlap or fail closed if an active P0/standard record is malformed.

    This protects *selection*; shared GitHub Actions concurrency separately
    fences any overlap between jobs started before and after a queue update.
    """
    if queue.get("can_execute") is not False or queue.get("terminal_authority") != "V17_TERMINAL_REDUCER":
        raise ValueError("STANDARD_QUEUE_GOVERNANCE_INVALID")
    records = queue.get("records")
    if not isinstance(records, list):
        raise ValueError("STANDARD_QUEUE_RECORDS_INVALID")
    selected = [r for r in records if isinstance(r, dict) and str(r.get("incident_id")) == str(incident_id)]
    if len(selected) != 1:
        raise ValueError("STANDARD_INCIDENT_NOT_UNIQUE_OR_MISSING")
    row = selected[0]
    if row.get("state") != "OPEN" or row.get("execution_lane") != "STANDARD":
        raise ValueError("STANDARD_INCIDENT_NOT_OPEN_STANDARD")
    keys = row.get("conflict_keys")
    if not isinstance(keys, list) or not keys or any(not isinstance(k, str) or not k.strip() for k in keys):
        raise ValueError("STANDARD_CONFLICT_KEYS_MISSING")
    standard_keys = {k.strip().upper() for k in keys}
    rapid_keys: set[str] = set()
    for p0 in records:
        if not isinstance(p0, dict):
            raise ValueError("STANDARD_QUEUE_ROW_MALFORMED")
        if p0.get("state") != "OPEN" or p0.get("severity") != "P0":
            continue
        if p0.get("execution_lane") != "RAPID":
            raise ValueError("STANDARD_P0_LANE_INVALID")
        p0_keys = p0.get("conflict_keys")
        if not isinstance(p0_keys, list) or not p0_keys or any(not isinstance(k, str) or not k.strip() for k in p0_keys):
            raise ValueError("STANDARD_P0_CONFLICT_KEYS_MISSING")
        rapid_keys.update(k.strip().upper() for k in p0_keys)
    return tuple(sorted(standard_keys & rapid_keys))


def standard_queue_excluding_rapid_conflicts(queue: dict[str, Any]) -> dict[str, Any]:
    """Preserve live inventory but do not dispatch STANDARD over open P0 keys."""
    records = queue.get("records")
    if not isinstance(records, list):
        raise ValueError("STANDARD_QUEUE_RECORDS_INVALID")
    allowed = []
    deferred = []
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("STANDARD_QUEUE_ROW_MALFORMED")
        if item.get("state") != "OPEN":
            raise ValueError("STANDARD_QUEUE_NONOPEN_RECORD")
        if item.get("severity") == "P0":
            if item.get("execution_lane") != "RAPID":
                raise ValueError("STANDARD_P0_LANE_INVALID")
            continue
        if item.get("execution_lane") != "STANDARD":
            raise ValueError("STANDARD_NON_P0_LANE_INVALID")
        overlap = standard_conflicts_with_rapid(queue, str(item.get("incident_id")))
        if overlap:
            deferred.append({"incident_id": item["incident_id"], "reason": "OPEN_P0_CONFLICT_KEYS", "keys": list(overlap)})
        else:
            allowed.append(item)
    return {**queue, "records": allowed, "queue_depth": len(allowed), "rapid_p0_count": 0,
            "deferred_standard": deferred, "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["build", "validate", "standard", "check-standard"])
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--issues")
    parser.add_argument("--queue")
    parser.add_argument("--incident")
    parser.add_argument("--output")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text())
    _validate_manifest(manifest)
    if args.command == "validate":
        print(json.dumps({"valid": True, "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER"}, sort_keys=True))
        return

    if args.command in {"standard", "check-standard"}:
        if not args.queue:
            raise SystemExit("--queue required for standard safety")
        queue = json.loads(Path(args.queue).read_text())
        if args.command == "check-standard":
            if not args.incident:
                raise SystemExit("--incident required for exact STANDARD target")
            overlap = standard_conflicts_with_rapid(queue, args.incident)
            if overlap:
                raise SystemExit("OPEN_P0_CONFLICT_KEYS:" + ",".join(overlap))
            print(json.dumps({"safe": True, "can_execute": False}))
            return
        result = standard_queue_excluding_rapid_conflicts(queue)
        payload = json.dumps(result, indent=2, sort_keys=True)
        if args.output:
            Path(args.output).write_text(payload + "\n")
        print(payload)
        return

    if not args.issues:
        raise SystemExit("--issues is required for build")
    issues = json.loads(Path(args.issues).read_text())
    queue = build_queue(manifest, issues)
    payload = json.dumps(queue, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n")
    print(payload)


if __name__ == "__main__":
    main()
