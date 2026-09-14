"""Daily cross-book market evidence snapshot for WOW V17 Scout (research-only).

Two modes:

``--probe``
    Fetch one provider capability and print only its *structure* — key names,
    container types and lengths, no values. This is how an unverified provider
    response schema gets pinned without a credential or a price ever being
    printed into a log or a transcript.

default (snapshot)
    Walk the configured sports, capture opener and current cross-book prices,
    and write one research-only evidence file. Every sport terminates exactly
    once, as either ``CAPTURED`` or a typed blocker; the run reconciles
    ``requested == captured + blocked``.

Nothing here publishes probability, edge, stake or an executable instruction.
A provider outage degrades to a typed blocker with zero rows; market prices
are never substituted for a missing model.

Failure boundary: an unavailable market feed terminates as
``MARKET_DATA_UNOBTAINABLE``. That is an evidence outcome and never, on its
own, downgrades a fitted sporting probability — ``MODEL_UNAVAILABLE`` stays
reserved for an absent fitted probability capability, which this lane cannot
cause.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v17 import market_evidence_hardening as hardening
from v17 import market_evidence_sources as sources
from v17 import market_evidence_native_live as live

DEFAULT_SPORTS = (
    "americanfootball_nfl",
    "americanfootball_ncaaf",
    "baseball_mlb",
    "basketball_nba",
    "icehockey_nhl",
)


def _enable_research_market_evidence() -> None:
    """Enable the research evidence lane unless the dedicated kill switch is set."""
    kill_switch = os.environ.get("WOW_MARKET_EVIDENCE_KILL_SWITCH", "false").strip().lower() == "true"
    sources.ENABLED = not kill_switch
    os.environ["WOW_MARKET_EVIDENCE_ENABLED"] = "false" if kill_switch else "true"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def snapshot_dates(now: datetime | None = None) -> list[str]:
    """Today and tomorrow in UTC — a daily run must cover the next slate too."""
    base = now or _now()
    return [(base + timedelta(days=offset)).strftime("%Y-%m-%d") for offset in (0, 1)]


def _sharpapi_live_params(sport_key: str) -> dict[str, str] | None:
    """Build the documented SharpAPI league query for a WOW sport key."""
    league = sources.sharpapi_league(sport_key)
    if not league:
        return None
    return {"league": str(league).upper()}


def _lane(provider: str, sport_key: str, capability: str, result: sources.MarketEvidenceResult, date: str | None) -> dict[str, Any]:
    lane: dict[str, Any] = {
        "provider": provider,
        "sport_key": sport_key,
        "capability": capability,
        "date": date,
        "status": "CAPTURED" if result.ok else "BLOCKED",
        "reason_code": result.code,
        "http_status": result.status,
        "observed_at": result.observed_at,
        "event_count": len(result.data) if result.ok and isinstance(result.data, list) else 0,
        "degradation_class": None if result.ok else hardening.provider_degradation(result.code, result.status),
        "affects_model_capability": False,
        "prediction_authority": False,
        "exact_line_authority": False,
        "research_only": True,
        "can_execute": False,
    }
    if result.schema_probe:
        lane["schema_probe"] = result.schema_probe
    return lane


def collect(sports: list[str], *, dates: list[str] | None = None, opener: Any = None) -> dict[str, Any]:
    dates = dates or snapshot_dates()
    lanes: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    for sport_key in sports:
        sharp = live.sharpapi_market_evidence(sport_key, opener=opener)
        lanes.append(_lane("SHARPAPI", sport_key, "odds", sharp, None))
        if sharp.ok:
            events.extend(sharp.data)

        for date in dates:
            for capability in ("openers", "events"):
                result = live.rundown_market_evidence(sport_key, date, capability=capability, opener=opener)
                lanes.append(_lane("RUNDOWN", sport_key, capability, result, date))
                if result.ok:
                    events.extend(result.data)

    captured = sum(1 for lane in lanes if lane["status"] == "CAPTURED")
    blocked = sum(1 for lane in lanes if lane["status"] == "BLOCKED")
    books = sorted({
        book.get("key")
        for event in events
        for book in event.get("bookmakers") or []
        if isinstance(book, dict) and book.get("key")
    })
    generated_at = _now().replace(microsecond=0).isoformat().replace("+00:00", "Z")
    hardening_report = hardening.analyze_snapshot_events(
        events,
        generated_at=generated_at,
        max_age_minutes=float(sources.MAX_AGE_MINUTES),
    )

    provider_degradation = {
        provider: [
            {
                "sport_key": lane["sport_key"],
                "capability": lane["capability"],
                "date": lane["date"],
                "reason_code": lane["reason_code"],
                "http_status": lane["http_status"],
                "degradation_class": lane["degradation_class"],
            }
            for lane in lanes
            if lane["provider"] == provider and lane["status"] == "BLOCKED"
        ]
        for provider in sorted({lane["provider"] for lane in lanes})
    }

    return {
        "schema_version": "wow.v17.market_evidence_snapshot.v2",
        "generated_at": generated_at,
        "dates": dates,
        "sports_requested": list(sports),
        "status": "MARKET_EVIDENCE_CAPTURED" if captured else sources.MARKET_DATA_UNOBTAINABLE,
        "lanes": lanes,
        "events": events,
        "bookmakers_seen": books,
        "reconciliation": {
            "lanes_requested": len(lanes),
            "lanes_captured": captured,
            "lanes_blocked": blocked,
            "captured_rows": len(events),
            "balanced": len(lanes) == captured + blocked,
        },
        "provider_capture": {
            provider: sum(
                lane["event_count"] for lane in lanes if lane["provider"] == provider
            )
            for provider in sorted({lane["provider"] for lane in lanes})
        },
        "provider_degradation": provider_degradation,
        "freshness": {
            key: value
            for key, value in hardening_report.items()
            if key != "disagreement_alerts"
        },
        "source_disagreement_alerts": hardening_report["disagreement_alerts"],
        "source_disagreement_alert_count": hardening_report["disagreement_alert_count"],
        "affects_fitted_model_availability": False,
        "research_ceiling": "RESEARCH_INTEREST",
        "source_class": sources.SOURCE_CLASS,
        "prediction_authority": False,
        "exact_line_authority": False,
        "research_only": True,
        "can_execute": False,
    }


def probe(provider: str, capability: str, *, sport_key: str | None = None, date: str | None = None) -> dict[str, Any]:
    provider_name = provider.upper()
    path_values: dict[str, Any] = {}
    if sport_key and provider_name == "RUNDOWN" and capability in {"events", "openers"}:
        resolved = sources.rundown_sport_id(sport_key)
        if not resolved.ok:
            return {"provider": provider_name, "capability": capability, "status": "BLOCKED", "reason_code": resolved.code}
        path_values["sport_id"] = resolved.data
        path_values["date"] = date or snapshot_dates()[0]
    elif date:
        path_values["date"] = date

    params = _sharpapi_live_params(sport_key) if provider_name == "SHARPAPI" and sport_key else None
    result = sources.fetch(provider, capability, path_values=path_values, params=params)
    if not result.ok:
        return {"provider": provider_name, "capability": capability, "status": "BLOCKED", "reason_code": result.code, "http_status": result.status}
    return {
        "provider": provider_name,
        "capability": capability,
        "status": "PROBED",
        "http_status": result.status,
        "note": "structure only; no provider values or credentials are included",
        **sources.structural_probe(result.data),
    }


def acceptance_failures(payload: dict[str, Any]) -> list[str]:
    """Names every credentialed provider that returned no rows."""
    health = {p["provider"]: p for p in sources.provider_health()["providers"]}
    capture = payload.get("provider_capture") or {}
    failures: list[str] = []
    for provider, rows in sorted(capture.items()):
        if not health.get(provider, {}).get("credential_configured"):
            continue
        if rows <= 0:
            failures.append(provider)
    return failures


def main(argv: list[str] | None = None) -> int:
    _enable_research_market_evidence()
    parser = argparse.ArgumentParser(description="WOW V17 research-only market evidence snapshot")
    parser.add_argument("--output", help="write the snapshot JSON here")
    parser.add_argument("--sports", default=",".join(DEFAULT_SPORTS))
    parser.add_argument("--probe", action="store_true", help="print a value-free structural probe instead of a snapshot")
    parser.add_argument(
        "--require-capture",
        action="store_true",
        help="acceptance mode: exit non-zero unless every credentialed provider returned rows",
    )
    parser.add_argument("--provider", default="RUNDOWN")
    parser.add_argument("--capability", default="sports")
    parser.add_argument("--sport-key")
    parser.add_argument("--date")
    args = parser.parse_args(argv)

    if args.probe:
        report = probe(args.provider, args.capability, sport_key=args.sport_key, date=args.date)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report.get("status") == "PROBED" else 1

    sports = [s.strip() for s in str(args.sports).split(",") if s.strip()]
    payload = collect(sports)
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n")
    else:
        print(text)

    if args.require_capture:
        return 0 if acceptance_failures(payload) == [] else 1
    return 0 if payload["status"] == "MARKET_EVIDENCE_CAPTURED" else 1


if __name__ == "__main__":
    sys.exit(main())