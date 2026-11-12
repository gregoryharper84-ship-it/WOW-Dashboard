"""Read-only research evidence manifest structural invariants for #1942."""
import json
from pathlib import Path
from urllib.parse import urlparse

PATH = Path(__file__).parent / "research/nfl_atd_2026_wk06_official_candidate_evidence.json"

def load():
    return json.loads(PATH.read_text(encoding="utf-8"))

def test_manifest_has_no_unearned_settlement_or_betting_authority():
    data=load()
    assert data["can_execute"] is False
    assert data["official_settlement_authority"] is False
    assert data["persisted_outcomes"] == 0
    assert data["status"] == "RESEARCH_CANDIDATES_ONLY_DO_NOT_SETTLE"

def test_five_independent_player_markets_are_not_ten_fake_outcomes():
    data=load()
    markets=[(event,player) for event in data["events"] for player in event["players"]]
    assert len(markets)==5
    assert len({(event["canonical_event_id"],player["name"],player["line"]) for event,player in markets})==5
    assert sum(len(player["directions"]) for _,player in markets)==10
    assert all(player["directions"]==["MORE","LESS"] and player["line"]==.5 for _,player in markets)
    assert all(player["scoring_touchdowns"] == 0 for _,player in markets)

def test_evidence_is_anchored_to_real_nfl_or_club_source_urls():
    data=load()
    for event in data["events"]:
        for field in ("scoring_evidence_url","participation_evidence_url"):
            url=urlparse(event[field])
            assert url.scheme=="https"
            assert url.hostname in {"www.nflgsis.com","www.chicagobears.com","static.www.nfl.com"}
            assert not url.username and not url.password
        assert event["canonical_event_id"].startswith("2026_06_")
        assert len(event["players"])>=2
