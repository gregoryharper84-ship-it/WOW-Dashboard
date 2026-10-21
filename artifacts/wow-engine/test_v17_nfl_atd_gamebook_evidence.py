"""NFL official gamebook candidate verification, no-write regressions."""
from datetime import datetime,timedelta,timezone
from v17 import nfl_atd_gamebook_evidence as engine

START=datetime(2026,10,18,17,tzinfo=timezone.utc)
NOW=datetime(2026,10,21,18,tzinfo=timezone.utc)
URL="https://www.nflgsis.com/2026/REG/06/60264/Gamebook.pdf"
PDF=b"%PDF-FIXTURE-SYNTAX-READER-INJECTED"
FIRST="""National Football League Game Summary
Date: Sunday, 10/18/2026
Carolina Panthers at Philadelphia Eagles
Lineups
QB 9 B.Young
WR 4 T.McMillan
WR 83 D.Moore
Scoring Plays
Team Qtr Time Play Description (Extra Point) (Drive Info) Visitor Home
Panthers 1 10:50 R.Fitzgerald 47 yd. Field Goal (6-32, 2:38) 3 0
Panthers 2 8:08 T.Tremble 12 yd. pass from B.Young (R.Fitzgerald kick) (5-35, 1:24) 10 0
Eagles 2 2:42 D.Pierce 2 yd. run (J.Elliott kick) (10-78, 5:26) 10 7
Panthers 4 6:33 T.Tremble 19 yd. pass from B.Young (kick failed) (4-48, 1:51) 25 14
"""

def fixture(player="Tommy Tremble",team="CAR"):
    prediction={
        "prediction_id":"p1","event_id":"2026_06_CAR_PHI","player":player,
        "sport":"NFL","stat_type":"ANYTIME_TD","direction":"MORE","line":0.5,
        "created_at":(START-timedelta(minutes=40)).isoformat(),
        "model_timestamp":(START-timedelta(hours=2)).isoformat(),
        "event_start_time":START.isoformat(),"can_execute":False,
    }
    snapshot={
        "event_id":"2026_06_CAR_PHI","player":player,
        "captured_at":(START-timedelta(hours=3)).isoformat(),"can_execute":False,
        "role_status":{
            "team":team,"opponent":"PHI" if team=="CAR" else "CAR",
            "player":player,"identity_binding_status":"PASS",
            "verified_canonical_event_id":"2026_06_CAR_PHI",
            "espn_athlete_id":"123456",
        },
    }
    return prediction,snapshot

def verify(monkeypatch,p,s,*,url=URL,text=FIRST):
    monkeypatch.setattr(engine,"_extract_page_1",lambda b:text)
    return engine.verify_anytime_td_gamebook(p,s,gamebook_url=url,pdf_bytes=PDF,now=NOW)

def test_official_scorer_receives_two_touchdowns_not_the_passer(monkeypatch):
    p,s=fixture()
    r=verify(monkeypatch,p,s)
    assert (r["status"],r["candidate_touchdowns"])==("CANDIDATE_ONLY",2)
    assert r["cannot_settle_without_independent_finalization"] is True
    assert r["can_execute"] is False

def test_passing_td_is_not_qb_anytime_scoring_td(monkeypatch):
    p,s=fixture("Bryce Young")
    r=verify(monkeypatch,p,s)
    assert r["candidate_touchdowns"]==0
    assert r["blocker"]=="PLAYER_PARTICIPATION_AND_FINAL_STATS_REQUIRED"

def test_zero_scorer_requires_participation_and_final_stat_checks(monkeypatch):
    p,s=fixture("David Moore")
    r=verify(monkeypatch,p,s)
    assert r["status"]=="HELD" and r["candidate_touchdowns"]==0

