"""Refresh configured structured Scout sources; optionally enrich with public web evidence."""
from __future__ import annotations
import argparse, json, os
from datetime import date
from typing import Any
try:
    from v17.research_source_adapters import SPORTSDATAIO_CAPABILITIES, sportsdataio_fetch
    from v17.research_source_snapshot_store import persist_source_result
    from v17.firecrawl_research_adapter import MAX_TARGETS_PER_REFRESH, fetch_firecrawl_page
except ModuleNotFoundError:
    from research_source_adapters import SPORTSDATAIO_CAPABILITIES, sportsdataio_fetch
    from research_source_snapshot_store import persist_source_result
    from firecrawl_research_adapter import MAX_TARGETS_PER_REFRESH, fetch_firecrawl_page

DEFAULT_SPORTS = ("americanfootball_ncaaf","americanfootball_nfl","baseball_mlb","basketball_nba","basketball_wnba")


def _refresh_optional_firecrawl(sports: tuple[str, ...]) -> dict[str, Any]:
    """Optional evidence is kept out of the baseline source status and model path."""
    successful: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    try:
        targets = json.loads(os.environ.get("WOW_FIRECRAWL_TARGETS_JSON", "[]"))
    except (TypeError, ValueError):
        targets = None
    if not isinstance(targets, list) or len(targets) > MAX_TARGETS_PER_REFRESH:
        return {"status": "CONFIG_INVALID", "source_code": "SOURCE_TARGETS_CONFIG_INVALID",
                "successful_snapshots": [], "blocked_snapshots": [],
                "prediction_authority": False, "can_execute": False}

    seen: set[tuple[str, str, str]] = set()
    for target in targets:
        if not isinstance(target, dict) or not all(
            isinstance(target.get(k), str) and bool(target[k])
            for k in ("sport_key", "capability", "url")
        ):
            blocked.append({"source_status": "BLOCKED", "source_code": "SOURCE_TARGET_INVALID"})
            continue
        sport = target["sport_key"]
        if sport not in sports:
            continue
        identity = (sport, target["capability"], target["url"])
        if identity in seen:
            continue
        seen.add(identity)
        result = fetch_firecrawl_page(*identity)
        try:
            receipt = persist_source_result(result)
        except Exception:
            # An optional enrichment store failure must not fail the main Scout refresh.
            receipt = {"provider": "FIRECRAWL", "sport_key": sport,
                       "capability": target["capability"], "source_status": "BLOCKED",
                       "source_code": "SOURCE_SNAPSHOT_PERSIST_FAILED",
                       "prediction_authority": False, "can_execute": False}
        (successful if result.ok and receipt.get("source_status") == "OK" else blocked).append(receipt)
    return {"status": "COMPLETE_WITH_BLOCKERS" if blocked else "COMPLETE",
            "successful_snapshots": successful, "blocked_snapshots": blocked,
            "prediction_authority": False, "can_execute": False}


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
    payload = {
        "schema_version": "wow.v17.scout_source_refresh.v1",
        "status": "COMPLETE_WITH_BLOCKERS" if blocked else "COMPLETE",
        "sports": list(sports),
        "successful_snapshots": receipts,
        "blocked_snapshots": blocked,
        "prediction_authority": False,
        "can_execute": False,
    }
    if os.environ.get("WOW_FIRECRAWL_ENABLED", "").lower() in ("1", "true", "yes"):
        payload["optional_firecrawl"] = _refresh_optional_firecrawl(sports)
    return payload


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
