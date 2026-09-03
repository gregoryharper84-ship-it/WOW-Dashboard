from datetime import datetime, timedelta, timezone

from v17.daily_prop_acquisition import _candidate_line, _schedule_pitchers


def test_candidate_line_is_discovery_only_half_point_from_prior10_median():
    assert _candidate_line([2,3,4,5,5,6,6,7,8,9]) == 5.5


def test_schedule_pitchers_returns_only_future_probable_pitchers():
    now=datetime.now(timezone.utc)
    future=(now+timedelta(hours=4)).isoformat().replace('+00:00','Z')
    past=(now-timedelta(hours=1)).isoformat().replace('+00:00','Z')
    slate=(now+timedelta(hours=4)).date().isoformat()
    payload={"dates":[{"games":[
        {"gamePk":1,"gameDate":future,"teams":{"home":{"probablePitcher":{"fullName":"Home P"}},"away":{"probablePitcher":{"fullName":"Away P"}}}},
        {"gamePk":2,"gameDate":past,"teams":{"home":{"probablePitcher":{"fullName":"Past P"}},"away":{}}},
    ]}]}
    rows=_schedule_pitchers(payload,requested_date=slate,requested_timezone="UTC",now=now)
    assert {(row['event_id'],row['player']) for row in rows} == {('MLB:1','Home P'),('MLB:1','Away P')}


def test_discovery_line_is_not_described_as_market_line():
    import v17.daily_prop_acquisition as module
    assert "No sportsbook line is invented" in module.__doc__
    assert module.SOURCE_TYPE == "AUTONOMOUS_DISCOVERY"
