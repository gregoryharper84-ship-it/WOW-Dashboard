from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from pydantic import BaseModel

import v17.fantasy_score_candidate_bridge as bridge
import v17.fantasy_score_forward_cohort_route as route_mod
import v17.fantasy_score_forward_cohort_schema_repair as schema_repair
import v17.fantasy_score_forward_cohort_runtime as runtime


HASH_A = "a" * 64
HASH_B = "b" * 64


NFL_WEIGHTS = {
    "passing_yards": 0.04,
    "passing_td": 4.0,
    "interceptions": -1.0,
    "rushing_yards": 0.10,
    "rushing_td": 6.0,
    "receiving_yards": 0.10,
    "receptions": 1.0,
    "receiving_td": 6.0,
    "fumbles_lost": -2.0,
    "two_point_conversions": 2.0,
}


def _nfl_artifact() -> dict:
    baseline = {
        "passing_yards": 220.0,
        "passing_td": 1.4,
        "interceptions": 0.7,
        "rushing_yards": 24.0,
        "rushing_td": 0.25,
        "receiving_yards": 0.0,
        "receptions": 0.0,
        "receiving_td": 0.0,
        "fumbles_lost": 0.2,
        "two_point_conversions": 0.05,
    }
    residuals = [
        {
            "passing_yards": -80.0,
            "passing_td": -1.0,
            "interceptions": 0.8,
            "rushing_yards": -12.0,
            "rushing_td": -0.2,
            "receiving_yards": 0.0,
            "receptions": 0.0,
            "receiving_td": 0.0,
            "fumbles_lost": 0.8,
            "two_point_conversions": -0.05,
        },
        {name: 0.0 for name in bridge.NFL_COMPONENTS},
        {
            "passing_yards": 90.0,
            "passing_td": 1.0,
            "interceptions": -0.7,
            "rushing_yards": 18.0,
            "rushing_td": 0.8,
            "receiving_yards": 0.0,
            "receptions": 0.0,
            "receiving_td": 0.0,
            "fumbles_lost": -0.2,
            "two_point_conversions": 0.95,
        },
    ]
    return {
        "sport": "NFL",
        "stat_type": "FANTASY_SCORE",
        "feature_schema_version": bridge.FEATURE_SCHEMA_VERSION,
        "candidate_research_active": True,
        "lifecycle_state": "CANDIDATE",
        "promoted": False,
        "active": False,
        "probability_publishable": False,
        "can_execute": False,
        "artifact_format": bridge.ARTIFACT_FORMAT,
        "model_artifact_version": "NFL_FANTASY_SCORE_EMPIRICAL_RESIDUAL_CANDIDATE_V1",
        "training_dataset_hash": HASH_A,
        "artifact_checksum": HASH_B,
        "artifact_payload": {
            "artifact_schema_version": bridge.ARTIFACT_SCHEMA_VERSION,
            "lane": "NFL",
            "player_means": {"Player One": baseline},
            "position_means": {"QB": baseline},
            "residual_vectors": {"QB": residuals},
            "scoring_profile": {
                "profile_id": "TEST_NFL_FANTASY_SCORE_V1",
                "verified": True,
                "weights": NFL_WEIGHTS,
            },
        },
    }


def test_candidate_scoring_is_deterministic_nonpublishable_and_normalized():
    artifact = _nfl_artifact()
    evidence = {"role_status": {"position": "QB"}}

    first = bridge.score_candidate_from_artifact(
        artifact=artifact,
        lane="NFL",
        player="Player One",
        line=15.5,
        direction="MORE",
        evidence=evidence,
        seed=17,
        simulation_count=50_000,
        model_timestamp="2026-09-16T00:00:00+00:00",
    )
    second = bridge.score_candidate_from_artifact(
        artifact=artifact,
        lane="NFL",
        player="Player One",
        line=15.5,
        direction="MORE",
        evidence=evidence,
        seed=17,
        simulation_count=50_000,
        model_timestamp="2026-09-16T00:00:00+00:00",
    )

    assert first == second
    assert first["simulation_count"] == 50_000
    assert first["market_family"] == "NFL_DFS_FANTASY_SCORE"
    assert first["controlling_specialist"] == "wow.nfl-dfs-fantasy-score-expert"
    assert first["model_source_sha256"] == HASH_A
    assert first["model_artifact_checksum"] == HASH_B
    assert first["scoring_profile_id"] == "TEST_NFL_FANTASY_SCORE_V1"
    assert first["raw_candidate_probability"] == first["P(MORE)"]
    assert first["P(MORE)"] + first["P(LESS)"] + first["P(PUSH)"] == pytest.approx(1.0)
    assert first["calibrated_probability"] is None
    assert first["calibrated_lower_bound"] is None
    assert first["probability_publishable"] is False
    assert first["rank_eligible"] is False
    assert first["can_execute"] is False


