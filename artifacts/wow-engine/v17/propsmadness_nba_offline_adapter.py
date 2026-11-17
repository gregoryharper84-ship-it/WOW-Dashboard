"""PropsMadness NBA offline contract, WOW #2018.

No vendor endpoints, credentials, network, DB writes, source entitlement,
governed model inference, probability publication or wagering execution.
Accepts ONLY synthetic test fixtures. Real data remains forbidden until an
independently documented vendor authorization and a separate approved adapter.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Mapping

PROVIDER = "PROPSMADNESS"
SPORT = "NBA"
FIXTURE_ORIGIN = "SYNTHETIC_TEST_DATA"
SCHEMA = "WOW_PROPSMADNESS_NBA_OFFLINE_FIXTURE_V1"
ALLOWED_STATS = frozenset({"POINTS", "REBOUNDS", "ASSISTS", "THREE_POINTERS_MADE"})
ALLOWED_TYPES = frozenset({"MARKET_DISCOVERY", "PREGAME_CONTEXT", "FINAL_OUTCOME"})
ALLOWED_CONTEXT = frozenset({
    "USG_PCT", "MINUTES", "FGA", "THREE_POINT_ATTEMPTS",
    "OPP_DEF_RTG", "REBOUND_CHANCES", "POTENTIAL_ASSISTS",
})
FORBIDDEN_AUTHORITY_FIELDS = frozenset({
    "model_probability", "calibrated_probability",
    "calibrated_lower_bound", "win_probability", "implied_probability",
    "approved_pick", "publication_allowed", "can_execute",
})
CAN_EXECUTE = False


class PropsMadnessFixtureHold(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PropsMadnessFixtureHold(code)


def _text(value: Any, code: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), code)
    return value.strip()


def _ts(value: Any, code: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise PropsMadnessFixtureHold(code) from exc
    _require(dt.utcoffset() is not None, code)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _number(value: Any, code: str, *, low: float = 0.0, high: float = 1000.0) -> float:
    _require(not isinstance(value, bool), code)
    try:
        n = float(value)
    except (TypeError, ValueError) as exc:
        raise PropsMadnessFixtureHold(code) from exc
    _require(math.isfinite(n) and low <= n <= high, code)
    return n


def _no_authority_fields(item: Mapping[str, Any]) -> None:
    _require(not FORBIDDEN_AUTHORITY_FIELDS.intersection(item), "PM_MODEL_AUTHORITY_FIELD_FORBIDDEN")
    _require(not any(k in item for k in ("api_key", "access_token", "cookie", "session")),
             "PM_CREDENTIAL_FIELD_FORBIDDEN")


def capability_receipt() -> dict[str, Any]:
    """Describes actual supported acquisition, not hypothetical service APIs."""
    return {
        "provider": PROVIDER, "sport": SPORT, "status": "NO_AUTHORIZED_VENDOR_FEED",
        "public_api": False, "vendor_permission": "UNVERIFIED",
        "transport": "SYNTHETIC_OFFLINE_FIXTURE_ONLY",
        "discovery_contract_designed": True,
        "research_context_contract_designed": True,
        "live_fetch_supported": False, "history_storage_authorized": False,
        "training_authorized": False, "prediction_authority": False,
        "can_execute": False,
    }


def normalize_synthetic_fixture(
    payload: bytes,
    *,
    player_crosswalk: Mapping[str, str],
    event_crosswalk: Mapping[str, str],
) -> dict[str, Any]:
    """Validate artificial fixture bytes; NEVER interpret as vendor authorization.

    Crosswalks must be supplied for tests; these are not authenticated mappings
    and do not grant eligibility in downstream sporting/model systems.
    """
    _require(isinstance(payload, bytes) and 0 < len(payload) <= 1_000_000,
             "PM_FIXTURE_SIZE_INVALID")
    try:
        root = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise PropsMadnessFixtureHold("PM_FIXTURE_JSON_INVALID") from exc
    _require(isinstance(root, dict), "PM_FIXTURE_SCHEMA_INVALID")
    _no_authority_fields(root)
    _require(root.get("schema") == SCHEMA, "PM_FIXTURE_SCHEMA_INVALID")
    _require(root.get("origin") == FIXTURE_ORIGIN, "PM_REAL_VENDOR_DATA_NOT_AUTHORIZED")
    _require(root.get("provider") == PROVIDER and root.get("sport") == SPORT,
             "PM_WRONG_PROVIDER_OR_SPORT")
    rows = root.get("rows")
    _require(isinstance(rows, list) and 0 < len(rows) <= 500,
             "PM_FIXTURE_ROWS_INVALID")
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    theses: set[tuple[Any, ...]] = set()
    for item in rows:
        _require(isinstance(item, dict), "PM_RECORD_SCHEMA_INVALID")
        _no_authority_fields(item)
        record_id = _text(item.get("provider_record_id"), "PM_RECORD_ID_MISSING")
        _require(record_id not in ids, "PM_DUPLICATE_SOURCE_RECORD_ID")
        ids.add(record_id)
        rtype = _text(item.get("record_type"), "PM_RECORD_TYPE_MISSING")
        _require(rtype in ALLOWED_TYPES, "PM_UNSUPPORTED_RECORD_TYPE")
        stat = _text(item.get("stat_type"), "PM_STAT_MISSING").upper()
        _require(stat in ALLOWED_STATS, "PM_UNSUPPORTED_NBA_STAT")
        _require(item.get("period") == "FULL_GAME", "PM_UNSUPPORTED_PERIOD")
        provider_player = _text(item.get("provider_player_id"), "PM_PLAYER_ID_MISSING")
        provider_event = _text(item.get("provider_event_id"), "PM_EVENT_ID_MISSING")
        player = player_crosswalk.get(provider_player)
        event = event_crosswalk.get(provider_event)
        _require(isinstance(player, str) and bool(player.strip())
                 and isinstance(event, str) and bool(event.strip()),
                 "PM_CANONICAL_IDENTITY_UNRESOLVED")
        team = _text(item.get("team_id"), "PM_TEAM_ID_MISSING").upper()
        opponent = _text(item.get("opponent_id"), "PM_OPPONENT_ID_MISSING").upper()
        _require(team != opponent, "PM_TEAMS_CONFLICT")
        tip = _ts(item.get("event_start_at"), "PM_EVENT_TIME_INVALID")
        observed = _ts(item.get("observed_at"), "PM_OBSERVED_TIME_INVALID")
        retrieved = _ts(item.get("retrieved_at"), "PM_RETRIEVED_TIME_INVALID")
        _require(observed <= retrieved, "PM_AS_OF_CHRONOLOGY_CONFLICT")
        _require(item.get("source_field"), "PM_FIELD_PROVENANCE_MISSING")
        out: dict[str, Any] = {
            "provider": PROVIDER,
            "sport": SPORT,
            "source_record_id": record_id,
            "provider_player_id": provider_player, "canonical_player_id": player.strip(),
            "provider_event_id": provider_event, "canonical_event_id": event.strip(),
            "team_id": team, "opponent_id": opponent,
            "stat_type": stat, "period": "FULL_GAME",
            "source_field": _text(item["source_field"], "PM_FIELD_PROVENANCE_MISSING"),
            "event_start_at": _iso(tip), "observed_at": _iso(observed),
            "retrieved_at": _iso(retrieved), "record_type": rtype,
            "source_origin": FIXTURE_ORIGIN, "crosswalk_attested": False,
            "prediction_authority": False, "can_execute": False,
        }
        if rtype == "MARKET_DISCOVERY":
            _require(retrieved < tip and observed < tip, "PM_POST_TIP_MARKET_OBSERVATION")
            _require("actual_value" not in item and "metric_value" not in item,
                     "PM_MARKET_SPORTING_MIXED")
            line = _number(item.get("line"), "PM_EXACT_LINE_INVALID", low=0.5, high=100.0)
            direction = _text(item.get("direction"), "PM_DIRECTION_MISSING").upper()
            _require(direction in {"MORE", "LESS"}, "PM_DIRECTION_INVALID")
            bookmaker = _text(item.get("sportsbook"), "PM_BOOK_MISSING")
            odds = item.get("american_odds")
            _require(type(odds) is int and (odds <= -100 or odds >= 100)
                     and -10000 <= odds <= 10000, "PM_ODDS_INVALID")
            out.update(evidence_domain="MARKET", line=line, direction=direction,
                       sportsbook=bookmaker, american_odds=odds,
                       pregame_feature_eligible=False)
            thesis = (rtype, player, event, stat, bookmaker, line, direction, _iso(observed))
        elif rtype == "PREGAME_CONTEXT":
            _require(retrieved < tip and observed < tip, "PM_POST_TIP_CONTEXT_LEAKAGE")
            _require(not any(field in item for field in
                             ("line", "sportsbook", "american_odds", "actual_value")),
                     "PM_CONTEXT_MARKET_OR_LABEL_MIXED")
            name = _text(item.get("metric_name"), "PM_METRIC_NAME_MISSING").upper()
            _require(name in ALLOWED_CONTEXT, "PM_UNSUPPORTED_CONTEXT_METRIC")
            value = _number(item.get("metric_value"), "PM_CONTEXT_VALUE_INVALID")
            out.update(evidence_domain="SPORTING_RESEARCH", metric_name=name,
                       metric_value=value, pregame_feature_eligible=False,
                       no_verified_source_rights=True)
            thesis = (rtype, player, event, name, _iso(observed))
        else:
            _require(not any(field in item for field in
                             ("line", "sportsbook", "american_odds", "metric_value")),
                     "PM_OUTCOME_MARKET_OR_CONTEXT_MIXED")
            final = _ts(item.get("game_final_at"), "PM_GAME_FINAL_TIME_INVALID")
            _require(tip < final <= observed, "PM_FINAL_OUTCOME_CHRONOLOGY_INVALID")
            value = _number(item.get("actual_value"), "PM_ACTUAL_VALUE_INVALID")
            _require(value.is_integer(), "PM_COUNTING_STAT_NONINTEGER")
            out.update(evidence_domain="SPORTING_SETTLED_LABEL", actual_value=int(value),
                       game_final_at=_iso(final), pregame_feature_eligible=False,
                       training_label_authorized=False)
            thesis = (rtype, player, event, stat)
        _require(thesis not in theses, "PM_DUPLICATE_EVIDENCE_THESIS")
        theses.add(thesis)
        normalized.append(out)
    counts = dict(sorted(Counter(x["record_type"] for x in normalized).items()))
    return {
        "schema": "WOW_PROPSMADNESS_NBA_OFFLINE_NORMALIZED_V1",
        "status": "OFFLINE_SYNTHETIC_FIXTURE_VERIFIED",
        "source_sha256": hashlib.sha256(payload).hexdigest(),
        "provider": PROVIDER, "sport": SPORT, "raw_rows": len(rows),
        "counts_by_type": counts, "rows": normalized,
        "vendor_access": "UNVERIFIED", "rights_state": "CONTRACT_REQUIRED",
        "source_provenance_attested": False, "crosswalk_attested": False,
        "storage_write_authorized": False, "training_ready": False,
        "model_certified": False, "probability_publishable": False,
        "can_execute": False,
    }
