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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["build", "validate"])
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--issues")
    parser.add_argument("--output")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text())
    _validate_manifest(manifest)
    if args.command == "validate":
        print(json.dumps({"valid": True, "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER"}, sort_keys=True))
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
