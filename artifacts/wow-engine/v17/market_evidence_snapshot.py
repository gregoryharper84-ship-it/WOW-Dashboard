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

``--require-capture``
    Run a bounded production acceptance sample rather than the full daily
    matrix. One current sport/date is sampled per configured provider. A
    configured provider that is rate-limited, schema-blocked, or otherwise
    degraded remains explicit evidence telemetry but does not erase another
    provider's valid capture or a completed sporting probability. Authentication
    rejection and a total absence of captured market evidence fail closed.

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
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

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

ACCEPTANCE_READY = "MARKET_EVIDENCE_ACCEPTANCE_READY"
ACCEPTANCE_DEGRADED_READY = "MARKET_EVIDENCE_ACCEPTANCE_DEGRADED_READY"
ACCEPTANCE_BLOCKED = "MARKET_EVIDENCE_ACCEPTANCE_BLOCKED"


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


def _bounded_rate_limit_retry(call: Callable[[], sources.MarketEvidenceResult]) -> sources.MarketEvidenceResult:
    """Retry only HTTP 429, with a tiny bounded backoff and no auth retries.

    The default is one retry. This is intentionally conservative so the
    hardening itself cannot amplify provider quota pressure.
    """
    result = call()
    max_retries = max(0, min(int(os.environ.get("WOW_MARKET_EVIDENCE_429_RETRIES", "1")), 2))
    backoff = max(0.0, min(float(os.environ.get("WOW_MARKET_EVIDENCE_429_BACKOFF_SECONDS", "0.5")), 2.0))
    retries = 0
    while result.status == 429 and retries < max_retries:
        retries += 1
        if backoff:
            time.sleep(backoff * retries)
        result = call()
    return result


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


def _provider_health_map() -> dict[str, dict[str, Any]]:
    return {row["provider"]: row for row in sources.provider_health()["providers"]}


def collect_acceptance(
    sport_key: str = "baseball_mlb",
    *,
    date: str | None = None,
    opener: Any = None,
) -> dict[str, Any]:
    """Take one bounded live sample per configured provider.

    This intentionally does *not* walk every sport, both dates, and both
    TheRundown capabilities. The acceptance contract is to prove that at least
    one configured market-evidence source can produce normalized rows while
    preserving typed degradation for independent providers. That avoids the
    old acceptance harness becoming its own rate-limit incident.
    """
    date = date or snapshot_dates()[0]
    health = _provider_health_map()
    lanes: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    if health.get("RUNDOWN", {}).get("credential_configured"):
        rundown = _bounded_rate_limit_retry(
            lambda: live.rundown_market_evidence(
                sport_key,
                date,
                capability="events",
                opener=opener,
            )
        )
        lanes.append(_lane("RUNDOWN", sport_key, "events", rundown, date))
        if rundown.ok and isinstance(rundown.data, list):
            events.extend(rundown.data)

    if health.get("SHARPAPI", {}).get("credential_configured"):
        sharp = _bounded_rate_limit_retry(
            lambda: live.sharpapi_market_evidence(sport_key, opener=opener)
        )
        lanes.append(_lane("SHARPAPI", sport_key, "odds", sharp, None))
        if sharp.ok and isinstance(sharp.data, list):
            events.extend(sharp.data)

    configured = sorted(
        provider for provider, row in health.items()
        if row.get("credential_configured")
    )
    provider_capture = {
        provider: sum(
            lane["event_count"] for lane in lanes if lane["provider"] == provider
        )
        for provider in configured
    }
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
        for provider in configured
    }
    auth_blockers = sorted({
        lane["provider"]
        for lane in lanes
        if lane.get("degradation_class") == "AUTH_REJECTED"
    })
    capture_failures = sorted(
        provider for provider in configured if provider_capture.get(provider, 0) <= 0
    )
    captured_providers = sorted(
        provider for provider in configured if provider_capture.get(provider, 0) > 0
    )
    ready = bool(captured_providers) and not auth_blockers
    if not ready:
        status = ACCEPTANCE_BLOCKED
    elif capture_failures:
        status = ACCEPTANCE_DEGRADED_READY
    else:
        status = ACCEPTANCE_READY

    return {
        "schema_version": "wow.v17.market_evidence_acceptance.v1",
        "generated_at": _now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": status,
        "sport_key": sport_key,
        "date": date,
        "configured_providers": configured,
        "captured_providers": captured_providers,
        "provider_capture": provider_capture,
        "provider_degradation": provider_degradation,
        "capture_failures": capture_failures,
        "auth_blockers": auth_blockers,
        "ready_for_market_evidence": ready,
        "lanes": lanes,
        "captured_rows": len(events),
        "affects_fitted_model_availability": False,
        "prediction_authority": False,
        "exact_line_authority": False,
        "research_only": True,
        "can_execute": False,
        "secret_values_exposed": False,
    }


def acceptance_blockers(payload: dict[str, Any]) -> list[str]:
    """Return only production blockers for the bounded acceptance sample.

    An independent provider's rate limit, schema mismatch, or HTTP acquisition
    failure is a typed evidence degradation. It is observable but does not
    erase another provider's successful capture. Authentication rejection is a
    hard blocker, and zero successful configured providers is a hard blocker.
    """
    blockers = [f"AUTH_REJECTED:{provider}" for provider in payload.get("auth_blockers") or []]
    if payload.get("configured_providers") and not payload.get("captured_providers"):
        blockers.append("NO_PROVIDER_CAPTURE")
    return blockers


def collect(sports: list[str], *, dates: list[str] | None = None, opener: Any = None) -> dict[str, Any]:
    dates = dates or snapshot_dates()
    lanes: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    for sport_key in sports:
        sharp = _bounded_rate_limit_retry(lambda: live.sharpapi_market_evidence(sport_key, opener=opener))
        lanes.append(_lane("SHARPAPI", sport_key, "odds", sharp, None))
        if sharp.ok:
            events.extend(sharp.data)

        for date in dates:
            for capability in ("openers", "events"):
                result = _bounded_rate_limit_retry(
                    lambda sport_key=sport_key, date=date, capability=capability: live.rundown_market_evidence(
                        sport_key, date, capability=capability, opener=opener,
                    )
                )
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
    """Names every credentialed provider that returned no rows.

    Kept as a strict diagnostic helper for regression tests and offline audits.
    The production acceptance CLI uses ``acceptance_blockers`` so an optional
    independent provider cannot erase a successful market-evidence source.
    """
    health = _provider_health_map()
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
        help=(
            "bounded live acceptance: fail only on provider-auth rejection or "
            "when no configured provider can produce normalized rows"
        ),
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

    if args.require_capture:
        acceptance_sport = (
            args.sport_key
            or os.environ.get("WOW_MARKET_EVIDENCE_ACCEPTANCE_SPORT")
            or "baseball_mlb"
        )
        payload = collect_acceptance(acceptance_sport, date=args.date)
    else:
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
        return 0 if acceptance_blockers(payload) == [] else 1
    return 0 if payload["status"] == "MARKET_EVIDENCE_CAPTURED" else 1


if __name__ == "__main__":
    sys.exit(main())
