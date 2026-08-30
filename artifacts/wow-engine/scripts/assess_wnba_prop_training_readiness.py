#!/usr/bin/env python3
"""Assess real WNBA historical prop rows before any offline model fit.

Input is JSON Lines. Each line must satisfy wnba_prop_training_contract.
The command emits readiness metadata only. It does not train/register/certify a
model, publish probabilities, or mutate runtime capability state.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wnba_prop_training_contract import (  # noqa: E402
    WNBAPropTrainingContractError,
    normalize_historical_row,
    training_readiness,
)


def load_rows(path: Path):
    rows = []
    rejected: list[dict[str, object]] = []
    for line_number, raw in enumerate(path.read_text().splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise WNBAPropTrainingContractError("WNBA_TRAINING_ROW_NOT_OBJECT")
            rows.append(normalize_historical_row(payload))
        except (json.JSONDecodeError, WNBAPropTrainingContractError) as exc:
            code = getattr(exc, "code", "WNBA_TRAINING_JSON_INVALID")
            rejected.append({"line": line_number, "code": code})
    return rows, rejected


def assess(path: Path, stat_type: str) -> dict[str, object]:
    rows, rejected = load_rows(path)
    readiness = training_readiness(rows, stat_type)
    if rejected:
        readiness = dict(readiness)
        readiness["training_status"] = "TRAINING_DATA_UNAVAILABLE"
        readiness["runtime_model_status"] = "MODEL_UNAVAILABLE"
        readiness["probability_publishable"] = False
        readiness["can_execute"] = False
        blockers = list(readiness.get("blockers") or [])
        if "WNBA_TRAINING_ROWS_REJECTED" not in blockers:
            blockers.append("WNBA_TRAINING_ROWS_REJECTED")
        readiness["blockers"] = blockers
    readiness["accepted_row_n"] = len(rows)
    readiness["rejected_row_n"] = len(rejected)
    readiness["rejected_rows"] = rejected[:100]
    readiness["artifact_training_status"] = "NOT_ATTEMPTED"
    readiness["artifact_registration_status"] = "NOT_ATTEMPTED"
    readiness["artifact_certification_status"] = "NOT_ATTEMPTED"
    return readiness


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--stat-type", required=True, choices=("PTS", "REB", "AST", "3PM"))
    args = parser.parse_args()
    result = assess(args.input, args.stat_type)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["training_status"] == "READY_FOR_OFFLINE_FIT" else 2


if __name__ == "__main__":
    raise SystemExit(main())
