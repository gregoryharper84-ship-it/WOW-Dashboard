from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Header
from fastapi.testclient import TestClient
from pydantic import BaseModel

from v17 import prop_stat_alias_boundary as subject


class _Request(BaseModel):
    sport: str
    stat_type: str


class _Market:
    ScorePropRequest = _Request


def test_nfl_host_aliases_use_pick_request_canonical_stat_authority():
    assert subject.canonical_stat_type("NFL", "PASS_YARDS") == "PASSING_YARDS"
    assert subject.canonical_stat_type("NFL", "RUSH_YARDS") == "RUSHING_YARDS"
    assert subject.canonical_stat_type("NFL", "REC_YARDS") == "RECEIVING_YARDS"
    assert subject.canonical_stat_type("NFL", "ANYTIME_TD") == "ANYTIME_TD"


def test_final_score_prop_boundary_rewrites_alias_before_captured_route():
    app = FastAPI()
    observed = {}

    @app.post("/score-prop", operation_id="scoreWowProp")
    def score_prop(
        req: _Request,
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ):
        observed["stat_type"] = req.stat_type
        observed["identity"] = x_wow_model_identity
        return {
            "controlling_specialist": "wow.nfl-player-prop-probability-expert",
            "probability_publishable": False,
            "can_execute": False,
        }

    assert subject.install_score_prop_alias_boundary(app, market_api=_Market()) is True

    with TestClient(app) as client:
        response = client.post(
            "/score-prop",
            headers={"X-WOW-Model-Identity": "WOW_BETTING_ENGINE"},
            json={"sport": "NFL", "stat_type": "PASS_YARDS"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert observed == {
        "stat_type": "PASSING_YARDS",
        "identity": "WOW_BETTING_ENGINE",
    }
    assert payload["stat_type_resolution"] == {
        "requested_stat_type": "PASS_YARDS",
        "canonical_stat_type": "PASSING_YARDS",
        "rewritten": True,
        "probability_mutated": False,
        "can_execute": False,
    }
    assert payload["can_execute"] is False