def test_candidate_rejects_missing_nfl_position_as_inputs_insufficient():
    with pytest.raises(bridge.FantasyScoreCandidateBridgeError) as caught:
        bridge.score_candidate_from_artifact(
            artifact=_nfl_artifact(),
            lane="NFL",
            player="Player One",
            line=15.5,
            direction="MORE",
            evidence={"role_status": {}},
            seed=17,
            simulation_count=50_000,
        )
    assert caught.value.code == "NFL_FANTASY_POSITION_MISSING_OR_INVALID"
    assert caught.value.failure_class == "MODEL_INPUTS_INSUFFICIENT"


def test_candidate_rejects_any_production_authority_on_research_artifact():
    artifact = _nfl_artifact()
    artifact["active"] = True
    with pytest.raises(bridge.FantasyScoreCandidateBridgeError) as caught:
        bridge.score_candidate_from_artifact(
            artifact=artifact,
            lane="NFL",
            player="Player One",
            line=15.5,
            direction="MORE",
            evidence={"role_status": {"position": "QB"}},
            seed=17,
            simulation_count=50_000,
        )
    assert caught.value.code == "FANTASY_SCORE_CANDIDATE_AUTHORITY_CONFLICT"
    assert caught.value.failure_class == "MODEL_OUTPUT_INVALID"


class _Req(BaseModel):
    sport: str
    stat_type: str


class _FakeProd:
    @staticmethod
    def _reject_llp_prop_identity(value):
        return (value or "WOW_BETTING_ENGINE").upper()


class _FakeMarket:
    ScorePropRequest = _Req
    prod = _FakeProd()

    def __init__(self):
        self.calls = []

    def score_prop(self, req, identity=None):
        self.calls.append((req.sport, req.stat_type, identity))
        return {"source": "captured-original", "can_execute": False}


def test_runtime_installer_delegates_nonfantasy_and_intercepts_only_fantasy(monkeypatch):
    app = FastAPI()
    market = _FakeMarket()
    candidate_calls = []

    def fake_candidate(_market, req, *, model_identity):
        candidate_calls.append((req.sport, req.stat_type, model_identity))
        return {
            "source": "fantasy-candidate",
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }

    monkeypatch.setattr(route_mod, "score_fantasy_candidate_research", fake_candidate)

    assert route_mod.install_fantasy_score_candidate_runtime_bridge(
        app,
        auth_dependency=Depends(lambda: None),
        market_api=market,
    ) is True

    normal = market.score_prop(_Req(sport="MLB", stat_type="PITCHER_STRIKEOUTS"), "WOW_BETTING_ENGINE")
    fantasy = market.score_prop(_Req(sport="NFL", stat_type="FANTASY_SCORE"), "WOW_BETTING_ENGINE")

    assert normal["source"] == "captured-original"
    assert market.calls == [("MLB", "PITCHER_STRIKEOUTS", "WOW_BETTING_ENGINE")]
    assert fantasy["source"] == "fantasy-candidate"
    assert candidate_calls == [("NFL", "FANTASY_SCORE", "WOW_BETTING_ENGINE")]
    score_routes = [
        route for route in app.router.routes
        if getattr(route, "path", None) == "/score-prop" and "POST" in (getattr(route, "methods", set()) or set())
    ]
    assert len(score_routes) == 1


def test_schema_repair_selector_omits_nonexistent_team_and_opponent_columns():
    class _Result:
        data = []

    class _Query:
        def __init__(self):
            self.selected = None

        def select(self, value):
            self.selected = value
            return self

        def eq(self, *_args):
            return self

        def gt(self, *_args):
            return self

        def order(self, *_args):
            return self

        def limit(self, *_args):
            return self

        def execute(self):
            assert "team" not in self.selected.split(",")
            assert "opponent" not in self.selected.split(",")
            return _Result()

    class _Db:
        def table(self, name):
            assert name == "wow_prop_evidence_snapshots"
            return _Query()

    rows = schema_repair._eligible_snapshots_live_schema(
        _Db(),
        runtime.LANE_SPECS["NFL"],
        10,
        now=runtime.datetime.now(runtime.timezone.utc),
    )
    assert rows == []
