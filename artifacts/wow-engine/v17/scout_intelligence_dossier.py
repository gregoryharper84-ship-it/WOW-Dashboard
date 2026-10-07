"""Structured non-predictive Scout Intelligence V2 dossier.

The dossier organizes observed research evidence before specialist handoff. It
never creates a sporting probability, calibrated bound, qualification label,
stake, or execution authority. Missing/ambiguous evidence remains explicit.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_runtime.scout_research import scrub_authority, validate_non_predictive_output

try:
    from v17.scout_source_policy import evidence_quality, source_requirements, source_rule
except ModuleNotFoundError:
    from scout_source_policy import evidence_quality, source_requirements, source_rule

SCHEMA_VERSION = "wow.v17.scout-intelligence-dossier.v1"
CAN_EXECUTE = False

SOURCE_FAMILY = {
    "LEAGUE_OFFICIAL": "OFFICIAL",
    "TEAM_OFFICIAL": "OFFICIAL",
    "VENUE_OFFICIAL": "VENUE",
    "PRIMARY_BEAT_REPORTER": "PRIMARY_REPORTING",
    "ESTABLISHED_STATS_PROVIDER": "STRUCTURED_STATS",
    "SPORTSBOOK_FEED": "MARKET",
    "WEATHER_PROVIDER": "WEATHER",
    "SECONDARY_MEDIA": "SECONDARY",
    "SOCIAL_UNVERIFIED": "UNVERIFIED",
}

VOLATILE_DOMAINS = frozenset({
    "availability", "personnel", "practice", "starter", "lineup", "bullpen",
    "weather", "rotation", "goalie", "lines", "market",
})
PARTICIPANT_STATE_DOMAINS = frozenset({
    "availability", "personnel", "practice", "starter", "lineup",
    "rotation", "goalie", "lines",
})
SEASONAL_SPORTS = frozenset({
    "americanfootball_nfl", "americanfootball_ncaaf", "baseball_mlb",
    "basketball_nba", "basketball_wnba", "basketball_ncaab", "icehockey_nhl",
})


def _aware(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _age_minutes(value: Any, *, now: datetime) -> float | None:
    parsed = _aware(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds() / 60.0)


def _event_hours(candidate: dict[str, Any], *, now: datetime) -> float | None:
    event = _aware(candidate.get("commence_time"))
    if event is None:
        return None
    return (event - now).total_seconds() / 3600.0


def _dynamic_max_age(domain: str, base: int, hours_to_event: float | None) -> int:
    if domain not in VOLATILE_DOMAINS or hours_to_event is None or hours_to_event > 24:
        return base
    if hours_to_event <= 0.5:
        return min(base, 15)
    if hours_to_event <= 1.5:
        return min(base, 30)
    if hours_to_event <= 6:
        return min(base, 60)
    return min(base, 240)


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _domain_rows(candidate: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    raw = candidate.get("research_domain_evidence")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for domain, value in raw.items():
        key = str(domain or "").strip().lower()
        if key:
            out[key] = _rows(value)
    return out


def _market_rows(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    return _rows(candidate.get("market_evidence"))


def _source_id(row: dict[str, Any], family: str) -> str:
    if family == "MARKET":
        return str(
            row.get("source_provider")
            or row.get("bookmaker")
            or row.get("bookmaker_title")
            or "SPORTSBOOK_MARKET_COMPLEX"
        )
    return str(
        row.get("source_provider")
        or row.get("source_id")
        or row.get("publisher")
        or row.get("source_url")
        or family
    )


def _observed_at(row: dict[str, Any]) -> Any:
    return (
        row.get("captured_at")
        or row.get("effective_at")
        or row.get("market_last_update")
        or row.get("bookmaker_last_update")
        or row.get("source_timestamp")
    )


def _claim_key(domain: str, row: dict[str, Any]) -> str:
    return str(
        row.get("claim_key")
        or row.get("evidence_type")
        or row.get("field")
        or row.get("market_key")
        or domain
    ).strip().upper()


def _claim_value(row: dict[str, Any]) -> Any:
    for key in ("value", "status", "state", "result", "outcome_name", "point"):
        if key in row:
            return row.get(key)
    return None


def _value_hash(value: Any) -> str | None:
    if value is None:
        return None
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _confirmed(row: dict[str, Any], source_class: str) -> bool:
    if "confirmed" in row:
        return row.get("confirmed") is True
    return source_class in {"LEAGUE_OFFICIAL", "TEAM_OFFICIAL", "VENUE_OFFICIAL", "ESTABLISHED_STATS_PROVIDER", "SPORTSBOOK_FEED", "WEATHER_PROVIDER"}


def _ledger_entry(
    domain: str,
    row: dict[str, Any],
    *,
    allowed_classes: set[str],
    now: datetime,
    hours_to_event: float | None,
    default_source_class: str,
) -> dict[str, Any]:
    source_class = str(row.get("source_class") or default_source_class).upper()
    family = SOURCE_FAMILY.get(source_class, "UNVERIFIED")
    observed_at = _observed_at(row)
    age = _age_minutes(observed_at, now=now)
    confirmed = _confirmed(row, source_class)
    evidence_type = str(row.get("evidence_type") or _claim_key(domain, row))
    base = evidence_quality(
        source_class,
        age_minutes=age,
        confirmed=confirmed,
        evidence_type=evidence_type,
    )
    dynamic_max = _dynamic_max_age(domain, int(base["max_age_minutes"]), hours_to_event)
    dynamic_stale = age is None or age > dynamic_max
    source_allowed = not allowed_classes or source_class in allowed_classes
    usable = bool(base["research_usable"] and source_allowed and not dynamic_stale)
    claim = _claim_key(domain, row)
    value = scrub_authority(_claim_value(row))
    source_id = _source_id(row, family)
    claim_id = hashlib.sha256(
        f"{domain}|{claim}|{family}|{source_id}|{observed_at}".encode()
    ).hexdigest()[:24]
    return {
        "claim_id": claim_id,
        "domain": domain,
        "claim_key": claim,
        "observed_value": value,
        "value_hash": _value_hash(value),
        "source_class": source_class,
        "source_family": family,
        "source_id": source_id,
        "observed_at": observed_at,
        "age_minutes": round(age, 2) if age is not None else None,
        "base_max_age_minutes": int(base["max_age_minutes"]),
        "dynamic_max_age_minutes": dynamic_max,
        "source_allowed_for_domain": source_allowed,
        "confirmation_required": bool(base["confirmation_required"]),
        "confirmation_ok": bool(base["confirmation_ok"]),
        "dynamic_stale": dynamic_stale,
        "research_usable": usable,
        "prediction_authority": False,
        "can_execute": False,
    }


def _event_context(candidate: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "official_event_id", "sport_key", "sport_title", "commence_time",
        "home_team", "away_team", "season_year", "season_type", "season_slug",
        "season_phase", "season_phase_source", "game_type", "event_type",
        "competition_round", "tournament_round", "series_state",
        "neutral_site", "venue", "surface", "competition_importance",
    )
    context = {field: candidate.get(field) for field in fields if candidate.get(field) is not None}
    phase = str(context.get("season_phase") or "").upper()
    context["regime_certainty"] = (
        "CONFIRMED"
        if phase and phase != "UNKNOWN"
        else ("UNRESOLVED" if str(candidate.get("sport_key") or "") in SEASONAL_SPORTS else "NOT_APPLICABLE")
    )
    return context


def _market_state(entries: list[dict[str, Any]], raw_rows: list[dict[str, Any]]) -> dict[str, Any]:
    books = {
        str(row.get("bookmaker") or row.get("bookmaker_title") or row.get("source_provider"))
        for row in raw_rows
        if row.get("bookmaker") or row.get("bookmaker_title") or row.get("source_provider")
    }
    market_keys = sorted({str(row.get("market_key")) for row in raw_rows if row.get("market_key")})
    grouped: dict[tuple[str, str, str], list[float]] = {}
    for row in raw_rows:
        price = row.get("price")
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            continue
        key = (
            str(row.get("market_key") or ""),
            str(row.get("outcome_name") or ""),
            str(row.get("point") or ""),
        )
        grouped.setdefault(key, []).append(float(price))
    anomalies = []
    for key, prices in grouped.items():
        if len(prices) >= 2 and max(prices) - min(prices) >= 40:
            anomalies.append({
                "code": "MARKET_PRICE_DISPERSION",
                "market_key": key[0],
                "outcome_name": key[1],
                "point": key[2] or None,
                "american_price_range": round(max(prices) - min(prices), 2),
                "research_only": True,
            })
    return {
        "observation_count": len(raw_rows),
        "bookmaker_count": len(books),
        "bookmakers": sorted(books),
        "market_keys": market_keys,
        "source_family_count": 1 if raw_rows else 0,
        "stale_observation_count": sum(1 for entry in entries if entry.get("dynamic_stale")),
        "anomalies": anomalies,
        "market_activity_modifies_sporting_probability": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def _material_conflicts(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect contemporaneous contradictions without treating updates as conflicts."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        if not entry.get("value_hash") or not entry.get("research_usable"):
            continue
        key = (str(entry.get("domain")), str(entry.get("claim_key")))
        grouped.setdefault(key, []).append(entry)

    conflicts: list[dict[str, Any]] = []
    for (domain, claim), rows in sorted(grouped.items()):
        parsed = [(_aware(row.get("observed_at")), row) for row in rows]
        dated = [(dt, row) for dt, row in parsed if dt is not None]
        if dated:
            latest = max(dt for dt, _row in dated)
            active = [
                row for dt, row in dated
                if (latest - dt).total_seconds() <= 15 * 60
            ]
        else:
            active = rows
        values = {str(row.get("value_hash")) for row in active if row.get("value_hash")}
        if len(values) > 1:
            conflicts.append({
                "code": "MATERIAL_DOMAIN_CONFLICT",
                "domain": domain,
                "claim_key": claim,
                "distinct_value_count": len(values),
                "comparison_window_minutes": 15,
            })
    return conflicts


def _refresh_plan(candidate: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    event = _aware(candidate.get("commence_time"))
    if event is None:
        return {
            "status": "BLOCKED_EVENT_TIME_INVALID",
            "next_refresh_at": None,
            "checkpoint": None,
            "triggered_rescan_reasons": ["EVENT_TIME_INVALID"],
        }
    hours = (event - now).total_seconds() / 3600.0
    checkpoints = [
        ("T_MINUS_24H", timedelta(hours=24)),
        ("T_MINUS_6H", timedelta(hours=6)),
        ("T_MINUS_90M", timedelta(minutes=90)),
        ("T_MINUS_30M", timedelta(minutes=30)),
    ]
    if hours <= 0:
        return {"status": "EVENT_STARTED", "next_refresh_at": None, "checkpoint": "CLOSED", "triggered_rescan_reasons": []}
    if hours <= 0.5:
        return {
            "status": "FINAL_REFRESH_DUE",
            "next_refresh_at": now.isoformat(),
            "checkpoint": "T_MINUS_30M",
            "triggered_rescan_reasons": ["FINAL_PREGAME_REFRESH"],
        }
    future = [(name, event - delta) for name, delta in checkpoints if event - delta > now]
    if future:
        name, when = min(future, key=lambda item: item[1])
    else:
        name, when = "T_MINUS_30M", event - timedelta(minutes=30)
    return {
        "status": "SCHEDULED",
        "next_refresh_at": when.astimezone(timezone.utc).isoformat(),
        "checkpoint": name,
        "triggered_rescan_reasons": [
            "PARTICIPANT_STATUS_CHANGE",
            "LINEUP_OR_ROTATION_CHANGE",
            "GOALIE_OR_STARTER_CHANGE",
            "WEATHER_OR_VENUE_CHANGE",
            "MATERIAL_MARKET_ANOMALY",
        ],
    }


def _change_detection(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(previous, dict):
        return {"status": "INITIAL_SNAPSHOT", "changed_sections": []}
    changed = []
    for section in ("event_context", "participant_state_timeline", "required_domain_coverage", "market_state"):
        if previous.get(section) != current.get(section):
            changed.append(section)
    return {
        "status": "CHANGED" if changed else "UNCHANGED",
        "changed_sections": changed,
    }


def build_scout_dossier(
    candidate: dict[str, Any],
    *,
    now: datetime | None = None,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sport_key = str(candidate.get("sport_key") or "")
    requirements = source_requirements(sport_key)
    hours_to_event = _event_hours(candidate, now=now)
    domain_rows = _domain_rows(candidate)

    ledger: list[dict[str, Any]] = []
    for domain, allowed in requirements.items():
        if domain == "market":
            continue
        allowed_classes = {str(value).upper() for value in allowed}
        for row in domain_rows.get(domain, []):
            ledger.append(_ledger_entry(
                domain,
                row,
                allowed_classes=allowed_classes,
                now=now,
                hours_to_event=hours_to_event,
                default_source_class="SOCIAL_UNVERIFIED",
            ))

    market_rows = _market_rows(candidate)
    market_allowed = {str(value).upper() for value in requirements.get("market", [])}
    market_entries = [
        _ledger_entry(
            "market",
            row,
            allowed_classes=market_allowed,
            now=now,
            hours_to_event=hours_to_event,
            default_source_class="SPORTSBOOK_FEED",
        )
        for row in market_rows
    ]
    ledger.extend(market_entries)

    coverage = {
        domain: any(entry["domain"] == domain and entry["research_usable"] for entry in ledger)
        for domain in requirements
    }
    missing = [domain for domain, covered in coverage.items() if not covered]
    participant_timeline = sorted(
        [
            {
                "domain": entry["domain"],
                "claim_key": entry["claim_key"],
                "observed_at": entry["observed_at"],
                "source_class": entry["source_class"],
                "source_id": entry["source_id"],
                "confirmation_ok": entry["confirmation_ok"],
                "research_usable": entry["research_usable"],
            }
            for entry in ledger
            if entry["domain"] in PARTICIPANT_STATE_DOMAINS
        ],
        key=lambda row: str(row.get("observed_at") or ""),
    )
    families: dict[str, dict[str, Any]] = {}
    for entry in ledger:
        family = str(entry["source_family"])
        node = families.setdefault(family, {"source_ids": set(), "domains": set()})
        node["source_ids"].add(str(entry["source_id"]))
        node["domains"].add(str(entry["domain"]))
    independence_graph = {
        family: {
            "source_ids": sorted(node["source_ids"]),
            "domains": sorted(node["domains"]),
            "independent_family": family != "MARKET",
        }
        for family, node in sorted(families.items())
    }

    context = _event_context(candidate)
    conflicts = _material_conflicts(ledger)
    uncertainties = [
        {"code": "MISSING_REQUIRED_DOMAIN", "domain": domain}
        for domain in missing
    ]
    if context.get("regime_certainty") == "UNRESOLVED":
        uncertainties.append({"code": "REGIME_CONTEXT_UNRESOLVED", "domain": "event_context"})
    missing_timestamps = sorted({
        entry["domain"] for entry in ledger if entry.get("observed_at") in (None, "")
    })
    uncertainties.extend(
        {"code": "SOURCE_TIMESTAMP_MISSING", "domain": domain}
        for domain in missing_timestamps
    )

    dossier: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "event_context": context,
        "participant_state_timeline": participant_timeline,
        "evidence_ledger": ledger,
        "source_independence_graph": independence_graph,
        "required_domains": list(requirements.keys()),
        "required_domain_coverage": coverage,
        "missing_required_domains": missing,
        "domain_completeness": round(
            100.0 * (len(requirements) - len(missing)) / len(requirements), 2
        ) if requirements else 0.0,
        "market_state": _market_state(market_entries, market_rows),
        "contradictions": conflicts,
        "red_team_results": {
            "status": "QUARANTINED" if conflicts else ("WATCH" if missing else "PASSED"),
            "material_conflicts": conflicts,
            "missing_required_domains": missing,
        },
        "uncertainty_ledger": uncertainties,
        "refresh_plan": _refresh_plan(candidate, now=now),
        "prediction_authority": False,
        "final_probability_requires_controlling_specialist": True,
        "can_execute": False,
    }
    dossier["change_detection"] = _change_detection(dossier, previous)
    violations = validate_non_predictive_output(dossier)
    if violations:
        raise ValueError("SCOUT_DOSSIER_AUTHORITY_VIOLATION:" + ",".join(violations))

    stable = {
        key: value
        for key, value in dossier.items()
        if key not in {"generated_at", "dossier_hash", "change_detection", "refresh_plan"}
    }
    dossier["dossier_hash"] = hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return dossier


__all__ = ["SCHEMA_VERSION", "build_scout_dossier"]
