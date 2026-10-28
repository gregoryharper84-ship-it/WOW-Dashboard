"""Bounded, read-only Datadog log -> canonical Reporter candidate signals.

No raw message, request body, URL, credential, user ID, or provider response is
returned or written. Logs are untrusted *evidence*, never agent instructions.
An authenticated existing Engineering controller must assign the incident ID,
reconcile a durable dedupe record and acquire its own writer lease before work.

custom_gpt_identity=WOW_BETTING_ENGINE
runtime_generation=V17_ACTIVE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SERVICE = "wow-governed-probability-engine"
SITES = frozenset({"datadoghq.com", "us3.datadoghq.com", "us5.datadoghq.com", "datadoghq.eu", "ap1.datadoghq.com"})
MAX_PAGES = 4
PAGE_LIMIT = 250
MAX_EVENTS = MAX_PAGES * PAGE_LIMIT
SAFE_SHA = re.compile(r"^[a-f0-9]{40}$")
CODE_PATTERN = re.compile(r"\b(WOW_V17_[A-Z0-9_]{3,96}|WOW_PROP_[A-Z0-9_]{3,96}|MODEL_[A-Z0-9_]{3,96})\b")
HTTP_PATTERN = re.compile(r'"(GET|POST|PUT|PATCH|DELETE)\s+([^\s"]+)\s+HTTP/[0-9.]+"\s+(\d{3})')
ALLOWED_TYPED_CODES = frozenset({
    "WOW_V17_NFL_PICKEM_ASYNC_CLAIM_FAILED",
    "WOW_PROP_MODEL_SELF_ACCEPTANCE",
    "MODEL_SCORER_FAILED", "MODEL_INPUTS_INSUFFICIENT", "MODEL_OUTPUT_INVALID",
    "MODEL_UNAVAILABLE", "MODEL_RECONCILIATION_FAILED",
    "WOW_V17_PRODUCTION_PERSISTENCE_FAILED",
})


class SourceUnavailable(RuntimeError):
    """A fixed public reason code, never an upstream exception/string."""


def utc_time(value: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("INVALID_UTC_TIMESTAMP") from None
    if stamp.tzinfo is None:
        raise ValueError("INVALID_UTC_TIMESTAMP")
    return stamp.astimezone(timezone.utc)


def _url_route(path: str) -> str:
    """Only predefined route names; never store arbitrary path or query text."""
    path = path.split("?", 1)[0]
    if path == "/":
        return "ROOT_PROBE"
    if path in {"/health", "/health/live", "/governance"}:
        return "HEALTH_PROBE"
    if re.fullmatch(r"/v17/scout-handoff-runs/[A-Za-z0-9_-]{1,120}", path):
        return "SCOUT_HANDOFF"
    if path in {"/score-pick-request", "/score-team-event-request"}:
        return "INTERACTIVE_SCORE"
    return "OTHER_ROUTE"


def _parse_event(item: Mapping[str, Any], begin: datetime, end: datetime) -> dict[str, str] | None:
    """Accept only structurally valid Datadog logs from the exact production service."""
    attrs = item.get("attributes", item)
    if not isinstance(attrs, Mapping) or attrs.get("service") != SERVICE:
        return None
    message = attrs.get("message", "")
    if not isinstance(message, str) or len(message) > 16_384:
        return None
    try:
        stamp = utc_time(attrs.get("timestamp", ""))
    except ValueError:
        return None
    if not begin <= stamp < end:
        return None
    status = str(attrs.get("status", "")).lower()
    if status not in {"info", "warn", "warning", "error", "critical", "alert", "emergency"}:
        return None
    code_match = CODE_PATTERN.search(message)
    code = code_match.group(1) if code_match and code_match.group(1) in ALLOWED_TYPED_CODES else None
    # Evidence is not an incident solely because a known token is present.
    # Reject INFO and require the explicit failed prop self-acceptance result.
    if code and status == "info":
        return None
    if code == "WOW_PROP_MODEL_SELF_ACCEPTANCE" and not re.search(r"\bresult=FAIL\b", message):
        return None
    route = "OTHER_ROUTE"
    if code:
        if code == "WOW_V17_NFL_PICKEM_ASYNC_CLAIM_FAILED":
            route = "PICKEM_QUEUE_CLAIM"
        elif code == "WOW_PROP_MODEL_SELF_ACCEPTANCE":
            route = "PROP_SELF_ACCEPTANCE"
        elif code.startswith("MODEL_"):
            route = "FITTED_MODEL"
        else:
            route = "PERSISTENCE"
    else:
        http = HTTP_PATTERN.search(message)
        if http:
            route = _url_route(http.group(2))
            response = int(http.group(3))
            if response >= 500:
                code = "HTTP_5XX"
            elif response == 422:
                code = "HTTP_422"
            elif response == 401:
                code = "HTTP_401"
            elif response == 404 and route == "ROOT_PROBE":
                # Expected internet probes are not automatically defects.
                return None
    if code is None:
        return None
    # A single fake line cannot forge provenance or privileges. No free-form
    # log string is returned. Optional version is accepted only as an exact SHA.
    sha = attrs.get("version", "")
    sha = sha if isinstance(sha, str) and SAFE_SHA.fullmatch(sha) else "UNKNOWN_SHA"
    return {"timestamp_utc": stamp.isoformat().replace("+00:00", "Z"),
            "route": route, "typed_failure": code, "sha": sha}


def classify(events: Iterable[Mapping[str, Any]], *, begin: datetime, end: datetime,
             source_complete: bool = True) -> dict[str, Any]:
    """Aggregate redacted stable fingerprints; NEVER return raw events."""
    if begin.tzinfo is None or end.tzinfo is None or end <= begin:
        raise ValueError("INVALID_WINDOW")
    if not source_complete:
        raise SourceUnavailable("SOURCE_INCOMPLETE")
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    seen = 0
    for event in events:
        seen += 1
        if seen > MAX_EVENTS:
            raise SourceUnavailable("SOURCE_EVENT_LIMIT_EXCEEDED")
        if not isinstance(event, Mapping):
            continue
        row = _parse_event(event, begin, end)
        if row:
            grouped[(row["route"], row["typed_failure"])].append(row)
    findings = []
    for (route, code), matches in sorted(grouped.items()):
        count = len(matches)
        # Repeated validation errors are actionable; a single 422 isn't.
        threshold = {"HTTP_422": 10, "HTTP_401": 5, "HTTP_5XX": 3,
                     "MODEL_UNAVAILABLE": 3}.get(code, 1)
        if count < threshold:
            continue
        severity = "P1" if code in {"HTTP_5XX", "MODEL_SCORER_FAILED", "MODEL_RECONCILIATION_FAILED",
                                         "WOW_V17_PRODUCTION_PERSISTENCE_FAILED"} else "P2"
        if route == "PICKEM_QUEUE_CLAIM" and count >= 3:
            severity = "P1"
        if code == "HTTP_401" and route == "SCOUT_HANDOFF":
            severity = "P1" if count >= 10 else "P2"
        # Identity excludes transient SHA/run/time, includes canonical service,
        # route and *typed* failure. Existing Reporter must dedupe durably.
        basis = json.dumps([SERVICE, route, code], separators=(",", ":"))
        fingerprint = hashlib.sha256(basis.encode()).hexdigest()
        stamps = [r["timestamp_utc"] for r in matches]
        shas = sorted({r["sha"] for r in matches if r["sha"] != "UNKNOWN_SHA"})
        findings.append({
            "source": "DATADOG", "subsystem": "BACKEND_RUNTIME", "service": SERVICE,
            "failure_boundary": route, "typed_failure": code, "severity": severity,
            "fingerprint": fingerprint, "count": count, "first_seen_utc": min(stamps),
            "last_seen_utc": max(stamps), "release_shas": shas,
            "next_owner": "REPORTER_AGENT", "state": "CANDIDATE_UNVERIFIED",
            "can_execute": False,
        })
    return {"status": "CANDIDATES" if findings else "NO_ACTION",
            "source": "DATADOG", "service": SERVICE,
            "window_start_utc": begin.isoformat().replace("+00:00", "Z"),
            "window_end_utc": end.isoformat().replace("+00:00", "Z"),
            "events_inspected": seen, "findings": findings,
            "needs_durable_reporter_dedupe": True,
            "terminal_authority": "V17_TERMINAL_REDUCER", "can_execute": False,
            "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS": True}


def fetch_datadog(*, begin: datetime, end: datetime, api_key: str, app_key: str,
                  site: str = "datadoghq.com",
                  opener: Callable[..., Any] = urlopen) -> list[dict[str, Any]]:
    """Bounded Datadog REST read; fail closed on truncation/HTTP/transport.

    The API response itself is never printed. Credentials stay request headers.
    """
    if not api_key or not app_key:
        raise SourceUnavailable("DATADOG_CREDENTIAL_MISSING")
    if site not in SITES:
        raise SourceUnavailable("DATADOG_SITE_NOT_ALLOWED")
    url = f"https://api.{site}/api/v2/logs/events/search"
    cursor = None
    events: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        payload: dict[str, Any] = {
            "filter": {"from": begin.isoformat(), "to": end.isoformat(),
                       "query": f"service:{SERVICE} status:(error OR warn OR critical)"},
            "page": {"limit": PAGE_LIMIT},
            "sort": "timestamp",
        }
        if cursor:
            payload["page"]["cursor"] = cursor
        req = Request(url, data=json.dumps(payload).encode(), method="POST", headers={
            "DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key,
            "Content-Type": "application/json", "Accept": "application/json",
        })
        try:
            with opener(req, timeout=20) as response:
                raw = response.read(2_000_001)
        except HTTPError as exc:
            if exc.code == 429:
                raise SourceUnavailable("DATADOG_RATE_LIMITED") from None
            raise SourceUnavailable("DATADOG_HTTP_FAILED") from None
        except (OSError, URLError, TimeoutError):
            raise SourceUnavailable("DATADOG_TRANSPORT_FAILED") from None
        if len(raw) > 2_000_000:
            raise SourceUnavailable("DATADOG_PAGE_OVERSIZE")
        try:
            doc = json.loads(raw)
            rows = doc["data"]
            if not isinstance(rows, list) or len(rows) > PAGE_LIMIT:
                raise ValueError()
            page_obj = doc.get("meta", {}).get("page", {})
            next_cursor = page_obj.get("after")
            if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 2048):
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise SourceUnavailable("DATADOG_RESPONSE_INVALID") from None
        events.extend(rows)
        if not next_cursor:
            return events
        if next_cursor == cursor:
            raise SourceUnavailable("DATADOG_PAGINATION_STALLED")
        cursor = next_cursor
    raise SourceUnavailable("DATADOG_RESULT_TRUNCATED")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only, redacted WOW Datadog incident intake")
    parser.add_argument("--hours", type=int, default=2)
    parser.add_argument("--site", default=os.environ.get("DD_SITE", "datadoghq.com"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if not 1 <= args.hours <= 24:
        parser.error("hours must be between 1 and 24")
    end = datetime.now(timezone.utc).replace(microsecond=0)
    begin = end - timedelta(hours=args.hours)
    try:
        events = fetch_datadog(begin=begin, end=end,
                               api_key=os.environ.get("DD_API_KEY", ""),
                               app_key=os.environ.get("DD_APPLICATION_KEY", ""),
                               site=args.site)
        report = classify(events, begin=begin, end=end)
    except SourceUnavailable as exc:
        # Leave a typed, safe, *unsuccessful* receipt. Never write bogus NO_ACTION.
        report = {"status": "SOURCE_UNAVAILABLE", "reason": str(exc),
                  "source": "DATADOG", "can_execute": False}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, sort_keys=True) + "\n")
        print(f"WOW_LOG_INTAKE_SOURCE_UNAVAILABLE:{exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"WOW_LOG_INTAKE_{report['status']} findings={len(report['findings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
