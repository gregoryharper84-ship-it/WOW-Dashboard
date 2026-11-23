"""Two-way Kalshi slip *research* exchange for LLP and WOW Betting Engine.

Append-only evidence exchange; no model training, probability publication,
slip approval, capital sizing, orders, or trading. Every host label is a claim:
the shared Action bearer authenticates the caller, NOT the distinct GPT host.
Requires explicit feature-flag installation and the independent database migration.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Any, Callable, Mapping

from fastapi import Depends, FastAPI, HTTPException, Query

SCHEMA = "WOW_V17_KALSHI_SLIP_RESEARCH_EXCHANGE_V1"
MAX_SERVER_CLOCK_SKEW_SECONDS = 300
TABLE = "wow_kalshi_slip_research_exchange"
ROUTE = "/v17/intelligence/kalshi-slip-exchange"
CAN_EXECUTE = False
CAN_ALLOCATE_CAPITAL = False
PROBABILITY_PUBLISHABLE = False
HOSTS = frozenset({"LLP_TEAM_BETTING_ENGINE", "WOW_BETTING_ENGINE"})
SPORTS = frozenset({"NFL", "NBA", "NHL", "NCAAF", "NCAAB", "MLB", "WNBA", "SOCCER", "TENNIS", "PGA", "MMA", "BOXING", "CRICKET", "MIXED"})
FINDING_TYPES = frozenset({
    "SCHEDULE_REST_TRAVEL", "LINEUP_AVAILABILITY", "GAME_SCRIPT",
    "SPORT_SPECIALIST_DIAGNOSTIC", "DEPENDENCY_CORRELATION", "QUOTE_MARKET_SANITY",
    "SLIP_FRAGILITY", "SETTLEMENT_DIAGNOSTIC", "CALIBRATION_DIAGNOSTIC",
    "RESEARCH_HYPOTHESIS",
})
STAGES = frozenset({"PREGAME_RESEARCH", "POSTMORTEM_RESEARCH"})
REQUIRED = frozenset({
    "source_host", "scope", "sport", "reference_id", "finding_type", "finding_code",
    "analysis_stage", "as_of_utc", "source_observed_at_utc", "evidence_refs",
})
OPTIONAL = frozenset({"market_ref", "event_start_utc"})
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-/]{0,127}$")
CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,95}$")


class ExchangeHold(ValueError):
    """Typed, fail-closed research validation error."""


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ExchangeHold(label + "_INVALID")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExchangeHold(label + "_INVALID") from exc
    if dt.utcoffset() is None:
        raise ExchangeHold(label + "_INVALID")
    return dt.astimezone(timezone.utc)


def _token(value: Any, field: str) -> str:
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise ExchangeHold(field + "_INVALID")
    return value


def _host(value: Any) -> str:
    if not isinstance(value, str) or value not in HOSTS:
        raise ExchangeHold("EXCHANGE_HOST_UNSUPPORTED")
    return value


def normalize_research_observation(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Allow only strictly typed non-probability metadata. No PII/order data.

    Caller-supplied evidence references are NOT treated as verified receipt
    linkage. Any future ingest into model features requires a separate trusted
    server-side source reconciliation and independent certification.
    """
    if not isinstance(raw, Mapping) or set(raw) - (REQUIRED | OPTIONAL) or REQUIRED - set(raw):
        raise ExchangeHold("EXCHANGE_SCHEMA_OR_FORBIDDEN_FIELD")
    source_host = _host(raw["source_host"])
    scope = raw["scope"]
    if scope not in ("EVENT", "COHORT"):
        raise ExchangeHold("EXCHANGE_SCOPE_INVALID")
    sport = raw["sport"]
    if not isinstance(sport, str) or sport not in SPORTS or (scope == "EVENT" and sport == "MIXED"):
        raise ExchangeHold("EXCHANGE_SPORT_INVALID")
    reference_id = _token(raw["reference_id"], "REFERENCE_ID")
    market_ref = raw.get("market_ref")
    if market_ref is not None:
        market_ref = _token(market_ref, "MARKET_REF")
    finding_type = raw["finding_type"]
    if not isinstance(finding_type, str) or finding_type not in FINDING_TYPES:
        raise ExchangeHold("EXCHANGE_FINDING_TYPE_INVALID")
    finding_code = raw["finding_code"]
    if not isinstance(finding_code, str) or not CODE.fullmatch(finding_code):
        raise ExchangeHold("EXCHANGE_FINDING_CODE_INVALID")
    stage = raw["analysis_stage"]
    if not isinstance(stage, str) or stage not in STAGES:
        raise ExchangeHold("EXCHANGE_STAGE_INVALID")
    as_of = _utc(raw["as_of_utc"], "AS_OF")
    observed = _utc(raw["source_observed_at_utc"], "OBSERVED_AT")
    if observed > as_of:
        raise ExchangeHold("EXCHANGE_CHRONOLOGY_INVALID")
    # Client timestamps are claims; never accept source observations or an
    # analysis timestamp beyond the real server clock plus a small skew.
    server_now = datetime.now(timezone.utc)
    if observed > server_now + timedelta(seconds=MAX_SERVER_CLOCK_SKEW_SECONDS) or as_of > server_now + timedelta(seconds=MAX_SERVER_CLOCK_SKEW_SECONDS):
        raise ExchangeHold("EXCHANGE_FUTURE_TIMESTAMP_INVALID")
    start_raw = raw.get("event_start_utc")
    if scope == "EVENT" and start_raw is None:
        raise ExchangeHold("EXCHANGE_EVENT_START_MISSING")
    if scope == "COHORT" and (start_raw is not None or market_ref is not None):
        raise ExchangeHold("EXCHANGE_COHORT_CANNOT_IMPERSONATE_EVENT")
    start = _utc(start_raw, "EVENT_START") if start_raw is not None else None
    if stage == "PREGAME_RESEARCH" and (start is None or not as_of < start):
        raise ExchangeHold("EXCHANGE_PREGAME_CUTOFF_INVALID")
    if stage == "POSTMORTEM_RESEARCH" and start is not None and as_of < start:
        raise ExchangeHold("EXCHANGE_POSTMORTEM_BEFORE_START")
    refs = raw["evidence_refs"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= 12 or not all(isinstance(x, str) and TOKEN.fullmatch(x) for x in refs):
        raise ExchangeHold("EXCHANGE_EVIDENCE_REFS_INVALID")
    if len(refs) != len(set(refs)):
        raise ExchangeHold("EXCHANGE_DUPLICATE_EVIDENCE_REF")

    canonical = {
        "schema_version": SCHEMA,
        "source_host": source_host, "scope": scope, "sport": sport,
        "reference_id": reference_id, "market_ref": market_ref,
        "finding_type": finding_type, "finding_code": finding_code,
        "analysis_stage": stage,
        "as_of_utc": as_of.isoformat(),
        "source_observed_at_utc": observed.isoformat(),
        "event_start_utc": start.isoformat() if start else None,
        "evidence_refs": sorted(refs),
    }
    record_id = sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "record_id": record_id, **canonical,
        "evidence_status": "UNVERIFIED_RESEARCH",
        "source_host_attested": False,
        "model_feature_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def publish_research(db: Any, raw: Mapping[str, Any]) -> dict[str, Any]:
    row = normalize_research_observation(raw)
    # True append-only/idempotent semantic: ON CONFLICT DO NOTHING. Never
    # overwrite an existing record, even if a retried caller is compromised.
    db.table(TABLE).upsert(row, on_conflict="record_id", ignore_duplicates=True).execute()
    return {
        "schema_version": SCHEMA, "status": "RESEARCH_RECEIPT_PERSISTED",
        "record_id": row["record_id"], "claimed_source_host": row["source_host"],
        "source_host_attested": False, "model_feature_eligible": False,
        "probability_publishable": False, "can_execute": False,
    }


def read_research(
    db: Any, *, consumer_host: str, scope: str | None = None,
    reference_id: str | None = None, limit: int = 50,
) -> dict[str, Any]:
    _host(consumer_host)
    if scope is not None and scope not in ("EVENT", "COHORT"):
        raise ExchangeHold("EXCHANGE_SCOPE_INVALID")
    if reference_id is not None:
        _token(reference_id, "REFERENCE_ID")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ExchangeHold("EXCHANGE_LIMIT_INVALID")
    # Query is intentionally bounded. It reads the shared source for *both*
    # intelligence engines; consumer identity is only a claim under shared auth.
    query = db.table(TABLE).select(
        "record_id,schema_version,source_host,scope,sport,reference_id,market_ref,"
        "finding_type,finding_code,analysis_stage,as_of_utc,source_observed_at_utc,"
        "event_start_utc,evidence_refs,evidence_status,source_host_attested,"
        "model_feature_eligible,probability_publishable,can_execute,created_at"
    )
    if scope is not None:
        query = query.eq("scope", scope)
    if reference_id is not None:
        query = query.eq("reference_id", reference_id)
    rows = query.order("created_at", desc=True).limit(limit).execute().data or []
    # Do not echo unknown database fields. The remote table is not a prediction authority.
    allowed = {"record_id", "schema_version", "source_host", "scope", "sport", "reference_id", "market_ref", "finding_type", "finding_code", "analysis_stage", "as_of_utc", "source_observed_at_utc", "event_start_utc", "evidence_refs", "created_at"}
    clean_rows = [{k: row.get(k) for k in allowed} | {
        "source_host_attested": False, "evidence_status": "UNVERIFIED_RESEARCH",
        "model_feature_eligible": False, "probability_publishable": False,
        "can_execute": False,
    } for row in rows]
    return {
        "schema_version": SCHEMA, "status": "RESEARCH_READ_ONLY",
        "claimed_consumer_host": consumer_host, "consumer_host_attested": False,
        "entries": clean_rows, "entry_count": len(clean_rows),
        "model_feature_eligible": False, "probability_publishable": False,
        "can_execute": False,
    }


def install_kalshi_slip_research_routes(
    app: FastAPI, *, auth_dependency: Callable[..., Any], get_client_fn: Callable[[], Any],
) -> bool:
    """Opt-in route on existing authenticated V17 app. No app-wide mutations."""
    if getattr(app.state, "v17_kalshi_research_exchange_installed", False):
        return False
    if any(getattr(route, "path", None) == ROUTE for route in app.router.routes):
        return False
    dependencies = [Depends(auth_dependency)]

    @app.post(ROUTE, dependencies=dependencies, status_code=202,
              operation_id="publishWowV17KalshiSlipResearch")
    def publish(payload: dict[str, Any]):
        try:
            return publish_research(get_client_fn(), payload)
        except ExchangeHold as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc), "can_execute": False}) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail={"code": "EXCHANGE_PERSISTENCE_UNAVAILABLE", "can_execute": False}) from exc

    @app.get(ROUTE, dependencies=dependencies, operation_id="readWowV17KalshiSlipResearch")
    def read(consumer_host: str, scope: str | None = None, reference_id: str | None = None,
             limit: int = Query(50, ge=1, le=100)):
        try:
            return read_research(get_client_fn(), consumer_host=consumer_host,
                                 scope=scope, reference_id=reference_id, limit=limit)
        except ExchangeHold as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc), "can_execute": False}) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail={"code": "EXCHANGE_PERSISTENCE_UNAVAILABLE", "can_execute": False}) from exc

    app.state.v17_kalshi_research_exchange_installed = True
    return True
