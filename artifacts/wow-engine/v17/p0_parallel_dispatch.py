"""Select up to three non-conflicting RAPID P0 implementation leases.

This selector is engineering orchestration only. It does not score sports,
publish probabilities, or grant wager execution authority.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

STREAM_ORDER = ("A", "B", "C")
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


def select_parallel(records: list[dict[str, Any]]) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    claimed_keys: set[str] = set()
    claimed_incidents: set[str] = set()
    claimed_leases: set[str] = set()
    skipped: list[dict[str, Any]] = []

    for stream in STREAM_ORDER:
        for row in _eligible(records, stream):
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
            if incident in claimed_incidents:
                skipped.append({"incident_id": incident, "rapid_stream": stream, "reason": "DUPLICATE_INCIDENT"})
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
        "max_parallel_writers": 3,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    queue = json.loads(Path(args.queue).read_text())
    if queue.get("can_execute") is not False:
        raise SystemExit("queue must preserve can_execute=false")
    if queue.get("terminal_authority") != TERMINAL_AUTHORITY:
        raise SystemExit("queue must preserve V17_TERMINAL_REDUCER")
    result = select_parallel(list(queue.get("records") or []))
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
