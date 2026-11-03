"""Research-only pre-lock opponent ownership projection.

This is a frozen, externally supplied ownership scenario evaluator. It does not
derive opponent choices from the sporting model, infer hidden actual entries,
or access post-lock opponent cards. All values are unvalidated *assumptions*
until independently evaluated on historical pre-lock snapshots.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite, isclose
from typing import Any, Mapping, Sequence

from v17.nfl_pickem_full_pool_equity import (
    PoolEquityInputError, MAX_GAMES, _required, _time,
)

CAN_EXECUTE = False
AUTOMATIC_PROMOTION = False
PRODUCTION_PICK_MUTATION_ALLOWED = False
PRODUCTION_PROBABILITY_MUTATION_ALLOWED = False


def validate_prelock_ownership_forecast(
    forecast: Mapping[str, Any],
    *,
    lock_at: str,
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return an immutable-style normalized research snapshot, never picks.

    Each event must contain an explicit pair of official team identifiers.
    Forecast fractions are *opponent ownership assumptions*, not fitted
    sporting probabilities; provenance must be available before contest lock.
    """
    lock = _time(lock_at, "PICKEM_EQUITY_LOCK_INVALID")
    if not isinstance(events, (list, tuple)) or not 1 <= len(events) <= MAX_GAMES:
        raise PoolEquityInputError("PICKEM_EQUITY_GAME_COUNT_INVALID")
    if not isinstance(forecast, Mapping):
        raise PoolEquityInputError("PICKEM_OWNERSHIP_FORECAST_REQUIRED")
    if forecast.get("audience") != "OPPONENT_OWNERSHIP_RESEARCH":
        raise PoolEquityInputError("PICKEM_OWNERSHIP_AUDIENCE_INVALID")
    source = _required(forecast.get("source_id"), "PICKEM_OWNERSHIP_SOURCE_REQUIRED")
    receipt = _required(forecast.get("snapshot_id"), "PICKEM_OWNERSHIP_SNAPSHOT_REQUIRED")
    asof = _time(forecast.get("as_of"), "PICKEM_OWNERSHIP_TIMESTAMP_INVALID")
    available = _time(forecast.get("available_to_user_at"), "PICKEM_OWNERSHIP_AVAILABILITY_INVALID")
    if asof > lock or available > lock or available < asof:
        raise PoolEquityInputError("PICKEM_OWNERSHIP_POST_LOCK_FORECAST")
    owners = forecast.get("home_ownership")
    if not isinstance(owners, Mapping):
        raise PoolEquityInputError("PICKEM_OWNERSHIP_EVENT_SET_MISMATCH")
    identities = {}
    for event in events:
        if not isinstance(event, Mapping):
            raise PoolEquityInputError("PICKEM_OWNERSHIP_EVENT_ID_INVALID")
        key = _required(event.get("official_event_id"), "PICKEM_OWNERSHIP_EVENT_ID_INVALID")
        home = _required(event.get("home_team"), "PICKEM_OWNERSHIP_TEAM_ID_INVALID")
        away = _required(event.get("away_team"), "PICKEM_OWNERSHIP_TEAM_ID_INVALID")
        if home == away or key in identities:
            raise PoolEquityInputError("PICKEM_OWNERSHIP_EVENT_ID_INVALID")
        identities[key] = (home, away)
    if set(owners) != set(identities):
        raise PoolEquityInputError("PICKEM_OWNERSHIP_EVENT_SET_MISMATCH")
    clean = {}
    for key in identities:
        value = owners[key]
        if (isinstance(value, bool) or not isinstance(value, (float, int))
                or not isfinite(float(value)) or not 0 <= float(value) <= 1):
            raise PoolEquityInputError("PICKEM_OWNERSHIP_FRACTION_INVALID")
        clean[key] = float(value)
    return {
        "status": "RESEARCH_FORECAST_VALIDATED",
        "source_id": source,
        "snapshot_id": receipt,
        "as_of": asof.isoformat(),
        "available_to_user_at": available.isoformat(),
        "home_ownership": clean,
        "ownership_semantics": "UNVALIDATED_OPPONENT_PICK_FRACTIONS",
        "actual_opponent_cards_known": False,
        "certified_first_place_equity": False,
        "can_execute": False,
        "automatic_promotion": False,
        "production_pick_mutation_allowed": False,
        "production_probability_mutation_allowed": False,
    }