def test_mismatched_gamebook_event_rejected(monkeypatch):
    p,s=fixture()
    r=verify(monkeypatch,p,s,text=FIRST.replace("Carolina Panthers at Philadelphia Eagles","Chicago Bears at Atlanta Falcons"))
    assert r["blocker"]=="GSIS_GAMEBOOK_MATCHUP_MISMATCH"

def test_mismatched_gamebook_date_rejected(monkeypatch):
    p,s=fixture()
    assert verify(monkeypatch,p,s,text=FIRST.replace("10/18/2026","10/17/2026"))["blocker"]=="GSIS_GAMEBOOK_DATE_MISMATCH"

def test_url_cannot_be_spoofed(monkeypatch):
    p,s=fixture()
    for url in (
        "http://www.nflgsis.com/2026/REG/06/60264/Gamebook.pdf",
        "https://www.nflgsis.com.evil.invalid/2026/REG/06/60264/Gamebook.pdf",
        "https://www.nflgsis.com/2026/REG/05/60264/Gamebook.pdf",
        "https://www.nflgsis.com/2026/REG/06/60264/Gamebook.pdf?unsafe=true",
    ):
        assert verify(monkeypatch,p,s,url=url)["blocker"]=="NFL_GSIS_URL_NOT_CANONICAL"

def test_swapped_player_and_team_rejected(monkeypatch):
    p,s=fixture()
    s["role_status"]["player"]="Someone Else"
    assert verify(monkeypatch,p,s)["blocker"]=="PLAYER_IDENTITY_MISMATCH"
    p,s=fixture()
    s["role_status"]["team"]="ATL"
    assert verify(monkeypatch,p,s)["blocker"]=="TEAM_OPPONENT_IDENTITY_MISMATCH"

def test_after_kickoff_prediction_rejected(monkeypatch):
    p,s=fixture()
    p["created_at"]=(START+timedelta(minutes=1)).isoformat()
    assert verify(monkeypatch,p,s)["blocker"]=="PREGAME_OR_FINAL_TIME_INVALID"

def test_nonexecution_and_unsupported_route_rejected(monkeypatch):
    p,s=fixture()
    p["can_execute"]=True
    assert verify(monkeypatch,p,s)["blocker"]=="CAN_EXECUTE_MUST_BE_FALSE"
    p,s=fixture()
    p["stat_type"]="PASSING_TDS"
    assert verify(monkeypatch,p,s)["blocker"]=="NFL_ATD_EXACT_ROUTE_REQUIRED"

def test_unrecognized_scoring_classification_rejects_event(monkeypatch):
    p,s=fixture()
    extra=FIRST+"Panthers 4 0:20 D.Moore touchdown via unknown score type 25 20\n"
    assert verify(monkeypatch,p,s,text=extra)["blocker"]=="GSIS_SCORING_CLASSIFICATION_UNSUPPORTED"

def test_non_pdf_bytes_rejected(monkeypatch):
    p,s=fixture()
    r=engine.verify_anytime_td_gamebook(p,s,gamebook_url=URL,pdf_bytes=b"<html>not pdf</html>",now=NOW)
    assert r["blocker"]=="GSIS_PDF_MISSING_OR_OVERSIZED"

def test_adjacent_line_never_inherits_exact_touchdown_settlement_authority(monkeypatch):
    p,s=fixture()
    for wrong in (1.5, 2.5, -0.5, "NaN", True, None):
        p["line"]=wrong
        assert verify(monkeypatch,p,s)["blocker"] in {
            "ATD_EXACT_LINE_UNSUPPORTED", "ATD_LINE_INVALID",
        }
    p["line"]=0.5
    p["direction"]="TO_SCORE_IN_FIRST_QUARTER"
    assert verify(monkeypatch,p,s)["blocker"]=="ATD_DIRECTION_UNSUPPORTED"
    p["direction"]="MORE"
    p["period"]="FIRST_HALF"
    assert verify(monkeypatch,p,s)["blocker"]=="ATD_PERIOD_UNSUPPORTED"
