"""Prepare a Scout discovery handoff for durable persistence transport.

The discovery artifact is intentionally richer than the persistence wire format.
Historical and stale market-evidence arrays are diagnostic-only; active
``market_evidence`` is the evidence that the governed persistence layer writes.
Keeping the diagnostic arrays inside every candidate metadata slice defeats the
APPEND byte bound because the same multi-megabyte arrays are repeated on every
slice.

This module sanitizes only the downloaded temporary copy used by persistence.
It never changes the source discovery artifact, never upgrades a candidate, and
fails closed unless ``can_execute`` is exactly ``False``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

CANDIDATE_LANES = ("team_event_candidates", "prop_candidates")
DIAGNOSTIC_EVIDENCE_FIELDS = (
    "market_evidence_historical",
    "market_evidence_stale",
)


def _row_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict) and value:
        return 1
    return 0


def sanitize_handoff(handoff: dict[str, Any]) -> dict[str, int]:
    """Remove diagnostic evidence payloads from persistence transport metadata.

    Returns row counts for observability. Active ``market_evidence`` and all
    governance fields are preserved byte-for-byte at the JSON-value level.
    """
    if handoff.get("can_execute") is not False:
        raise RuntimeError("SCOUT_HANDOFF_GOVERNANCE_INVALID")

    model = handoff.get("model_handoff")
    if not isinstance(model, dict):
        model = {}

    counts = {field: 0 for field in DIAGNOSTIC_EVIDENCE_FIELDS}
    counts["candidates"] = 0

    for lane in CANDIDATE_LANES:
        rows = model.get(lane)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("can_execute") is not False:
                raise RuntimeError("SCOUT_HANDOFF_GOVERNANCE_INVALID")
            counts["candidates"] += 1
            for field in DIAGNOSTIC_EVIDENCE_FIELDS:
                counts[field] += _row_count(row.pop(field, None))

    return counts


def sanitize_file(input_path: str) -> dict[str, int]:
    path = Path(input_path)
    handoff = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(handoff, dict):
        raise RuntimeError("SCOUT_HANDOFF_INVALID")
    counts = sanitize_handoff(handoff)
    path.write_text(json.dumps(handoff, separators=(",", ":")) + "\n", encoding="utf-8")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    counts = sanitize_file(args.input)
    print(json.dumps(counts, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
