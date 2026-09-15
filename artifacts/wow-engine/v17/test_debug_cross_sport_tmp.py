from v17.test_daily_snapshot_runtime import (
    Event,
    SLATE_DATE,
    _cross_sport_event,
    _cross_sport_feed,
    governed_team_result,
)
from v17.daily_snapshot_runtime import DailySnapshotRequest


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

    req = DailySnapshotRequest(
        requested_slate_date=SLATE_DATE,
        requested_timezone="America/Chicago",
        lanes=["MONEYLINE"],
    )
    # Call the cross-sport integration boundary directly so pytest prints the
    # exact exception/traceback rather than run_daily_snapshot's typed wrapper.
    runtime._cross_sport_moneyline_rows(
        req,
        run_id="debug-cross-sport",
        event_api=Event,
        covered_event_ids=set(),
    )
