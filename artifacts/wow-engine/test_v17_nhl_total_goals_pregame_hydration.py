"""Research-only NHL total-goals pregame feature hydration and parity replay."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from copy import deepcopy

import pytest

from v17.nhl_total_goals_official_fixture import load_checked_in_season
from v17.nhl_total_goals_challenger import build_total_rows
from v17.nhl_total_goals_pregame_hydration import (
    CAN_EXECUTE, PROBABILITY_PUBLISHABLE,
    NHLTotalsPregameSourceError, hydrate_nhl_total_goals_pregame,
)

FIXTURE_DIR=Path(__file__).parent / "tests" / "fixtures"


def examples():
    games,_=load_checked_in_season(2021,fixture_directory=FIXTURE_DIR)
    source=[]
    for g in games:
        source.append({
            "sport":"NHL","source_provider":"NHL_PUBLIC_WEB_API",
            "historical_reconstruction":True,"can_execute":False,
            "official_event_id":g.event_id,"season":"20212022",
            "event_start_time":g.start_utc,
            "home_team":g.home_team,"away_team":g.away_team,
            "home_score":g.home_goals,"away_score":g.away_goals,
            "source_payload_sha256":g.source_projection_sha256,
        })
    model_rows,_=build_total_rows(source)
    # Pick a real NHL game with sufficient prior history for both teams.
    historical=model_rows[100]
    g=next(x for x in games if x.event_id==historical.event_id)
    start=datetime.fromisoformat(g.start_utc)
    simulated_source_time=(start-timedelta(minutes=2)).isoformat()
    simulated_as_of=(start-timedelta(minutes=1)).isoformat()
    target={
        "id":int(g.event_id),"season":20212022,"gameType":2,"gameState":"FUT",
        "startTimeUTC":g.start_utc,
        "homeTeam":{"abbrev":g.home_team},
        "awayTeam":{"abbrev":g.away_team},
        "source_id":"NHL_PUBLIC_WEB_API",
        "source_snapshot_id":"SYNTHETIC_REPLAY_NOT_A_REAL_PREGAME_CAPTURE",
        "source_payload_sha256":g.source_projection_sha256,
        "source_captured_at_utc":simulated_source_time,
    }
    # Only records before the simulated as-of are included. They really
    # settled historically, but no time-frozen archived receipt is asserted.
    past=[x for x in source if datetime.fromisoformat(x["event_start_time"]) <
          datetime.fromisoformat(simulated_as_of)]
    return target,past,simulated_as_of,historical


def test_prior_game_features_reproduce_real_fitted_training_row_without_result():
    target,past,as_of,expected=examples()
    result=hydrate_nhl_total_goals_pregame(past,target,evidence_as_of_utc=as_of)
    assert result["status"]=="NHL_TOTALS_PREGAME_RESEARCH_FEATURES_READY_NOT_CERTIFIED"
    assert result["features"]==pytest.approx(expected.features,abs=1e-10)
    assert len(result["features"])==6
    assert result["evidence"]["target_official_event_id"]==expected.event_id
    assert len(result["feature_receipt_sha256"])==64
    assert result["evidence"]["home_prior_games"]>=5
    assert result["evidence"]["away_prior_games"]>=5
    assert result["starting_goalies_confirmed"] is False
    assert result["completed_league_schedule_coverage_verified"] is False
    assert result["source_signature_independently_verified"] is False
    assert result["fitted_model_invoked"] is False
    assert result["certified_probability_package"] is None
    assert result["calibrated_lower_bound"] is None
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert not CAN_EXECUTE and not PROBABILITY_PUBLISHABLE


@pytest.mark.parametrize("change,code",[
    (lambda target,past,as_of:(target.__setitem__("gameState","OFF")), "NHL_TOTALS_PREGAME_TARGET_INVALID"),
    (lambda target,past,as_of:(target["homeTeam"].__setitem__("abbrev",target["awayTeam"]["abbrev"])), "NHL_TOTALS_PREGAME_TARGET_INVALID"),
    (lambda target,past,as_of:(target.__setitem__("source_payload_sha256","unknown")), "NHL_TOTALS_PREGAME_TARGET_INVALID"),
    (lambda target,past,as_of:(target.__setitem__("source_id","ODDS_API")), "NHL_TOTALS_PREGAME_TARGET_INVALID"),
    (lambda target,past,as_of:(target.__setitem__("source_captured_at_utc",target["startTimeUTC"])), "NHL_TOTALS_PREGAME_TARGET_INVALID"),
    (lambda target,past,as_of:(past.append(deepcopy(past[0]))), "NHL_TOTALS_PREGAME_DUPLICATE_EVENT"),
    (lambda target,past,as_of:(past[0].__setitem__("season","20222023")), "NHL_TOTALS_PREGAME_SEASON_CONFLICT"),
    (lambda target,past,as_of:(past[0].__setitem__("source_payload_sha256","bad")), "NHL_TOTALS_PREGAME_PRIOR_SOURCE_INVALID"),
])
def test_pregame_source_identity_conflict_or_future_evidence_fails_closed(change,code):
    target,past,as_of,_=examples()
    change(target,past,as_of)
    with pytest.raises(NHLTotalsPregameSourceError) as err:
        hydrate_nhl_total_goals_pregame(past,target,evidence_as_of_utc=as_of)
    assert err.value.code==code


def test_short_prior_history_is_typed_not_automatically_estimated():
    target,past,as_of,_=examples()
    past=[r for r in past if r["home_team"] not in (
        target["homeTeam"]["abbrev"],target["awayTeam"]["abbrev"]
    ) and r["away_team"] not in (
        target["homeTeam"]["abbrev"],target["awayTeam"]["abbrev"]
    )]
    with pytest.raises(NHLTotalsPregameSourceError) as err:
        hydrate_nhl_total_goals_pregame(past,target,evidence_as_of_utc=as_of)
    assert err.value.code=="NHL_TOTALS_PREGAME_PRIOR_HISTORY_INSUFFICIENT"


def test_hypothetical_result_observed_after_as_of_is_not_pregame_history():
    target,past,as_of,_=examples()
    past[0]["event_start_time"]=target["startTimeUTC"]
    with pytest.raises(NHLTotalsPregameSourceError) as err:
        hydrate_nhl_total_goals_pregame(past,target,evidence_as_of_utc=as_of)
    assert err.value.code=="NHL_TOTALS_PREGAME_FUTURE_GAME_LEAKAGE"
