from __future__ import annotations

from copy import deepcopy
import pytest

from v17.nfl_pickem_full_pool_equity import PoolEquityInputError
from v17.nfl_pickem_ownership_forecast import validate_prelock_ownership_forecast


LOCK = "2026-10-11T12:00:00Z"
EVENTS = [{"official_event_id": "A@H", "home_team": "H", "away_team": "A"}]
FORECAST = {
    "audience": "OPPONENT_OWNERSHIP_RESEARCH",
    "source_id": "prelock-survey",
    "snapshot_id": "survey-20261010",
    "as_of": "2026-10-10T10:00:00Z",
    "available_to_user_at": "2026-10-10T11:00:00Z",
    "home_ownership": {"A@H": 0.7},
}


def test_prelock_snapshot_is_research_only_and_deterministic():
    before = deepcopy(FORECAST)
    result = validate_prelock_ownership_forecast(FORECAST, lock_at=LOCK, events=EVENTS)
    assert result == validate_prelock_ownership_forecast(FORECAST, lock_at=LOCK, events=EVENTS)
    assert result["home_ownership"] == {"A@H": .7}
    assert result["can_execute"] is False
    assert result["actual_opponent_cards_known"] is False
    assert result["certified_first_place_equity"] is False
    assert result["automatic_promotion"] is False
    assert result["production_pick_mutation_allowed"] is False
    assert result["production_probability_mutation_allowed"] is False
    assert FORECAST == before


@pytest.mark.parametrize("field,value,code", [
    ("as_of", "2026-10-11T12:00:01Z", "PICKEM_OWNERSHIP_POST_LOCK_FORECAST"),
    ("available_to_user_at", "2026-10-11T12:00:01Z", "PICKEM_OWNERSHIP_POST_LOCK_FORECAST"),
    ("as_of", "2026-10-10T12:00:00", "PICKEM_OWNERSHIP_TIMESTAMP_INVALID"),
    ("snapshot_id", "", "PICKEM_OWNERSHIP_SNAPSHOT_REQUIRED"),
    ("audience", "OPPONENT_ENTRIES", "PICKEM_OWNERSHIP_AUDIENCE_INVALID"),
    ("home_ownership", {"A@H": 1.2}, "PICKEM_OWNERSHIP_FRACTION_INVALID"),
    ("home_ownership", {"wrong": 0.7}, "PICKEM_OWNERSHIP_EVENT_SET_MISMATCH"),
    ("home_ownership", {"A@H": float("nan")}, "PICKEM_OWNERSHIP_FRACTION_INVALID"),
    ("home_ownership", {"A@H": True}, "PICKEM_OWNERSHIP_FRACTION_INVALID"),
])
def test_invalid_forecast_fails_closed(field, value, code):
    forecast = {**FORECAST, field: value}
    with pytest.raises(PoolEquityInputError, match=code):
        validate_prelock_ownership_forecast(forecast, lock_at=LOCK, events=EVENTS)


def test_availability_must_follow_forecast_creation():
    forecast = {**FORECAST, "available_to_user_at": "2026-10-10T09:00:00Z"}
    with pytest.raises(PoolEquityInputError, match="PICKEM_OWNERSHIP_POST_LOCK_FORECAST"):
        validate_prelock_ownership_forecast(forecast, lock_at=LOCK, events=EVENTS)
