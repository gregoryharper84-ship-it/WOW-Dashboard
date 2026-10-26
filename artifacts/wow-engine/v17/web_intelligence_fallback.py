"""Multi-sport V17 web-intelligence *research* fallback; never a probability model.

Consumes normalized Scout/Research evidence; deliberately does not search the web
or trust supplied URLs as proof of what a page says. Upstream acquisition remains
responsible for truthful source capture. No V17 terminal reducer input is created.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping
from urllib.parse import urlsplit

from v17.scout_source_policy import COMMON_SOURCE_RULES, evidence_quality, source_rule
from v17.web_intelligence_sport_profiles import MARKETS, NO_LINE_MARKETS, normalize_sport, weights_for

CAN_EXECUTE = False
RESEARCH_CEILING = "RESEARCH_INTEREST"
WEIGHTS = weights_for("NBA")  # Compatibility with existing NBA research v1
# Research heuristics, NOT fitted coefficients or calibration parameters.
TIER_QUALITY = {
    "TIER_1_OFFICIAL": 1.0,
    "TIER_2_PRIMARY_REPORTER": 0.80,
    "TIER_3_ESTABLISHED_DATA": 0.90,
    "TIER_4_SECONDARY": 0.50,
    "TIER_5_UNVERIFIED": 0.0,
}
MODEL_FAILURES = frozenset({
    "MODEL_UNAVAILABLE", "MODEL_ROUTE_UNSUPPORTED", "MODEL_INPUTS_INSUFFICIENT",
    "MODEL_SCORER_FAILED", "MODEL_OUTPUT_INVALID", "LIVE_GPT_ACTION_INVOCATION_BLOCKED",
})
_MARKET_CLASSES = frozenset({"SPORTSBOOK_FEED"})


def _time(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError, OverflowError):
        return None
    return dt.astimezone(timezone.utc) if dt.utcoffset() is not None else None


def _https(url: Any) -> bool:
    try:
        parts = urlsplit(str(url))
        return parts.scheme == "https" and bool(parts.hostname) and not parts.username and not parts.password
    except ValueError:
        return False


def _odds_break_even(odds: Any) -> float | None:
    if isinstance(odds, bool) or not isinstance(odds, (int, float)) or not isfinite(odds):
        return None
    if odds >= 100:
        return 100 / (odds + 100)
    if odds <= -100:
        return -odds / (100 - odds)
    return None


def _market_audit(req: Mapping[str, Any], now: datetime) -> list[dict[str, Any]]:
    records = []
    for item in req.get("market_quotes") or []:
        timestamp = _time(item.get("observed_at"))
        match = (
            str(item.get("event_id")) == str(req.get("official_event_id"))
            and str(item.get("market_type")).upper() == str(req.get("market_type")).upper()
            and str(item.get("selection")).casefold() == str(req.get("selection")).casefold()
            and item.get("line") == req.get("line")
        )
        age_ok = timestamp is not None and 0 <= (now - timestamp).total_seconds() <= 900
        price = _odds_break_even(item.get("american_odds"))
        accepted = match and age_ok and price is not None
        records.append({
            "market_identity_match": match,
            "quote_fresh": age_ok,
            "price_mathematically_valid": price is not None,
            "quote_status": "EXACT_SNAPSHOT_STRUCTURAL_MATCH" if accepted else "MARKET_QUOTE_UNVERIFIED",
            "break_even_probability": round(price, 6) if accepted else None,
            "book": item.get("book"),
            "observed_at": item.get("observed_at"),
            "independent_market_verification": False,
        })
    return records


def research_fallback(req: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc))
    if now.utcoffset() is None:
        raise ValueError("now must have timezone information")
    now = now.astimezone(timezone.utc)
    model_status = str(req.get("model_status") or "")
    base: dict[str, Any] = {
        "schema_version": "WOW_WEB_INTELLIGENCE_FALLBACK_V1",
        "lane": "RESEARCH_ONLY",
        "source_validation": "STRUCTURAL_AND_FRESHNESS_ONLY_NOT_INDEPENDENTLY_FETCH_VERIFIED",
        "sport": normalize_sport(req.get("sport")),
        "official_event_id": req.get("official_event_id"),
        "selection": req.get("selection"),
        "market_type": req.get("market_type"),
        "line": req.get("line"),
        "upstream_model_status": model_status,
        "research_ceiling": RESEARCH_CEILING,
        "status": "HOLD",
        "research_score": None,
        "evidence_quality_factor": None,
        "factor_scores": {},
        "factor_weights": dict(weights_for(req.get("sport")) or {}),
        "research_profile": "SPORT_SPECIFIC_UNFITTED_HEURISTIC_V1",
        "accepted_evidence": [],
        "rejected_evidence": [],
        "market_audit": _market_audit(req, now),
        "model_probability": None,
        "raw_model_probability": None,
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "rank_eligible": False,
        "probability_publishable": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "terminal_label": None,
        "can_execute": False,
    }

    def finish(code: str) -> dict[str, Any]:
        base["status"] = code
        return base

    if model_status not in MODEL_FAILURES:
        return finish("FALLBACK_NOT_APPLICABLE")
    weights = weights_for(base["sport"])
    if weights is None:
        return finish("RESEARCH_PROFILE_UNSUPPORTED")
    start = _time(req.get("event_start_time_utc"))
    if not req.get("official_event_id") or not req.get("selection") or not req.get("market_type"):
        return finish("EVENT_OR_SELECTION_IDENTITY_MISSING")
    if req.get("market_type") not in MARKETS[base["sport"]]:
        return finish("EXACT_MARKET_UNSUPPORTED")
    if req.get("market_type") in NO_LINE_MARKETS and req.get("line") is not None:
        return finish("EXACT_MARKET_IDENTITY_INVALID")
    if req.get("market_type") not in NO_LINE_MARKETS and (
        isinstance(req.get("line"), bool)
        or not isinstance(req.get("line"), (int, float))
        or not isfinite(req["line"])
    ):
        return finish("EXACT_MARKET_IDENTITY_INVALID")
    if start is None or start <= now:
        return finish("EVENT_NOT_PREGAME")

    # One latest observation per independent source group and factor.
    independent: dict[tuple[str, str], dict[str, Any]] = {}
    for i, item in enumerate(req.get("evidence") or []):
        reason = None
        factor = str(item.get("factor") or "").upper()
        source_class = str(item.get("source_class") or "").upper()
        source_group = str(item.get("source_group") or "").strip().lower()
        observed = _time(item.get("observed_at"))
        value = item.get("favorability")
        if factor not in weights:
            reason = "FACTOR_UNSUPPORTED"
        elif source_class not in COMMON_SOURCE_RULES or source_class in _MARKET_CLASSES:
            reason = "SPORTING_SOURCE_UNSUPPORTED"
        elif not source_group or not _https(item.get("url")) or not str(item.get("claim") or "").strip():
            reason = "SOURCE_PROVENANCE_INCOMPLETE"
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not 0 <= value <= 1:
            reason = "RESEARCH_VALUE_INVALID"
        elif observed is None or observed > now or observed >= start:
            reason = "SOURCE_TIMESTAMP_INVALID"
        else:
            age_minutes = (now - observed).total_seconds() / 60
            quality = evidence_quality(source_class, age_minutes=age_minutes,
                                       confirmed=bool(item.get("confirmed", False)),
                                       evidence_type=item.get("evidence_type"))
            if not quality["research_usable"]:
                reason = "SOURCE_STALE_OR_UNCONFIRMED"
        if reason:
            base["rejected_evidence"].append({"index": i, "reason": reason})
            continue
        record = {
            "index": i, "factor": factor, "source_group": source_group,
            "source_class": source_class, "url": item["url"],
            "claim": item["claim"], "observed_at": item["observed_at"],
            "favorability": float(value),
            "quality": TIER_QUALITY[source_rule(source_class).trust_tier],
            "datetime": observed,
        }
        key = (factor, source_group)
        if key not in independent or record["datetime"] > independent[key]["datetime"]:
            independent[key] = record

    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in weights}
    for record in independent.values():
        grouped[record["factor"]].append(record)
        base["accepted_evidence"].append({
            key: record[key] for key in ("factor", "source_group", "source_class", "url", "claim", "observed_at")
        })
    missing = [factor for factor, records in grouped.items() if not records]
    base["missing_factors"] = missing
    if missing:
        return finish("INSUFFICIENT_RESEARCH_EVIDENCE")
    if any(max(r["favorability"] for r in records) - min(r["favorability"] for r in records) >= 0.35
           for records in grouped.values()):
        return finish("RESEARCH_EVIDENCE_CONFLICT")

    for factor, records in grouped.items():
        base["factor_scores"][factor] = round(
            sum(r["favorability"] for r in records) / len(records), 6
        )
    groups = {record["source_group"] for record in independent.values()}
    mean_source_quality = sum(r["quality"] for r in independent.values()) / len(independent)
    q = min(1.0, len(groups) / 5.0) * mean_source_quality
    directional = sum(weights[k] * (base["factor_scores"][k] - 0.5) for k in weights)
    base["evidence_quality_factor"] = round(q, 6)
    base["research_score"] = round(max(0.0, min(100.0, 50 + 100 * q * directional)), 2)
    return finish("RESEARCH_INTEREST")
