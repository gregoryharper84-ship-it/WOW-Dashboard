"""Governed derivation of model-ready NCAAF availability evidence.

Raw official-conference reports remain immutable source observations. Derived QB and
skill evidence is attributed to the reviewed WOW aggregation provider and carries
raw source identities/hashes in its payload. No probability is invented.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
DERIVED_PROVIDER = "WOW_NCAAF_AVAILABILITY_AGGREGATOR_V1"


class NCAAvailabilityAggregationUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _ts(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise NCAAvailabilityAggregationUnavailable("NCAAF_ROLE_TIMESTAMP_INVALID", f"{field} is required")
    try:
        out = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NCAAvailabilityAggregationUnavailable("NCAAF_ROLE_TIMESTAMP_INVALID", f"{field} malformed") from exc
    if out.utcoffset() is None:
        raise NCAAvailabilityAggregationUnavailable("NCAAF_ROLE_TIMESTAMP_INVALID", f"{field} must be offset-aware")
    return out


def _hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _raw_for(rows: Sequence[Mapping[str, Any]], *, team: str, player: str) -> Mapping[str, Any]:
    matches = []
    for row in rows:
        if str(row.get("evidence_kind") or "").upper() != "PLAYER_AVAILABILITY_REPORT":
            continue
        payload = row.get("payload")
        if isinstance(payload, Mapping) and str(payload.get("team") or "") == team and str(row.get("player") or "") == player:
            matches.append(row)
    if not matches:
        raise NCAAvailabilityAggregationUnavailable("NCAAF_STARTER_AVAILABILITY_NOT_PROVEN", f"No explicit raw availability row for {team} starter {player}")
    return max(matches, key=lambda r: _ts(r.get("evidence_timestamp"), "evidence_timestamp"))


def _source_ref(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "raw_provider": raw.get("source_provider"),
        "raw_record_id": raw.get("source_record_id"),
        "raw_source_uri": raw.get("source_uri"),
        "raw_evidence_sha256": raw.get("payload_sha256"),
    }


def build_qb_evidence(*, official_event_id: str, event_start_time: str, scope: str, team: str,
                      starter_qb: str, depth_chart_as_of: str, depth_chart_source: str,
                      raw_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    kickoff = _ts(event_start_time, "event_start_time")
    role_ts = _ts(depth_chart_as_of, "depth_chart_as_of")
    if role_ts >= kickoff:
        raise NCAAvailabilityAggregationUnavailable("NCAAF_ROLE_NOT_PREGAME", "Depth-chart evidence must be strictly pregame")
    scope = str(scope).upper()
    if scope not in {"HOME", "AWAY"}:
        raise NCAAvailabilityAggregationUnavailable("NCAAF_ROLE_SCOPE_INVALID", "scope must be HOME or AWAY")
    if not starter_qb or not depth_chart_source:
        raise NCAAvailabilityAggregationUnavailable("NCAAF_STARTER_IDENTITY_NOT_PROVEN", "starter_qb and depth_chart_source are required")

    raw = _raw_for(raw_rows, team=team, player=starter_qb)
    payload = raw.get("payload")
    if not isinstance(payload, Mapping):
        raise NCAAvailabilityAggregationUnavailable("NCAAF_AVAILABILITY_PAYLOAD_INVALID", "raw payload must be an object")
    status = str(payload.get("status") or "").upper()
    source_ref = _source_ref(raw)
    base_payload = {
        "starter_qb": starter_qb,
        "status": status,
        "depth_chart_source": depth_chart_source,
        "depth_chart_as_of": role_ts.isoformat(),
        "source_defined_play_probability": payload.get("source_defined_play_probability"),
        **source_ref,
    }
    common = {
        "official_event_id": official_event_id,
        "event_start_time": kickoff.isoformat(),
        "scope": scope,
        "team": team,
        "player": starter_qb,
        "source_provider": DERIVED_PROVIDER,
        "source_record_id": raw.get("source_record_id"),
        "source_uri": raw.get("source_uri"),
        "evidence_timestamp": str(raw.get("evidence_timestamp")),
        "provenance_grade": "B",
        "blocker_codes": [],
        "can_execute": False,
    }
    status_row = {**common, "evidence_kind": "QB_STATUS", "payload": base_payload}
    status_row["payload_sha256"] = _hash({"kind": "QB_STATUS", **base_payload, "official_event_id": official_event_id, "scope": scope})
    rows = [status_row]

    probability = payload.get("source_defined_play_probability")
    if isinstance(probability, (int, float)) and not isinstance(probability, bool) and 0.0 <= float(probability) <= 1.0:
        certainty_payload = {
            "value": float(probability),
            "starter_qb": starter_qb,
            "derivation": "OFFICIAL_CONFERENCE_POLICY_SOURCE_DEFINED",
            **source_ref,
        }
        certainty = {**common, "evidence_kind": "QB_CERTAINTY", "payload": certainty_payload}
        certainty["payload_sha256"] = _hash({"kind": "QB_CERTAINTY", **certainty_payload, "official_event_id": official_event_id, "scope": scope})
        rows.append(certainty)
    return rows


def build_skill_availability(*, official_event_id: str, event_start_time: str, scope: str, team: str,
                             player_weights: Mapping[str, float], raw_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    kickoff = _ts(event_start_time, "event_start_time")
    scope = str(scope).upper()
    if scope not in {"HOME", "AWAY"} or not player_weights:
        raise NCAAvailabilityAggregationUnavailable("NCAAF_SKILL_AGGREGATION_INVALID", "scope and nonempty player_weights are required")
    total = sum(float(v) for v in player_weights.values())
    if abs(total - 1.0) > 1e-9 or any(float(v) <= 0 for v in player_weights.values()):
        raise NCAAvailabilityAggregationUnavailable("NCAAF_SKILL_WEIGHTS_INVALID", "player weights must be positive and sum exactly to 1")
    components = []
    latest_ts: datetime | None = None
    weighted = 0.0
    raw_providers = set()
    for player, weight in player_weights.items():
        raw = _raw_for(raw_rows, team=team, player=player)
        payload = raw.get("payload")
        probability = payload.get("source_defined_play_probability") if isinstance(payload, Mapping) else None
        if not isinstance(probability, (int, float)) or isinstance(probability, bool):
            raise NCAAvailabilityAggregationUnavailable("NCAAF_SKILL_PROBABILITY_NOT_SOURCE_DEFINED", f"No source-defined probability for {player}")
        p = float(probability)
        if not 0.0 <= p <= 1.0:
            raise NCAAvailabilityAggregationUnavailable("NCAAF_SKILL_PROBABILITY_INVALID", f"Invalid source-defined probability for {player}")
        ts = _ts(raw.get("evidence_timestamp"), "evidence_timestamp")
        if ts >= kickoff:
            raise NCAAvailabilityAggregationUnavailable("NCAAF_AVAILABILITY_NOT_PREGAME", "Raw availability must be strictly pregame")
        latest_ts = ts if latest_ts is None or ts > latest_ts else latest_ts
        raw_providers.add(str(raw.get("source_provider")))
        weighted += float(weight) * p
        components.append({"player": player, "weight": float(weight), "play_probability": p, **_source_ref(raw)})
    if len(raw_providers) != 1:
        raise NCAAvailabilityAggregationUnavailable("NCAAF_SKILL_PROVIDER_MIXED", "A single governed raw provider is required per aggregation")
    payload = {"value": weighted, "derivation": "EXPLICIT_WEIGHTED_SOURCE_DEFINED_PLAY_PROBABILITY", "raw_provider": next(iter(raw_providers)), "components": components}
    return {
        "official_event_id": official_event_id,
        "event_start_time": kickoff.isoformat(),
        "evidence_kind": "SKILL_AVAILABILITY",
        "scope": scope,
        "team": team,
        "player": None,
        "source_provider": DERIVED_PROVIDER,
        "source_record_id": None,
        "source_uri": None,
        "evidence_timestamp": latest_ts.isoformat() if latest_ts else None,
        "provenance_grade": "B",
        "payload": payload,
        "payload_sha256": _hash({"kind": "SKILL_AVAILABILITY", **payload, "official_event_id": official_event_id, "scope": scope}),
        "blocker_codes": [],
        "can_execute": False,
    }
