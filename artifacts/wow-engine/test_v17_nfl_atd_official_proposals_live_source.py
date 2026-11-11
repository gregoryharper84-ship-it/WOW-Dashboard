"""Acceptance on original NFL-hosted gamebooks: five exact player markets.

No database connection. No source PDF checked into Git. No persisted outcomes.
"""
from datetime import datetime, timezone
from urllib.request import Request, urlopen

import pytest

from test_v17_nfl_atd_gsis_live_source import _payload as car_payload
from test_v17_nfl_atd_nflcom_live_source import payload as chi_payload
from v17.nfl_atd_official_settlement_proposals import propose_official_nfl_atd

GSIS_URL = "https://www.nflgsis.com/2026/REG/06/60264/Gamebook.pdf"
NFL_URL = "https://static.www.nfl.com/image/upload/v1792409927/gamecenter/aab577eb-4feb-11f1-abca-2c54536568a9.pdf"

def _fetch(url: str) -> bytes:
    with urlopen(Request(url,headers={"User-Agent":"WOW-V17-Official-Outcome-Evidence/1.0"}),timeout=22) as response:
        assert response.status==200
        data=response.read(3_000_001)
    assert data.startswith(b"%PDF-") and len(data)<=3_000_000
    return data

@pytest.mark.parametrize("direction,expected",[("MORE","MISS"),("LESS","HIT")])
def test_all_five_real_official_scoring_candidates_without_persisting(direction,expected):
    sources=[(GSIS_URL,[(car_payload("Bryce Young"),"Bryce Young"),
                         (car_payload("David Moore"),"David Moore"),
                         (car_payload("Tetairoa McMillan"),"Tetairoa McMillan")]),
             (NFL_URL,[(chi_payload("Austin Hooper","ATL"),"Austin Hooper"),
                        (chi_payload("Kalif Raymond","CHI"),"Kalif Raymond")])]
    for url, cases in sources:
        data=_fetch(url)
        for (prediction,snapshot),name in cases:
            prediction["direction"]=direction
            prediction["line"]=0.5
            prediction["prediction_id"]=f"PROSPECTIVE-TEST-{name}-{direction}"
            proposal=propose_official_nfl_atd(
                prediction,snapshot,gamebook_url=url,pdf_bytes=data,
                now=datetime.now(timezone.utc))
            assert proposal["status"]=="PROPOSAL_REQUIRES_INDEPENDENT_SETTLEMENT_ACCEPTANCE",(name,proposal)
            assert proposal["actual_stat"]==0,(name,proposal)
            assert proposal["hypothetical_official_result"]==expected
            assert proposal["settlement_persisted"] is False
            assert proposal["can_execute"] is False
