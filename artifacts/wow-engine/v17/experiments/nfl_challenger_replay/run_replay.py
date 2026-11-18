"""Offline NFL chronological replay command; no network, database writes or promotion."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from replay import evaluate_chronological_replay


def run_from_file(source: Path, output: Path | None = None, *,
                  fit_end_season: int = 2024, calibration_season: int = 2025,
                  holdout_season: int = 2026) -> dict[str, Any]:
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("expected {rows:[], incumbent:{}} JSON object")
    result = evaluate_chronological_replay(
        data.get("rows"), data.get("incumbent"),
        fit_end_season=fit_end_season, calibration_season=calibration_season,
        holdout_season=holdout_season,
    )
    if output is not None and result["status"] == "RETROSPECTIVE_RESEARCH_ONLY":
        output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False)+"\n",encoding="utf-8")
    return result


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,required=True,help="JSON object with frozen evidence rows and actual incumbent receipts")
    parser.add_argument("--out",type=Path,help="Optional local research JSON report")
    parser.add_argument("--fit-end-season",type=int,default=2024)
    parser.add_argument("--calibration-season",type=int,default=2025)
    parser.add_argument("--holdout-season",type=int,default=2026)
    args=parser.parse_args()
    try:
        result=run_from_file(args.input,args.out,
                             fit_end_season=args.fit_end_season,
                             calibration_season=args.calibration_season,
                             holdout_season=args.holdout_season)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result,indent=2,sort_keys=True,allow_nan=False))
    return 0 if result["status"] == "RETROSPECTIVE_RESEARCH_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
