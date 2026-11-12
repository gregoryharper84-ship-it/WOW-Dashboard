"""NFL score-event proposal tests: no wagering, no DB writes, no calibration."""
from datetime import datetime, timezone

from v17 import nfl_atd_official_settlement_proposals as proposals

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)
BYTES = b"%PDF-test-dummy"

def _p(direction="MORE",line=.5):
    return {"prediction_id":"receipt-1","event_id":"2026_06_CHI_ATL",
            "player":"Austin Hooper","line":line,"direction":direction}

def _s():
    return {"source_snapshot_id":"snapshot-1"}

def test_more_miss_and_less_hit_for_zero_td(monkeypatch):
    monkeypatch.setattr(proposals, "verify_anytime_td_gamebook",
       lambda *args,**kw: {"status":"HELD","blocker":"PLAYER_PARTICIPATION_AND_FINAL_STATS_REQUIRED","candidate_touchdowns":0})
    monkeypatch.setattr(proposals, "_participation_from_final_stats", lambda *a:(True,"POSITIVE_OFFICIAL_FINAL_GAME_STAT"))
    for direction,expected in [("MORE","MISS"),("LESS","HIT")]:
        result=proposals.propose_official_nfl_atd(_p(direction),_s(),gamebook_url="https://www.nflgsis.com/test",pdf_bytes=BYTES,now=NOW)
        assert result["hypothetical_official_result"]==expected
        assert result["actual_stat"]==0
        assert result["settlement_persisted"] is False
        assert result["calibration_performed"] is False
        assert result["can_execute"] is False

def test_positive_official_touchdown_is_not_double_counted_or_promoted(monkeypatch):
    monkeypatch.setattr(proposals, "verify_anytime_td_gamebook",
       lambda *args,**kw: {"status":"CANDIDATE_ONLY","candidate_touchdowns":2})
    monkeypatch.setattr(proposals, "_participation_from_final_stats", lambda *a:(True,"POSITIVE_OFFICIAL_FINAL_GAME_STAT"))
    r=proposals.propose_official_nfl_atd(_p(),_s(),gamebook_url="https://www.nflgsis.com/test",pdf_bytes=BYTES,now=NOW)
    assert r["actual_stat"]==2
    assert r["hypothetical_official_result"]=="HIT"
    assert r["settlement_persisted"] is False

def test_missing_snap_stat_play_proof_fails_closed(monkeypatch):
    monkeypatch.setattr(proposals, "verify_anytime_td_gamebook",
       lambda *args,**kw: {"status":"HELD","blocker":"PLAYER_PARTICIPATION_AND_FINAL_STATS_REQUIRED","candidate_touchdowns":0})
    monkeypatch.setattr(proposals, "_participation_from_final_stats",
                        lambda *a:(False,"PLAYER_IN_GAME_PARTICIPATION_NOT_PROVEN"))
    r=proposals.propose_official_nfl_atd(_p(),_s(),gamebook_url="x",pdf_bytes=BYTES,now=NOW)
    assert r["status"]=="HELD" and r["blocker"]=="PLAYER_IN_GAME_PARTICIPATION_NOT_PROVEN"

def test_official_identity_hold_never_converts_to_zero_td_hit(monkeypatch):
    monkeypatch.setattr(proposals, "verify_anytime_td_gamebook",
       lambda *args,**kw: {"status":"HELD","blocker":"PLAYER_IDENTITY_MISMATCH","can_execute":False})
    r=proposals.propose_official_nfl_atd(_p("LESS"),_s(),gamebook_url="x",pdf_bytes=BYTES,now=NOW)
    assert r["status"]=="HELD"
    assert "hypothetical_official_result" not in r

def test_unsupported_threshold_does_not_inherit_05_auth(monkeypatch):
    monkeypatch.setattr(proposals, "verify_anytime_td_gamebook",
       lambda *args,**kw: {"status":"HELD","blocker":"ATD_EXACT_LINE_UNSUPPORTED","can_execute":False})
    r=proposals.propose_official_nfl_atd(_p("MORE",1.5),_s(),gamebook_url="x",pdf_bytes=BYTES,now=NOW)
    assert r["blocker"]=="ATD_EXACT_LINE_UNSUPPORTED"

def test_positive_participation_requires_final_individual_stats_page():
    from unittest.mock import patch
    class Page:
        def __init__(self,value): self.value=value
        def extract_text(self,**_):return self.value
    class Reader:
        pages=[Page("National Football League Game Summary"),
               Page("Final Individual Statistics\nPASS RECEIVING\n A.Hooper 1 1 6 6.0 6 0")]
    with patch("pypdf.PdfReader",return_value=Reader()):
        ok,why=proposals._participation_from_final_stats(BYTES,"Austin Hooper")
        assert ok is True
        absent,reason=proposals._participation_from_final_stats(BYTES,"Kalif Raymond")
        assert absent is False and reason=="PLAYER_IN_GAME_PARTICIPATION_NOT_PROVEN"
