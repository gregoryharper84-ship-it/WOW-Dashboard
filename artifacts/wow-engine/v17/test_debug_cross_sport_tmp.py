from v17.test_daily_snapshot_runtime import (
    DB,
    Event,
    Market,
    SLATE_DATE,
    _cross_sport_event,
    _cross_sport_feed,
    governed_team_result,
)
from v17.daily_snapshot_runtime import DailySnapshotRequest, run_daily_snapshot


def test_debug_cross_sport_payload(monkeypatch):
    from v17 import cross_sport_discovery_feed as feed
    from v17 import daily_snapshot_runtime as runtime
    from v17.team_event_bridge_runtime import TEAM_EVENT_BRIDGES

    monkeypatch.setattr(runtime, "score_team_event_request", lambda *a, **k: governed_team_result())
    slate = _cross_sport_feed({
        "NFL": [_cross_sport_event("nfl-1")],
        "NHL": [_cross_sport_event("nhl-1")],
        "SOCCER": [_cross_sport_event("soccer-1")],
    })
    monkeypatch.setattr(feed, "odds_proxy_feed", lambda **kwargs: slate)
    monkeypatch.setattr(feed, "rundown_board_feed", lambda **kwargs: slate)
    for sport in ("NFL", "NHL", "SOCCER"):
        monkeypatch.delitem(TEAM_EVENT_BRIDGES, sport, raising=False)

    payload = run_daily_snapshot(
        DailySnapshotRequest(
            requested_slate_date=SLATE_DATE,
            requested_timezone="America/Chicago",
            lanes=["MONEYLINE"],
        ),
        db=DB(),
        market_api=Market,
        event_api=Event,
    )
    raise AssertionError(repr({
        "blockers": payload.get("blockers"),
        "audit": payload.get("cross_sport_discovery_audit"),
        "rows": payload.get("rows"),
    }))
