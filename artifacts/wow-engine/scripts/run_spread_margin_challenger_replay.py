#!/usr/bin/env python3
"""Run one read-only V17 point-spread historical replay and emit a JSON receipt."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from v17.spread_margin_replay import run_historical_replay


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", required=True, choices=("NFL", "NBA", "WNBA", "NCAAF", "NCAAB"))
    parser.add_argument("--min-rows", type=int, default=300)
    parser.add_argument("--ridge-alpha", type=float, default=4.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = run_historical_replay(sport=args.sport, min_rows=args.min_rows, ridge_alpha=args.ridge_alpha)
    text = json.dumps(result, sort_keys=True, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
