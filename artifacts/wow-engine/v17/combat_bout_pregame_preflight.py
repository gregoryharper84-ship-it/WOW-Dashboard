"""Source-bound MMA/Boxing research input verification, not fight-win inference.

Separate sports, sources and fitted specialists are mandatory. Shared code
checks identity, chronology and source rights only; no Elo, odds, predictions,
implicit binary result, calibration, certification or execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from typing import Any, Mapping

from v17.model_source_entitlements import source_readiness

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
_SOURCE = {"MMA": "UFCSTATS_PUBLIC", "BOXING": "BOXING_DATA_API"}
_ALLOWED_ROUNDS = {"MMA": frozenset((3, 5)), "BOXING": frozenset((4, 6, 8, 10, 12))}
_OUTCOME_SPACE = ("FIGHTER_A", "FIGHTER_B", "DRAW", "NO_CONTEST")
_MARKET_KEYS = frozenset(("odds", "sportsbook", "price", "implied_probability",
                          "no_vig", "market_prior", "betting_line"))


class CombatEvidenceBlocked(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _time(value: Any) -> datetime:
    try:
        value = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if value.utcoffset() is None:
            raise ValueError("naive timestamp")
        return value.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CombatEvidenceBlocked("COMBAT_TIMESTAMP_INVALID") from exc


def _hash(raw: Any) -> str:
    return sha256(json.dumps(raw, sort_keys=True, separators=(",", ":"),
                            allow_nan=False).encode("utf-8")).hexdigest()


def preflight_combat_bout(
    sport: str, snapshot: Mapping[str, Any], *, now: datetime,
) -> dict[str, Any]:
    sport = str(sport or "").strip().upper()
    if sport not in _SOURCE:
        raise CombatEvidenceBlocked("COMBAT_SPORT_NOT_SUPPORTED_BY_INPUT_CONTRACT")
    if not isinstance(snapshot, Mapping):
        raise CombatEvidenceBlocked("COMBAT_PREGAME_SNAPSHOT_MISSING")
    source = _SOURCE[sport]
    if str(snapshot.get("source_id") or "") != source:
        raise CombatEvidenceBlocked("COMBAT_SOURCE_SPORT_MISMATCH")
    if not source_readiness(source).ready_for_candidate_training:
        raise CombatEvidenceBlocked("COMBAT_SOURCE_RIGHTS_OR_CREDENTIALS_MISSING")
    event_id = str(snapshot.get("official_event_id") or "").strip()
    bout_id = str(snapshot.get("official_bout_id") or "").strip()
    source_snapshot_id = str(snapshot.get("source_snapshot_id") or "").strip()
    if not event_id or not bout_id or not source_snapshot_id:
        raise CombatEvidenceBlocked("COMBAT_OFFICIAL_BOUT_IDENTITY_INCOMPLETE")
    event_start = _time(snapshot.get("event_start_time"))
    source_time = _time(snapshot.get("source_timestamp"))
    if not source_time <= _time(now) < event_start:
        raise CombatEvidenceBlocked("COMBAT_PREGAME_EVIDENCE_LEAKAGE")
    rounds = snapshot.get("scheduled_rounds")
    if type(rounds) is not int or rounds not in _ALLOWED_ROUNDS[sport]:
        raise CombatEvidenceBlocked("COMBAT_ROUNDS_OR_RULESET_UNSUPPORTED")
    weight_class = str(snapshot.get("weight_class") or "").strip()
    if not weight_class:
        raise CombatEvidenceBlocked("COMBAT_WEIGHT_CLASS_UNKNOWN")
    if tuple(snapshot.get("outcome_space") or ()) != _OUTCOME_SPACE:
        raise CombatEvidenceBlocked("COMBAT_DRAW_NO_CONTEST_RULES_UNRESOLVED")
    payload = snapshot.get("source_payload")
    if not isinstance(payload, Mapping):
        raise CombatEvidenceBlocked("COMBAT_SOURCE_PAYLOAD_MISSING")
    try:
        digest = _hash(payload)
    except (TypeError, ValueError) as exc:
        raise CombatEvidenceBlocked("COMBAT_SOURCE_PAYLOAD_INVALID") from exc
    if snapshot.get("source_payload_sha256") != digest:
        raise CombatEvidenceBlocked("COMBAT_SOURCE_HASH_MISMATCH")
    if (payload.get("official_event_id") != event_id
            or payload.get("official_bout_id") != bout_id):
        raise CombatEvidenceBlocked("COMBAT_SOURCE_BOUT_IDENTITY_MISMATCH")
    athletes = snapshot.get("participants")
    if not isinstance(athletes, list) or len(athletes) != 2:
        raise CombatEvidenceBlocked("COMBAT_TWO_FIGHTER_IDENTITY_REQUIRED")
    ids: list[str] = []
    for athlete in athletes:
        if not isinstance(athlete, Mapping):
            raise CombatEvidenceBlocked("COMBAT_PARTICIPANT_IDENTITY_INCOMPLETE")
        athlete_id = str(athlete.get("official_fighter_id") or "").strip()
        if not athlete_id:
            raise CombatEvidenceBlocked("COMBAT_PARTICIPANT_IDENTITY_INCOMPLETE")
        if athlete.get("participation_status") != "CONFIRMED" or athlete.get("weigh_in_status") != "OFFICIAL":
            raise CombatEvidenceBlocked("COMBAT_FIGHTER_STATUS_NOT_VERIFIED")
        if _time(athlete.get("feature_as_of")) > source_time:
            raise CombatEvidenceBlocked("COMBAT_POST_SNAPSHOT_FEATURE_LEAKAGE")
        features = athlete.get("historical_features")
        if not isinstance(features, Mapping) or not features:
            raise CombatEvidenceBlocked("COMBAT_HISTORICAL_FEATURES_INCOMPLETE")
        for key, value in features.items():
            if str(key).lower() in _MARKET_KEYS:
                raise CombatEvidenceBlocked("COMBAT_MARKET_FEATURE_FORBIDDEN")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                raise CombatEvidenceBlocked("COMBAT_HISTORICAL_FEATURE_INVALID")
        ids.append(athlete_id)
    if len(set(ids)) != 2:
        raise CombatEvidenceBlocked("COMBAT_DUPLICATE_FIGHTER_ID")
    if set(payload.get("official_fighter_ids") or []) != set(ids):
        raise CombatEvidenceBlocked("COMBAT_SOURCE_FIGHTER_BINDING_MISMATCH")
    return {
        "status": "CANDIDATE_INPUT_EVIDENCE_SOURCE_REVIEW_REQUIRED",
        "sport": sport, "official_event_id": event_id, "official_bout_id": bout_id,
        "source_snapshot_id": source_snapshot_id, "source_payload_sha256": digest,
        "controlling_sport_specialist": None, "model_probability": None,
        "probability_publishable": False, "rank_eligible": False,
        "independent_source_review_required": True, "can_execute": False,
    }
