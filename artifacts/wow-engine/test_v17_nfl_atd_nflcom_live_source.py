"""Real NFL.com gamebook acceptance for Bears-Falcons 2026 Week 6.

Runs without Supabase credentials; source content is never saved or redistributed.
"""
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from v17.nfl_atd_gamebook_evidence import _official_url, verify_anytime_td_gamebook

URL="https://static.www.nfl.com/image/upload/v1792409927/gamecenter/aab577eb-4feb-11f1-abca-2c54536568a9.pdf"
START=datetime(2026,10,18,17,tzinfo=timezone.utc)

def payload(player,team):
    opponent="ATL" if team=="CHI" else "CHI"
    return (
        {"event_id":"2026_06_CHI_ATL","player":player,"sport":"NFL","stat_type":"ANYTIME_TD",
         "line":0.5,"direction":"LESS","can_execute":False,
         "created_at":(START-timedelta(minutes=40)).isoformat(),
         "model_timestamp":(START-timedelta(hours=2)).isoformat(),
         "event_start_time":START.isoformat()},
        {"event_id":"2026_06_CHI_ATL","player":player,
         "captured_at":(START-timedelta(hours=3)).isoformat(),
         "can_execute":False,"role_status":{
             "team":team,"opponent":opponent,"player":player,
             "identity_binding_status":"PASS",
             "verified_canonical_event_id":"2026_06_CHI_ATL",
             "espn_athlete_id":"123456",
         }},
    )

def test_nflcom_gamebook_host_allowlist_refuses_spoofed_origins():
    assert _official_url(URL,2026,6)
    for bad in (
        URL.replace("https:", "http:"),
        URL.replace("static.www.nfl.com", "static.www.nfl.com.evil.invalid"),
        URL+"?redirect=1",
        URL.replace("/image/upload/", "/image/"),
        "https://static.www.nfl.com/image/upload/v1792409927/gamecenter/not-a-uuid.pdf",
    ):
        assert not _official_url(bad,2026,6)

def test_real_nflcom_bears_falcons_pdf_scoring_identity_is_read_only():
    try:
        req=Request(URL,headers={"User-Agent":"Mozilla/5.0 WOW-V17-Audit/1.0","Accept":"application/pdf"})
        with urlopen(req, timeout=20) as response:
            assert response.status==200, f"OFFICIAL_NFL_HTTP_{response.status}"
            data=response.read(3_000_001)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise AssertionError(f"OFFICIAL_NFLCOM_SOURCE_UNAVAILABLE:{type(exc).__name__}") from exc
    assert data.startswith(b"%PDF-") and len(data)<3_000_000
    now=datetime.now(timezone.utc)
    # Both recorded, participating players had zero official scoring touchdowns;
    # candidate is not a settlement until final-stat/market rule acceptance.
    for name,team in (("Austin Hooper","ATL"),("Kalif Raymond","CHI")):
        p,s=payload(name,team)
        answer=verify_anytime_td_gamebook(p,s,gamebook_url=URL,pdf_bytes=data,now=now)
        assert answer["status"]=="HELD",answer
        assert answer["candidate_touchdowns"]==0,answer
        assert answer["blocker"]=="PLAYER_PARTICIPATION_AND_FINAL_STATS_REQUIRED"
        assert answer["can_execute"] is False
    p,s=payload("Austin Hooper","ATL")
    s["event_id"]="2026_06_CAR_PHI"
    assert verify_anytime_td_gamebook(p,s,gamebook_url=URL,pdf_bytes=data,now=now)["blocker"]=="EVENT_IDENTITY_MISMATCH"
