"""No-write, exact-source historic NFL TD evidence gate tests."""
from datetime import datetime, timezone
from types import SimpleNamespace

from v17 import nfl_atd_historical_source_binding as adapter

NOW=datetime(2026,10,21,20,tzinfo=timezone.utc)

def test_unknown_event_is_out_of_domain_and_never_fetched():
    def bad_http(*args,**kwargs):raise AssertionError("HTTP must not be called")
    r=adapter.prepare_exact_nfl_td_official_outcome(
        {"event_id":"2026_07_NYG_DAL"},{},http_get=bad_http,now=NOW)
    assert r["status"]=="HELD" and r["blocker"]=="PINNED_OFFICIAL_NFL_GAMEBOOK_NOT_REGISTERED"

def test_official_pdf_evidence_is_still_a_no_write_release_hold(monkeypatch):
    monkeypatch.setattr(adapter,"propose_official_nfl_atd",lambda *a,**kw:{
        "status":"PROPOSAL_REQUIRES_INDEPENDENT_SETTLEMENT_ACCEPTANCE",
        "actual_stat":0,"hypothetical_official_result":"HIT",
        "settlement_persisted":False,"can_execute":False,
    })
    url=adapter.SOURCE_REGISTRY["2026_06_CAR_PHI"]
    def fake_http(actual,**_):
        assert actual==url
        return SimpleNamespace(status_code=200,url=url,content=b"%PDF-FIXTURE")
    r=adapter.prepare_exact_nfl_td_official_outcome(
        {"event_id":"2026_06_CAR_PHI"},{},http_get=fake_http,now=NOW)
    assert r["status"]=="HOLD_PENDING_PROTECTED_SETTLEMENT_ACCEPTANCE"
    assert r["settlement_write_authorized"] is False
    assert r["candidate_official_outcome"]["actual_stat"]==0
    assert r["can_execute"] is False

def test_invalid_http_origin_or_redirect_cannot_prepare_an_outcome():
    url=adapter.SOURCE_REGISTRY["2026_06_CHI_ATL"]
    for response in (
        SimpleNamespace(status_code=301,url=url,content=b"%PDF-bytes"),
        SimpleNamespace(status_code=200,url="https://evil.invalid/pdf",content=b"%PDF-bytes"),
        SimpleNamespace(status_code=200,url=url,content=b"<html>fake PDF</html>"),
    ):
        r=adapter.prepare_exact_nfl_td_official_outcome(
            {"event_id":"2026_06_CHI_ATL"},{},
            http_get=lambda *a,**kw:response,now=NOW)
        assert r["status"]=="HELD" and r["blocker"]=="OFFICIAL_PDF_FETCH_OR_PROVENANCE_FAILED"

def test_research_evidence_mismatch_stays_held(monkeypatch):
    monkeypatch.setattr(adapter,"propose_official_nfl_atd",
                        lambda *a,**kw:{"status":"HELD","blocker":"PLAYER_IDENTITY_MISMATCH","can_execute":False})
    url=adapter.SOURCE_REGISTRY["2026_06_CHI_ATL"]
    response=SimpleNamespace(status_code=200,url=url,content=b"%PDF-ok")
    r=adapter.prepare_exact_nfl_td_official_outcome(
        {"event_id":"2026_06_CHI_ATL"},{},http_get=lambda *a,**kw:response,now=NOW)
    assert r["status"]=="HELD" and r["blocker"]=="PLAYER_IDENTITY_MISMATCH"
