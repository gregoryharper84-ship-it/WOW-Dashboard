"""Offline first-party NHL cutoff revalidation controls, no wagers."""
from datetime import datetime, timedelta, timezone

import pytest

from v17.nhl_cutoff_identity import NHLCutoffIdentityError, verify_nhl_identity_at_scoring
from v17.nhl_event_identity import NHLEventIdentityError

NOW = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)
START = NOW + timedelta(hours=6)
KW = {
    "official_event_id": "2026020381",
    "home_team": "New York Islanders",
    "away_team": "Toronto Maple Leafs",
    "event_start_time": START.isoformat(),
    "as_of_utc": NOW,
}


def official(**_kwargs):
    return {
        "event_id": "2026020381", "event_start_time": START.isoformat(),
        "home_team": "NYI", "away_team": "TOR", "game_type": 2,
        "game_state": "FUT", "identity_provider": "NHL_PUBLIC_WEB_API",
        "identity_source": "https://api-web.nhle.com/v1/club-schedule-season/NYI/20262027",
        "identity_match_sha256": "a" * 64, "identity_verified_at": NOW.isoformat(),
        "market_features_used": False, "prediction_authority": False,
        "can_execute": False,
    }


def test_read_only_current_fixture_verifies_and_never_claims_persistence():
    out = verify_nhl_identity_at_scoring(**KW, resolver=official)
    assert out["official_event_id"] == "2026020381"
    assert out["identity_match_sha256"] == "a" * 64
    assert out["source_snapshot_id"] is None
    assert out["source_persistence_status"] == "NOT_PERSISTED"
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False


@pytest.mark.parametrize("mutate,code", [
    ({"event_id": "2026020376"}, "NHL_CUTOFF_OFFICIAL_EVENT_ID_CHANGED"),
    ({"home_team": "TOR"}, "NHL_CUTOFF_PARTICIPANTS_CHANGED"),
    ({"event_start_time": (START + timedelta(minutes=1)).isoformat()},
     "NHL_CUTOFF_OFFICIAL_START_CHANGED"),
    ({"game_state": "LIVE"}, "NHL_EVENT_NOT_PREGAME"),
    ({"game_state": "OFF"}, "NHL_EVENT_NOT_PREGAME"),
    ({"game_state": "POSTPONED"}, "NHL_EVENT_NOT_PREGAME"),
    ({"game_type": 3}, "NHL_CUTOFF_GAME_TYPE_UNVERIFIED"),
    ({"identity_provider": "ESPN"}, "NHL_CUTOFF_OFFICIAL_PROVENANCE_INVALID"),
    ({"identity_match_sha256": None}, "NHL_CUTOFF_IDENTITY_DIGEST_MISSING"),
    ({"identity_match_sha256": "a" * 63}, "NHL_CUTOFF_IDENTITY_DIGEST_MISSING"),
    ({"identity_verified_at": (NOW - timedelta(minutes=6)).isoformat()},
     "NHL_CUTOFF_VERIFICATION_TIME_INVALID"),
])
def test_fails_closed_on_updated_official_source(mutate, code):
    with pytest.raises(NHLCutoffIdentityError) as exc:
        verify_nhl_identity_at_scoring(
            **KW, resolver=lambda **k: {**official(**k), **mutate}
        )
    assert exc.value.code == code


def test_source_unavailable_is_not_model_unavailable():
    def blocked(**_kwargs):
        raise NHLEventIdentityError("NHL_OFFICIAL_SOURCE_RATE_LIMITED")
    with pytest.raises(NHLCutoffIdentityError) as exc:
        verify_nhl_identity_at_scoring(**KW, resolver=blocked)
    assert exc.value.code == "NHL_OFFICIAL_SOURCE_RATE_LIMITED"


def test_provider_clock_drift_and_pregame_hold():
    for error in ("espn-401999999", "202602038"):
        with pytest.raises(NHLCutoffIdentityError) as exc:
            verify_nhl_identity_at_scoring(**{**KW, "official_event_id": error},
                                           resolver=official)
        assert exc.value.code == "NHL_CUTOFF_EVENT_INPUT_INVALID"
    with pytest.raises(NHLCutoffIdentityError) as exc:
        verify_nhl_identity_at_scoring(**{**KW, "as_of_utc": START},
                                       resolver=official)
    assert exc.value.code == "NHL_CUTOFF_EVENT_INPUT_INVALID"
