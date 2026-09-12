"""Refresh configured structured Scout research sources into raw snapshots.

Raw snapshots are persisted before candidate linkage. Identity linkage is a
separate verified step. This command is research-only and can_execute=false.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Any

try:
    from v17.research_source_adapters import SPORTSDATAIO_CAPABILITIES, sportsdataio_fetch
    from v17.research_source_snapshot_store import persist_source_result
except ModuleNotFoundError:
    from research_source_adapters import SPORTSDATAIO_CAPABILITIES, sportsdataio_fetch
    from research_source_snapshot_store import persist_source_result

DEFAULT_SPORTS = (
    "americanfootball_ncaaf",
    "americanfootball_nfl",
    "baseball_mlb",
    "basketball_nba",
    "basketball_wnba",
)


def refresh(*, sports: tuple[str, ...] = DEFAULT_SPORTS, today: str | None = None) -> dict[str, Any]:
    today = today or date.today().isoformat()
    receipts: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for cap in SPORTSDATAIO_CAPABILITIES:
        if cap.sport_key not in sports or not cap.supported:
            continue
        path_values = {"date": today} if "{date}" in str(cap.endpoint_template or "") else None
        result = sportsdataio_fetch(cap.sport_key, cap.capability, path_values=path_values)
        receipt = persist_source_result(result)
        (receipts if result.ok else blocked).append(receipt)
    return {
        "schema_version": "wow.v17.scout_source_refresh.v1",
        "status": "COMPLETE_WITH_BLOCKERS" if blocked else "COMPLETE",
        "sports": list(sports),
        "successful_snapshots": receipts,
        "blocked_snapshots": blocked,
        "prediction_authority": False,
        "can_execute": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sports", default=",".join(DEFAULT_SPORTS))
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--output")
    args = parser.parse_args()
    sports = tuple(x.strip() for x in args.sports.split(",") if x.strip())
    payload = refresh(sports=sports, today=args.date)
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        from pathlib import Path
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
