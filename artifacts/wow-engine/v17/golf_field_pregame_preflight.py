"""PGA outright-winner historical/forward FIELD evidence preflight; never a model.

A PGA field is a mutually-exclusive multi-entrant outcome, not a two-team
moneyline. This is an input validation boundary only: provider entitlements,
independent identity approval, fitting, calibration and dynamic bounds remain
separate, and LPGA requires its own approved data/source contract.
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
MARKET_FIELDS = frozenset((
    "odds", "price", "implied_probability", "market_probability",
    "no_vig", "sportsbook", "bookmaker", "betting_line",
))


class GolfFieldInputBlocked(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _time(value: Any) -> datetime:
    try:
        ts = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if ts.utcoffset() is None:
            raise ValueError("timezone missing")
        return ts.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GolfFieldInputBlocked("GOLF_TIMESTAMP_INVALID") from exc


def _digest(payload: Any) -> str:
    return sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def preflight_pga_field(
    snapshot: Mapping[str, Any], *, now: datetime,
) -> dict[str, Any]:
    """Admit *evidence* to research only; never compute entrant probabilities."""
    if not isinstance(snapshot, Mapping):
        raise GolfFieldInputBlocked("GOLF_EVIDENCE_MISSING")
    tour = str(snapshot.get("tour") or "").upper()
    if tour != "PGA":
        # LPGA is a separate tour/source/model: never alias its field to PGA.
        raise GolfFieldInputBlocked("GOLF_TOUR_SOURCE_NOT_CERTIFIED")
    evidence_source = str(snapshot.get("source_id") or "").upper()
    if evidence_source != "DATA_GOLF":
        raise GolfFieldInputBlocked("GOLF_MODEL_SOURCE_NOT_APPROVED")
    if not source_readiness("DATA_GOLF").ready_for_candidate_training:
        raise GolfFieldInputBlocked("GOLF_TRAINING_SOURCE_ENTITLEMENT_NOT_PROVEN")
    event_id = str(snapshot.get("official_tournament_id") or "").strip()
    source_id = str(snapshot.get("source_snapshot_id") or "").strip()
    if not event_id or not source_id:
        raise GolfFieldInputBlocked("GOLF_CANONICAL_FIELD_IDENTITY_INCOMPLETE")
    now_utc = _time(now)
    start = _time(snapshot.get("event_start_time"))
    source_time = _time(snapshot.get("source_timestamp"))
    if not source_time <= now_utc < start or not source_time < start:
        raise GolfFieldInputBlocked("GOLF_PREGAME_CUTOFF_VIOLATION")
    payload = snapshot.get("source_payload")
    if not isinstance(payload, Mapping):
        raise GolfFieldInputBlocked("GOLF_SOURCE_PAYLOAD_MISSING")
    try:
        digest = _digest(payload)
    except (TypeError, ValueError) as exc:
        raise GolfFieldInputBlocked("GOLF_SOURCE_PAYLOAD_INVALID") from exc
    if snapshot.get("source_payload_sha256") != digest:
        raise GolfFieldInputBlocked("GOLF_SOURCE_PAYLOAD_HASH_MISMATCH")
    if str(payload.get("official_tournament_id") or "") != event_id:
        raise GolfFieldInputBlocked("GOLF_SOURCE_EVENT_ID_MISMATCH")
    entrants = snapshot.get("entrants")
    if not isinstance(entrants, list) or len(entrants) < 3:
        # A field outright cannot be manufactured from a two-player H2H event.
        raise GolfFieldInputBlocked("GOLF_FIELD_OUTCOME_SPACE_INVALID")
    ids: list[str] = []
    for entrant in entrants:
        if not isinstance(entrant, Mapping):
            raise GolfFieldInputBlocked("GOLF_ENTRANT_IDENTITY_INCOMPLETE")
        player_id = str(entrant.get("official_player_id") or "").strip()
        if not player_id:
            raise GolfFieldInputBlocked("GOLF_ENTRANT_IDENTITY_INCOMPLETE")
        if entrant.get("status") != "CONFIRMED":
            raise GolfFieldInputBlocked("GOLF_WITHDRAWAL_OR_FIELD_STATUS_UNRESOLVED")
        feature_time = _time(entrant.get("feature_as_of"))
        if feature_time > source_time or feature_time >= start:
            raise GolfFieldInputBlocked("GOLF_FEATURE_TEMPORAL_LEAKAGE")
        features = entrant.get("historical_features")
        if not isinstance(features, Mapping) or not features:
            raise GolfFieldInputBlocked("GOLF_HISTORICAL_FEATURES_MISSING")
        for key, value in features.items():
            if str(key).lower() in MARKET_FIELDS:
                raise GolfFieldInputBlocked("GOLF_MARKET_FEATURE_FORBIDDEN")
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not isfinite(value):
                raise GolfFieldInputBlocked("GOLF_FEATURE_NONFINITE_OR_UNTYPED")
        ids.append(player_id)
    if len(set(ids)) != len(ids):
        raise GolfFieldInputBlocked("GOLF_DUPLICATE_ENTRANT_ID")
    if set(payload.get("field_player_ids") or []) != set(ids):
        raise GolfFieldInputBlocked("GOLF_SOURCE_FIELD_ROSTER_MISMATCH")
    return {
        "status": "RESEARCH_EVIDENCE_PREPARED_UNCERTIFIED",
        "tour": "PGA",
        "official_tournament_id": event_id,
        "source_snapshot_id": source_id,
        "source_payload_sha256": digest,
        "field_size": len(ids),
        "model_probability": None,
        "probability_publishable": False,
        "rank_eligible": False,
        "independent_verification_status": "REQUIRED",
        "can_execute": False,
    }
