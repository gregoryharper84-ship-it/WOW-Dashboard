"""Refresh configured structured Scout research sources into raw snapshots.

Every invocation emits exactly one typed, machine-readable receipt (#1484):

- ``COMPLETE``                      health=HEALTHY   every attempted capability persisted OK
- ``COMPLETE_WITH_BLOCKERS``        health=DEGRADED  >=1 OK snapshot and >=1 blocked capability
- ``BLOCKED_SOURCE_CONFIGURATION``  health=BLOCKED   database and/or provider credential missing
- ``BLOCKED_SOURCE_ACQUISITION``    health=BLOCKED   configured, but zero OK snapshots persisted
- ``BLOCKED_REFRESH_EXCEPTION``     health=BLOCKED   refresh aborted before a typed result

The CLI exits non-zero (``EXIT_BLOCKED``) whenever health is BLOCKED so a run that
acquired nothing can never conclude green. Receipts carry only booleans, codes,
counts and snapshot identities; exception messages are never recorded because
transport errors can embed endpoint or credential material.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    from v17.research_source_adapters import SPORTSDATAIO_CAPABILITIES, sportsdataio_fetch
    from v17.research_source_snapshot_store import persist_source_result
except ModuleNotFoundError:
    from research_source_adapters import SPORTSDATAIO_CAPABILITIES, sportsdataio_fetch
    from research_source_snapshot_store import persist_source_result

SCHEMA_VERSION = "wow.v17.scout_source_refresh.v2"
DEFAULT_SPORTS = ("americanfootball_ncaaf", "americanfootball_nfl", "baseball_mlb", "basketball_nba", "basketball_wnba")
EXIT_HEALTHY_OR_DEGRADED = 0
EXIT_BLOCKED = 3

HEALTH_BY_STATUS = {
    "COMPLETE": "HEALTHY",
    "COMPLETE_WITH_BLOCKERS": "DEGRADED",
    "BLOCKED_SOURCE_CONFIGURATION": "BLOCKED",
    "BLOCKED_SOURCE_ACQUISITION": "BLOCKED",
    "BLOCKED_REFRESH_EXCEPTION": "BLOCKED",
}


def configuration_state() -> dict[str, bool]:
    """Boolean presence only — never the values."""
    return {
        "database_configured": bool(os.environ.get("WOW_SCOUT_DATABASE_URL") or os.environ.get("DATABASE_URL")),
        "sportsdataio_configured": bool(os.environ.get("SPORTSDATAIO_API_KEY") or os.environ.get("WOW_SPORTSDATAIO_API_KEY")),
    }


def _receipt(status: str, *, sports: tuple[str, ...], today: str, configuration: dict[str, bool],
             successful: list[dict[str, Any]], blocked: list[dict[str, Any]], attempted: int,
             error_type: str | None = None) -> dict[str, Any]:
    blocker_codes = sorted({str(b.get("source_code")) for b in blocked if b.get("source_code")})
    if status == "BLOCKED_SOURCE_CONFIGURATION":
        blocker_codes = sorted(set(blocker_codes) | {"SOURCE_CONFIGURATION_MISSING"})
    if error_type:
        blocker_codes = sorted(set(blocker_codes) | {f"REFRESH_EXCEPTION:{error_type}"})
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "health": HEALTH_BY_STATUS[status],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": today,
        "sports": list(sports),
        "configuration": dict(configuration),
        "attempted_count": attempted,
        "successful_count": len(successful),
        "blocked_count": len(blocked),
        "blocker_codes": blocker_codes,
        "error_type": error_type,
        "successful_snapshots": successful,
        "blocked_snapshots": blocked,
        "prediction_authority": False,
        "can_execute": False,
    }


def _exception_entry(cap: Any, stage: str, exc: BaseException) -> dict[str, Any]:
    return {
        "provider": getattr(cap, "provider", "SPORTSDATAIO"),
        "sport_key": cap.sport_key,
        "capability": cap.capability,
        "source_status": "BLOCKED",
        "source_code": f"{stage}_EXCEPTION:{type(exc).__name__}",
        "persisted": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def refresh(*, sports: tuple[str, ...] = DEFAULT_SPORTS, today: str | None = None) -> dict[str, Any]:
    today = today or date.today().isoformat()
    configuration = configuration_state()
    if not all(configuration.values()):
        # Fail closed before any provider or database call.
        return _receipt("BLOCKED_SOURCE_CONFIGURATION", sports=sports, today=today, configuration=configuration,
                        successful=[], blocked=[], attempted=0)

    successful: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    attempted = 0
    for cap in SPORTSDATAIO_CAPABILITIES:
        if cap.sport_key not in sports or not cap.supported:
            continue
        attempted += 1
        path_values = {"date": today} if "{date}" in str(cap.endpoint_template or "") else None
        try:
            result = sportsdataio_fetch(cap.sport_key, cap.capability, path_values=path_values)
        except Exception as exc:  # noqa: BLE001 — typed into the receipt, message deliberately dropped
            blocked.append(_exception_entry(cap, "FETCH", exc))
            continue
        try:
            persisted = dict(persist_source_result(result))
        except Exception as exc:  # noqa: BLE001
            blocked.append(_exception_entry(cap, "PERSISTENCE", exc))
            continue
        persisted["persisted"] = True
        (successful if result.ok else blocked).append(persisted)

    if not successful:
        status = "BLOCKED_SOURCE_ACQUISITION"
    elif blocked:
        status = "COMPLETE_WITH_BLOCKERS"
    else:
        status = "COMPLETE"
    return _receipt(status, sports=sports, today=today, configuration=configuration,
                    successful=successful, blocked=blocked, attempted=attempted)


def exit_code_for(receipt: dict[str, Any]) -> int:
    # Unknown/missing health fails closed.
    return EXIT_HEALTHY_OR_DEGRADED if receipt.get("health") in ("HEALTHY", "DEGRADED") else EXIT_BLOCKED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sports", default=",".join(DEFAULT_SPORTS))
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    sports = tuple(x.strip() for x in args.sports.split(",") if x.strip())
    try:
        payload = refresh(sports=sports, today=args.date)
    except Exception as exc:  # noqa: BLE001
        payload = _receipt("BLOCKED_REFRESH_EXCEPTION", sports=sports, today=args.date,
                           configuration=configuration_state(), successful=[], blocked=[], attempted=0,
                           error_type=type(exc).__name__)
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    health = payload["health"]
    if health == "BLOCKED":
        print(f"::error::Scout structured-source refresh {payload['status']}: {','.join(payload['blocker_codes'])}")
    elif health == "DEGRADED":
        print(f"::warning::Scout structured-source refresh DEGRADED: {payload['blocked_count']} of {payload['attempted_count']} capabilities blocked")
    return exit_code_for(payload)


if __name__ == "__main__":
    raise SystemExit(main())
