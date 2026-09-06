#!/usr/bin/env python3
"""Run the MLB Game Winner shadow evaluation against the configured WOW DB."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import api_prod_market_acceptance as base
from v17.mlb_game_winner_shadow_db_runner import run_shadow_evaluation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--bootstrap-models", type=int, default=24)
    parser.add_argument("--min-forward", type=int, default=100)
    args = parser.parse_args()

    db = base.market_api.prod.get_client()
    report = run_shadow_evaluation(
        db,
        bootstrap_models=args.bootstrap_models,
        min_forward=args.min_forward,
    )
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
