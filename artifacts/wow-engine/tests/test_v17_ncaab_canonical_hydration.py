"""NCAAB canonical source boundary: server-owned only, never publication."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from v17 import team_event_bridge_runtime as bridges


def _req():
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    return SimpleNamespace(
        requester_host_identity="WOW_BETTING_ENGINE",
        candidate_family="OUTRIGHT_WINNER",
        sport="NCAAB", league="NCAAB", official_event_id="NCAAB:123",
        event_key="NCAAB:123", home_team="Alpha", away_team="Beta",
        event_start_time_utc=start, sport_specific_evidence={},
    )


def _server_response(req):
    return {
        "official_event_id": req.official_event_id,
        "home_team": req.home_team,
        "away_team": req.away_team,
        "source_snapshot_id": "server-capture-123",
        "features": {"pace": 70.0},
        "feature_audit": {
            "server_owned": True,
            "timestamp_attestation": "VERIFIED",
            "source_replay_status": "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS",
            "as_of": (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat(),
        },
    }


@pytest.fixture(autouse=True)
def scout(monkeypatch):
    monkeypatch.setattr(
        bridges._base_runtime, "_run_mandatory_scout_research",
        lambda req: {"pass_scout_research_barrier": True},
    )


def test_ncaab_rejects_caller_features_even_when_they_look_complete():
    req = _req()
    req.sport_specific_evidence = {"features": {"pace": 70.0}, "source_snapshot_id": "caller"}
    with pytest.raises(HTTPException) as e:
        bridges.score_ncaab_team_event_request(req, event_api=SimpleNamespace())
    assert e.value.status_code == 422
    assert "NCAAB_CANONICAL_SERVER_HYDRATION_UNAVAILABLE" in e.value.detail["blockers"]
    assert e.value.detail["can_execute"] is False


@pytest.mark.parametrize("mutation", ["event", "team", "clock", "future", "identity", "source", "replay"])
def test_ncaab_rejects_forged_or_late_server_evidence(mutation):
    req = _req()
    evidence = _server_response(req)
    if mutation == "event":
        evidence["official_event_id"] = "NCAAB:wrong"
    elif mutation == "team":
        evidence["away_team"] = "Wrong"
    elif mutation == "clock":
        evidence["feature_audit"]["as_of"] = "2026-11-22T09:00:00"
    elif mutation == "future":
        evidence["feature_audit"]["as_of"] = (
            datetime.now(timezone.utc) + timedelta(hours=5)
        ).isoformat()
    elif mutation == "identity":
        evidence["feature_audit"]["server_owned"] = False
    elif mutation == "source":
        evidence["source_snapshot_id"] = ""
    else:
        evidence["feature_audit"]["source_replay_status"] = "CAPTURED_ONLY"
    with pytest.raises(HTTPException) as e:
        bridges.score_ncaab_team_event_request(
            req, event_api=SimpleNamespace(hydrate_ncaab_features=lambda _req: evidence)
        )
    assert "NCAAB_CANONICAL_SERVER_HYDRATION_UNAVAILABLE" in e.value.detail["blockers"]
    assert e.value.detail["can_execute"] is False


def test_ncaab_verified_server_hydration_reaches_research_only_not_a_fitted_prediction():
    req = _req()
    result = bridges.score_ncaab_team_event_request(
        req, event_api=SimpleNamespace(hydrate_ncaab_features=_server_response)
    )
    assert result["code"] == "MODEL_RESEARCH_ONLY_HOLD"
    assert result["source_snapshot_id"] == "server-capture-123"
    assert result["canonical_hydration_verified"] is True
    assert result["model_invoked"] is False
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
    assert result["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
