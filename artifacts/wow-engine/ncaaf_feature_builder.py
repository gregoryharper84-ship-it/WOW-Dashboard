"""Deterministic NCAAF training-feature materializer.

Consumes only complete, pregame-qualified normalized evidence. It does not fetch
external data, train models, calibrate probabilities, publish probabilities, or
perform execution.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from ncaaf_evidence_readiness import assess_pregame_evidence

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
FEATURE_SCHEMA_VERSION = "NCAAF_FEATURES_V1"


class NCAAFFeatureBuildUnavailable(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def _parse_ts(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise NCAAFFeatureBuildUnavailable("NCAAF_EVIDENCE_TIMESTAMP_INVALID", "Missing evidence timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NCAAFFeatureBuildUnavailable("NCAAF_EVIDENCE_TIMESTAMP_INVALID", "Malformed evidence timestamp") from exc
    if parsed.utcoffset() is None:
        raise NCAAFFeatureBuildUnavailable("NCAAF_EVIDENCE_TIMESTAMP_INVALID", "Evidence timestamp must be offset-aware")
    return parsed


def _number(payload: Mapping[str, Any], key: str, *, low: float | None = None, high: float | None = None) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NCAAFFeatureBuildUnavailable("NCAAF_EVIDENCE_VALUE_INVALID", f"{key} must be numeric")
    number = float(value)
    if low is not None and number < low:
        raise NCAAFFeatureBuildUnavailable("NCAAF_EVIDENCE_VALUE_INVALID", f"{key} below allowed range")
    if high is not None and number > high:
        raise NCAAFFeatureBuildUnavailable("NCAAF_EVIDENCE_VALUE_INVALID", f"{key} above allowed range")
    return number


def _latest(rows: Iterable[Mapping[str, Any]], kind: str, scope: str) -> Mapping[str, Any]:
    matches = [
        row for row in rows
        if str(row.get("evidence_kind") or "").upper() == kind
        and str(row.get("scope") or "").upper() == scope
    ]
    if not matches:
        raise NCAAFFeatureBuildUnavailable("NCAAF_PREGAME_EVIDENCE_INCOMPLETE", f"Missing {kind}:{scope}")
    return max(matches, key=lambda row: _parse_ts(row.get("evidence_timestamp")))


def _value(rows: list[Mapping[str, Any]], kind: str, scope: str, key: str = "value", *, low: float | None = None, high: float | None = None) -> float:
    row = _latest(rows, kind, scope)
    payload = row.get("payload")
    if not isinstance(payload, Mapping):
        raise NCAAFFeatureBuildUnavailable("NCAAF_EVIDENCE_PAYLOAD_INVALID", f"{kind}:{scope} payload must be an object")
    return _number(payload, key, low=low, high=high)


def build_training_feature_row(
    *,
    training_game_id: str,
    official_event_id: str,
    event_start_time: str,
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    evidence = list(rows)
    readiness = assess_pregame_evidence(
        official_event_id=official_event_id,
        event_start_time=event_start_time,
        rows=evidence,
    )
    if not readiness.ready:
        raise NCAAFFeatureBuildUnavailable(
            "NCAAF_PREGAME_EVIDENCE_INCOMPLETE",
            ",".join(readiness.missing_slots),
        )

    usable = [
        row for row in evidence
        if str(row.get("official_event_id") or "") == official_event_id
        and str(row.get("provenance_grade") or "UNVERIFIED").upper() in {"A", "B", "C"}
        and isinstance(row.get("blocker_codes"), list)
        and not row.get("blocker_codes")
    ]
    feature_as_of = max(_parse_ts(row.get("evidence_timestamp")) for row in usable).isoformat()
    source_manifest = {
        "official_event_id": official_event_id,
        "evidence_ids": sorted(str(row.get("evidence_id") or "") for row in usable if row.get("evidence_id")),
        "payload_sha256": sorted(str(row.get("payload_sha256") or "") for row in usable if row.get("payload_sha256")),
        "provider_set": sorted({str(row.get("source_provider") or "") for row in usable if row.get("source_provider")}),
    }

    weather = _latest(usable, "WEATHER", "EVENT")
    weather_payload = weather.get("payload")
    if not isinstance(weather_payload, Mapping):
        raise NCAAFFeatureBuildUnavailable("NCAAF_EVIDENCE_PAYLOAD_INVALID", "WEATHER payload must be an object")

    away_rest = _latest(usable, "REST_TRAVEL", "AWAY")
    away_rest_payload = away_rest.get("payload")
    if not isinstance(away_rest_payload, Mapping):
        raise NCAAFFeatureBuildUnavailable("NCAAF_EVIDENCE_PAYLOAD_INVALID", "REST_TRAVEL:AWAY payload must be an object")

    row = {
        "training_game_id": training_game_id,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_as_of": feature_as_of,
        "feature_source_manifest": source_manifest,
        "home_power_rating": _value(usable, "TEAM_POWER", "HOME"),
        "away_power_rating": _value(usable, "TEAM_POWER", "AWAY"),
        "home_off_epa": _value(usable, "OFF_EPA", "HOME"),
        "away_off_epa": _value(usable, "OFF_EPA", "AWAY"),
        "home_def_epa": _value(usable, "DEF_EPA", "HOME"),
        "away_def_epa": _value(usable, "DEF_EPA", "AWAY"),
        "home_success_rate": _value(usable, "SUCCESS_RATE", "HOME"),
        "away_success_rate": _value(usable, "SUCCESS_RATE", "AWAY"),
        "home_explosiveness": _value(usable, "EXPLOSIVENESS", "HOME"),
        "away_explosiveness": _value(usable, "EXPLOSIVENESS", "AWAY"),
        "home_qb_value": _value(usable, "QB_VALUE", "HOME"),
        "away_qb_value": _value(usable, "QB_VALUE", "AWAY"),
        "home_qb_certainty": _value(usable, "QB_CERTAINTY", "HOME", low=0.0, high=1.0),
        "away_qb_certainty": _value(usable, "QB_CERTAINTY", "AWAY", low=0.0, high=1.0),
        "home_ol_health": _value(usable, "OL_HEALTH", "HOME", low=0.0, high=1.0),
        "away_ol_health": _value(usable, "OL_HEALTH", "AWAY", low=0.0, high=1.0),
        "home_def_front_health": _value(usable, "DEF_FRONT_HEALTH", "HOME", low=0.0, high=1.0),
        "away_def_front_health": _value(usable, "DEF_FRONT_HEALTH", "AWAY", low=0.0, high=1.0),
        "home_skill_availability": _value(usable, "SKILL_AVAILABILITY", "HOME", low=0.0, high=1.0),
        "away_skill_availability": _value(usable, "SKILL_AVAILABILITY", "AWAY", low=0.0, high=1.0),
        "home_rest_days": _value(usable, "REST_TRAVEL", "HOME", "rest_days", low=0.0),
        "away_rest_days": _value(usable, "REST_TRAVEL", "AWAY", "rest_days", low=0.0),
        "travel_distance_miles": _number(away_rest_payload, "travel_distance_miles", low=0.0),
        "home_tempo": _value(usable, "TEMPO", "HOME"),
        "away_tempo": _value(usable, "TEMPO", "AWAY"),
        "home_turnover_volatility": _value(usable, "TURNOVER_VOLATILITY", "HOME", low=0.0),
        "away_turnover_volatility": _value(usable, "TURNOVER_VOLATILITY", "AWAY", low=0.0),
        "home_special_teams_rating": _value(usable, "SPECIAL_TEAMS", "HOME"),
        "away_special_teams_rating": _value(usable, "SPECIAL_TEAMS", "AWAY"),
        "weather_temperature_f": _number(weather_payload, "temperature_f"),
        "weather_wind_mph": _number(weather_payload, "wind_mph", low=0.0),
        "weather_precip_probability": _number(weather_payload, "precip_probability", low=0.0, high=1.0),
        "market_home_no_vig": None,
        "market_away_no_vig": None,
        "market_timestamp": None,
        "can_execute": False,
    }

    market_rows = [r for r in usable if str(r.get("evidence_kind") or "").upper() == "MARKET_NO_VIG" and str(r.get("scope") or "").upper() == "EVENT"]
    if market_rows:
        market = max(market_rows, key=lambda r: _parse_ts(r.get("evidence_timestamp")))
        payload = market.get("payload")
        if not isinstance(payload, Mapping):
            raise NCAAFFeatureBuildUnavailable("NCAAF_EVIDENCE_PAYLOAD_INVALID", "MARKET_NO_VIG payload must be an object")
        row["market_home_no_vig"] = _number(payload, "home_probability", low=0.0, high=1.0)
        row["market_away_no_vig"] = _number(payload, "away_probability", low=0.0, high=1.0)
        row["market_timestamp"] = market.get("evidence_timestamp")

    return row
