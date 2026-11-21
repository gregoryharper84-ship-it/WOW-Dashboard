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

STREAM_ORDER = ("LLP_RESTORE", "AUTONOMY_RESTORE", "GOVERNANCE", "A", "B", "C", "SOCCER_RESTORE")
MAX_PARALLEL_WRITERS = 3
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
LEASE_GROUP_RE = re.compile(r"P0_[A-Z0-9_]+")
INCIDENT_ID_RE = re.compile(r"[1-9][0-9]*")
ACTIVE_LEASE_RE = re.compile(r"\blease=(P0_[A-Z0-9_]+)(?:\s|$)", re.IGNORECASE)
ACTIVE_INCIDENT_RE = re.compile(r"\bincident=([1-9][0-9]*)(?:\s|$)")


def _keys(record: dict[str, Any]) -> set[str]:
    values = record.get("conflict_keys")
    if not isinstance(values, list):
        return set()
    return {str(k).strip().upper() for k in values if str(k).strip()}


def _eligible(records: list[dict[str, Any]], stream: str) -> list[dict[str, Any]]:
    rows = [
        row
        for row in records
        if str(row.get("severity") or "").upper() == "P0"
        and str(row.get("execution_lane") or "").upper() == "RAPID"
        and str(row.get("rapid_stream") or "").upper() == stream
        and str(row.get("state") or "").upper() == "OPEN"
        and row.get("can_execute") is False
        and row.get("terminal_authority") == TERMINAL_AUTHORITY
    ]
    return sorted(
        rows,
        key=lambda row: (
            int(row.get("priority_rank") or 9999),
            str(row.get("updated_utc") or ""),
            str(row.get("incident_id") or ""),
        ),
    )


def active_leases_from_runs(
    runs: list[dict[str, Any]], manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    entries = {
        str(entry["issue_number"]): entry
        for stream in ("restoration", "acceleration")
        for entry in manifest.get(stream, [])
        if str(entry.get("severity") or "").upper() == "P0"
    }
    active: dict[str, dict[str, Any]] = {}
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError("ACTIVE_P0_RUN_INVALID")
        if str(run.get("status") or "").lower() == "completed":
            continue

        title = str(run.get("displayTitle") or "")
        if "P0_" not in title.upper():
            continue
        lease_match = ACTIVE_LEASE_RE.search(title)
        incident_match = ACTIVE_INCIDENT_RE.search(title)
        if lease_match is None or incident_match is None:
            raise ValueError("ACTIVE_P0_LEASE_IDENTITY_INVALID")

        lease_group = lease_match.group(1).upper()
        incident = incident_match.group(1)
        entry = entries.get(incident)
        if (
            LEASE_GROUP_RE.fullmatch(lease_group) is None
            or entry is None
            or str(entry.get("lease_group") or "").upper() != lease_group
            or str(entry.get("execution_lane") or "").upper() != "RAPID"
            or str(entry.get("rapid_stream") or "").upper() not in STREAM_ORDER
            or not isinstance(entry.get("conflict_keys"), list)
            or not _keys(entry)
        ):
            raise ValueError("ACTIVE_P0_LEASE_METADATA_INVALID")

        lease = {
            "incident_id": incident,
            "lease_group": lease_group,
            "conflict_keys": sorted(_keys(entry)),
        }
        prior = active.get(lease_group)
        if prior is not None and prior != lease:
            raise ValueError("ACTIVE_P0_LEASE_DUPLICATE")
        active[lease_group] = lease
    return list(active.values())


def select_parallel(
    records: list[dict[str, Any]],
    *,
    run_number: int = 1,
    active_leases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if isinstance(run_number, bool) or not isinstance(run_number, int) or run_number < 1:
        raise ValueError("DISPATCH_RUN_NUMBER_INVALID")

    active_by_lease: dict[str, dict[str, Any]] = {}
    for lease in active_leases or []:
        if not isinstance(lease, dict):
            raise ValueError("ACTIVE_P0_LEASE_INVALID")
        incident = str(lease.get("incident_id") or "")
        lease_group = str(lease.get("lease_group") or "").upper()
        keys = _keys(lease)
        if (
            INCIDENT_ID_RE.fullmatch(incident) is None
            or LEASE_GROUP_RE.fullmatch(lease_group) is None
            or not isinstance(lease.get("conflict_keys"), list)
            or not keys
        ):
            raise ValueError("ACTIVE_P0_LEASE_INVALID")
        item = {
            "incident_id": incident,
            "lease_group": lease_group,
            "conflict_keys": sorted(keys),
        }
        prior = active_by_lease.get(lease_group)
        if prior is not None and prior != item:
            raise ValueError("ACTIVE_P0_LEASE_DUPLICATE")
        active_by_lease[lease_group] = item

    selected: list[dict[str, Any]] = []
    claimed_keys = {
        key
        for lease in active_by_lease.values()
        for key in lease["conflict_keys"]
    }
    claimed_incidents = {lease["incident_id"] for lease in active_by_lease.values()}
    claimed_leases = set(active_by_lease)
    skipped: list[dict[str, Any]] = []
    start = (run_number - 1) % len(STREAM_ORDER)
    stream_order = STREAM_ORDER[start:] + STREAM_ORDER[:start]
    available_slots = max(0, MAX_PARALLEL_WRITERS - len(active_by_lease))

    for stream in stream_order:
        eligible = _eligible(records, stream)
        if len(selected) >= available_slots:
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
            incident = str(row.get("incident_id") or "")
            lease_group = str(row.get("lease_group") or "").upper()
            keys = _keys(row)
            if INCIDENT_ID_RE.fullmatch(incident) is None or not lease_group or not keys:
                skipped.append(
                    {
                        "incident_id": incident or "UNKNOWN",
                        "rapid_stream": stream,
                        "reason": "MISSING_EXPLICIT_LEASE_OR_CONFLICT_METADATA",
                    }
                )
                continue
            if LEASE_GROUP_RE.fullmatch(lease_group) is None:
                skipped.append(
                    {
                        "incident_id": incident,
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
        "max_parallel_writers": MAX_PARALLEL_WRITERS,
        "active_writer_count": len(active_by_lease),
        "opportunity": run_number,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--active-runs", required=True)
    parser.add_argument("--run-number", type=int, required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    queue = json.loads(Path(args.queue).read_text())
    manifest = json.loads(Path(args.manifest).read_text())
    active_runs = json.loads(Path(args.active_runs).read_text())
    if queue.get("can_execute") is not False:
        raise SystemExit("queue must preserve can_execute=false")
    if queue.get("terminal_authority") != TERMINAL_AUTHORITY:
        raise SystemExit("queue must preserve V17_TERMINAL_REDUCER")
    active_leases = active_leases_from_runs(active_runs, manifest)
    result = select_parallel(
        list(queue.get("records") or []),
        run_number=args.run_number,
        active_leases=active_leases,
    )
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
