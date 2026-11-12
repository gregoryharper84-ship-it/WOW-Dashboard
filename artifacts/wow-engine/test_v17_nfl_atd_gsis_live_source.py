"""Public NFL GSIS real-PDF acceptance: NO DATABASE / NO WRITES.

The PDF is fetched transiently; do not commit or redistribute league document.
Network/source failures are typed acceptance failures, never fake passes.
"""
from datetime import datetime,timedelta,timezone
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from v17.nfl_atd_gamebook_evidence import verify_anytime_td_gamebook

EVENT_START=datetime(2026,10,18,17,tzinfo=timezone.utc)
URL="https://www.nflgsis.com/2026/REG/06/60264/Gamebook.pdf"

def _payload(player):
    return (
        {"event_id":"2026_06_CAR_PHI","player":player,"sport":"NFL",
         "stat_type":"ANYTIME_TD","line":0.5,"direction":"LESS","created_at":(EVENT_START-timedelta(minutes=40)).isoformat(),
         "model_timestamp":(EVENT_START-timedelta(hours=2)).isoformat(),
         "event_start_time":EVENT_START.isoformat(),"can_execute":False},
        {"event_id":"2026_06_CAR_PHI","player":player,
         "captured_at":(EVENT_START-timedelta(hours=3)).isoformat(),
         "can_execute":False,"role_status":{
             "team":"CAR","opponent":"PHI","player":player,
             "identity_binding_status":"PASS",
             "verified_canonical_event_id":"2026_06_CAR_PHI",
             "espn_athlete_id":"123456",
         }}
    )

def test_actual_official_nfl_gsis_scoring_pdf_is_parsed_without_writes():
    request=Request(URL,headers={"User-Agent":"WOW-V17-Accuracy-Research/1.0","Accept":"application/pdf"})
    try:
        with urlopen(request,timeout=20) as response:
            assert response.status==200, f"OFFICIAL_GSIS_HTTP_{response.status}"
            data=response.read(3_000_001)
    except (HTTPError,URLError,TimeoutError) as exc:
        raise AssertionError(f"OFFICIAL_GSIS_SOURCE_UNAVAILABLE:{type(exc).__name__}") from exc
    assert data.startswith(b"%PDF-"), "OFFICIAL_GSIS_PDF_INVALID"
    assert len(data)<3_000_000, "OFFICIAL_GSIS_PDF_OVERSIZED"
    t,s=_payload("Tommy Tremble")
    scored=verify_anytime_td_gamebook(t,s,gamebook_url=URL,pdf_bytes=data,now=datetime.now(timezone.utc))
    assert scored["status"]=="CANDIDATE_ONLY",scored
    assert scored["candidate_touchdowns"]==2,scored
    assert scored["can_execute"] is False
    q,qs=_payload("Bryce Young")
    throwing_is_not_scoring=verify_anytime_td_gamebook(q,qs,gamebook_url=URL,pdf_bytes=data,now=datetime.now(timezone.utc))
    assert throwing_is_not_scoring["status"]=="HELD",throwing_is_not_scoring
    assert throwing_is_not_scoring["candidate_touchdowns"]==0
