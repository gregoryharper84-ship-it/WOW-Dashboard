"""Offline-only paired chronological holdout validator (no network or mutations)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from learning_shadow import chronological_holdout_shadow


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", type=Path, help="Frozen paired receipts + independent finals")
    parser.add_argument("--train-end", required=True)
    parser.add_argument("--calibration-end", required=True)
    parser.add_argument("--holdout-start", required=True)
    parser.add_argument("--holdout-end", required=True)
    parser.add_argument("--out", type=Path, help="Optional local read-only evaluation report")
    args = parser.parse_args()
    if not args.jsonl.is_file():
        parser.error("Holdout file does not exist")
    rows = []
    try:
        with args.jsonl.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    obj = json.loads(line)
                    if not isinstance(obj, dict) or obj.get("synthetic") is True:
                        parser.error("Require object receipts; synthetic data cannot be used for holdout acceptance")
                    rows.append(obj)
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"Invalid receipt file: {exc}")
    result = chronological_holdout_shadow(
        rows, train_end_utc=args.train_end,
        calibration_end_utc=args.calibration_end,
        holdout_start_utc=args.holdout_start,
        holdout_end_utc=args.holdout_end,
    )
    text = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["status"] == "PAIRED_HOLDOUT_RESEARCH_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
