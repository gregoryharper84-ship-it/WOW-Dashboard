"""Production hardening helpers for V17 sportsbook market evidence.

This module is deliberately evidence-only.  It classifies freshness, surfaces
cross-provider quote disagreements, and classifies provider degradation.  It
never computes or mutates sporting model probability, calibration, ranking,
stake, or execution state.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

CAN_EXECUTE = False
DEFAULT_MAX_AGE_MINUTES = 15.0
DEFAULT_DISAGREEMENT_IMPLIED_DELTA = 0.035
EVENT_TIME_TOLERANCE_MINUTES = 15.0

FRESH = "FRESH"
AGING = "AGING"
STALE = "STALE"
UNAVAILABLE = "UNAVAILABLE"
HISTORICAL = "HISTORICAL_OPENER"


def _aware(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _provider(event: dict[str, Any]) -> tuple[str, str | None]:
    marker = event.get("_wow_market_evidence") or event.get("_wow_secondary_source") or {}
    marker = marker if isinstance(marker, dict) else {}
    raw = str(marker.get("provider") or "UNKNOWN")
    provider = raw.removesuffix("_MARKET_EVIDENCE")
    capability = marker.get("provider_detail") or marker.get("capability")
    return provider, str(capability) if capability else None


def classify_freshness(
    timestamp: Any,
    *,
    now: datetime | None = None,
    max_age_minutes: float = DEFAULT_MAX_AGE_MINUTES,
    historical: bool = False,
) -> dict[str, Any]:
    """Classify one evidence timestamp at the point it is consumed."""
    if historical:
        return {
            "state": HISTORICAL,
            "age_minutes": None,
            "max_age_minutes": max_age_minutes,
            "research_usable": False,
            "current_market_evidence": False,
            "prediction_authority": False,
            "can_execute": False,
        }
    parsed = _aware(timestamp)
    now = now or datetime.now(timezone.utc)
    if parsed is None:
        state = UNAVAILABLE
        age = None
    else:
        age = max(0.0, (now - parsed).total_seconds() / 60.0)
        if age > max_age_minutes:
            state = STALE
        elif age > max_age_minutes * 0.5:
            state = AGING
        else:
            state = FRESH
    return {
        "state": state,
        "age_minutes": round(age, 3) if age is not None else None,
        "max_age_minutes": max_age_minutes,
        "research_usable": state in {FRESH, AGING},
        "current_market_evidence": state in {FRESH, AGING},
        "prediction_authority": False,
        "can_execute": False,
    }


def provider_degradation(code: Any, status: Any = None) -> str | None:
    token = str(code or "").upper()
    try:
        http = int(status) if status is not None else None
    except Exception:
        http = None
    if http == 429 or token.endswith("_HTTP_429"):
        return "RATE_LIMITED"
    if http in {401, 403} or token.endswith("_HTTP_401") or token.endswith("_HTTP_403"):
        return "AUTH_REJECTED"
    if "SCHEMA_UNRECOGNISED" in token or "SCHEMA_UNRECOGNIZED" in token:
        return "SCHEMA_UNRECOGNISED"
    if token in {"MARKET_EVIDENCE_CREDENTIAL_UNCONFIGURED", "MARKET_EVIDENCE_DISABLED"}:
        return token
    if token and token not in {"MARKET_EVIDENCE_NORMALISED", "MARKET_EVIDENCE_FETCH_OK"}:
        return "SOURCE_ACQUISITION_DEGRADED"
    return None


def american_implied_probability(price: Any) -> float | None:
    """Price-derived market metric used only for disagreement detection."""
    try:
        value = float(price)
    except Exception:
        return None
    if value == 0:
        return None
    if value < 0:
        return (-value) / ((-value) + 100.0)
    return 100.0 / (value + 100.0)


def _event_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if str(left.get("sport_key") or "") != str(right.get("sport_key") or ""):
        return False
    for field in ("home_team", "away_team"):
        a = _norm(left.get(field))
        b = _norm(right.get(field))
        if not a or not b or a != b:
            return False
    a_time = _aware(left.get("commence_time"))
    b_time = _aware(right.get("commence_time"))
    if a_time is None or b_time is None:
        return False
    return abs((a_time - b_time).total_seconds()) / 60.0 <= EVENT_TIME_TOLERANCE_MINUTES


def _quote_rows(event: dict[str, Any]) -> list[dict[str, Any]]:
    provider, capability = _provider(event)
    rows: list[dict[str, Any]] = []
    for book in event.get("bookmakers") or []:
        if not isinstance(book, dict):
            continue
        book_title = str(book.get("title") or book.get("key") or "")
        book_key = _norm(book_title)
        for market in book.get("markets") or []:
            if not isinstance(market, dict):
                continue
            market_key = str(market.get("key") or "")
            updated = market.get("last_update") or book.get("last_update")
            for outcome in market.get("outcomes") or []:
                if not isinstance(outcome, dict):
                    continue
                rows.append({
                    "provider": provider,
                    "capability": capability,
                    "bookmaker": book_title,
                    "bookmaker_key": book_key,
                    "market_key": market_key,
                    "outcome_name": str(outcome.get("name") or ""),
                    "outcome_key": _norm(outcome.get("name")),
                    "point": outcome.get("point"),
                    "price": outcome.get("price"),
                    "updated_at": updated,
                })
    return rows


def _quote_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    point = "" if row.get("point") is None else str(row.get("point"))
    return (
        str(row.get("bookmaker_key") or ""),
        str(row.get("market_key") or ""),
        str(row.get("outcome_key") or ""),
        point,
    )


def disagreement_alerts(
    events: list[dict[str, Any]],
    *,
    threshold: float | None = None,
) -> list[dict[str, Any]]:
    """Compare exact same-book, same-market, same-line quotes across providers."""
    if threshold is None:
        threshold = float(os.environ.get(
            "WOW_MARKET_EVIDENCE_DISAGREEMENT_IMPLIED_DELTA",
            str(DEFAULT_DISAGREEMENT_IMPLIED_DELTA),
        ))
    alerts: list[dict[str, Any]] = []
    for i, left in enumerate(events):
        left_provider, left_capability = _provider(left)
        if left_capability == "openers":
            continue
        for right in events[i + 1:]:
            right_provider, right_capability = _provider(right)
            if right_capability == "openers" or not left_provider or not right_provider:
                continue
            if left_provider == right_provider or not _event_matches(left, right):
                continue
            left_rows = {_quote_key(row): row for row in _quote_rows(left)}
            right_rows = {_quote_key(row): row for row in _quote_rows(right)}
            for key in sorted(set(left_rows) & set(right_rows)):
                a = left_rows[key]
                b = right_rows[key]
                pa = american_implied_probability(a.get("price"))
                pb = american_implied_probability(b.get("price"))
                if pa is None or pb is None:
                    continue
                delta = abs(pa - pb)
                if delta < threshold:
                    continue
                alerts.append({
                    "code": "MARKET_SOURCE_DISAGREEMENT",
                    "sport_key": left.get("sport_key"),
                    "home_team": left.get("home_team"),
                    "away_team": left.get("away_team"),
                    "bookmaker": a.get("bookmaker"),
                    "market_key": a.get("market_key"),
                    "outcome_name": a.get("outcome_name"),
                    "point": a.get("point"),
                    "providers": sorted([left_provider, right_provider]),
                    "market_implied_probability_delta": round(delta, 6),
                    "threshold": threshold,
                    "action": "PRESERVE_ALL_EVIDENCE_AND_FLAG",
                    "model_probability_mutated": False,
                    "prediction_authority": False,
                    "can_execute": False,
                })
    return alerts


def analyze_snapshot_events(
    events: list[dict[str, Any]],
    *,
    generated_at: Any = None,
    now: datetime | None = None,
    max_age_minutes: float = DEFAULT_MAX_AGE_MINUTES,
) -> dict[str, Any]:
    now = now or _aware(generated_at) or datetime.now(timezone.utc)
    provider_states: dict[str, dict[str, int]] = {}
    totals = {FRESH: 0, AGING: 0, STALE: 0, UNAVAILABLE: 0, HISTORICAL: 0}
    for event in events:
        provider, capability = _provider(event)
        provider_bucket = provider_states.setdefault(provider, dict(totals))
        historical = capability == "openers"
        for row in _quote_rows(event):
            quality = classify_freshness(
                row.get("updated_at"), now=now, max_age_minutes=max_age_minutes, historical=historical,
            )
            state = quality["state"]
            totals[state] += 1
            provider_bucket[state] += 1

    alerts = disagreement_alerts(events)
    return {
        "max_age_minutes": max_age_minutes,
        "counts": totals,
        "providers": provider_states,
        "current_rows_usable": totals[FRESH] + totals[AGING],
        "current_rows_stale_or_unknown": totals[STALE] + totals[UNAVAILABLE],
        "historical_opener_rows": totals[HISTORICAL],
        "disagreement_alert_count": len(alerts),
        "disagreement_alerts": alerts,
        "prediction_authority": False,
        "can_execute": False,
    }


__all__ = [
    "AGING", "CAN_EXECUTE", "DEFAULT_MAX_AGE_MINUTES", "FRESH", "HISTORICAL",
    "STALE", "UNAVAILABLE", "american_implied_probability", "analyze_snapshot_events",
    "classify_freshness", "disagreement_alerts", "provider_degradation",
]
