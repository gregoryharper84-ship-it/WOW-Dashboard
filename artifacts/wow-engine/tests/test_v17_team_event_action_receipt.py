"""End-to-end Action receipt: a held row through the mounted /score-team-event route."""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from v17 import team_event_request_runtime as request_runtime
from v17.model_evidence_boundary import HOLD
from v17.team_event_bridge_runtime import (
    TEAM_EVENT_BRIDGES,
    register_team_event_bridge,
    score_registered_team_event_request,
)
from v17.team_event_capability_manifest import TEAM_EVENT_INPUT_CONTRACTS
from v17.team_event_official_publication_guard import evaluate_team_event_official_publication
from v17.team_event_request_runtime import install_team_event_routes

MODEL_AT = "2026-11-12T18:30:00+00:00"
EVENT_START = "2026-11-12T19:00:00+00:00"


class _EmptyDb:
    """Governed read store with no fitted rows: absence, never an exception."""

    def table(self, _name: str):
        return self

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, _count: int):
        return self

    def execute(self):
        return _EmptyResult()


class _EmptyResult:
    data: list = []


def _governed_package():
    return {
        "candidate_id": "nfl-candidate-1",
        "calibrated_probability": 0.72,
        "calibrated_lower_bound": 0.66,
        "calibrated_upper_bound": 0.78,
        "immutable_model_timestamp": MODEL_AT,
        "calibration_method": "ISOTONIC",
        "calibration_version": "v1",
        "source_snapshot_id": "snapshot-1",
        "source_snapshot_timestamp": "2026-11-12T18:25:00+00:00",
        "outcome_space": "TWO_WAY_WINNER",
        "model_version": "NFL_EVENT_MODEL_V1",
        "model_family": "TEST_NFL_TEAM_EVENT_FAMILY_V1",
        "feature_schema_version": "TEST_NFL_EVENT_FEATURES_V1",
        "probability_publishable": True,
        "rank_eligible": True,
        "blockers": [],
        "can_execute": False,
    }


def _request_body():
    evidence = {field: f"verified:{field}" for field in TEAM_EVENT_INPUT_CONTRACTS["NFL"]}
    return {
        "requester_host_identity": "WOW_BETTING_ENGINE",
        "research_run_id": "action-receipt-run-1",
        "requested_slate_date": "2026-11-12",
        "requested_timezone": "UTC",
        "candidate_family": "TEAM_EVENT",
        "decision_intent": "WINNER",
        "event_key": "NFL:401000001",
        "official_event_id": "401000001",
        "event_start_time_utc": EVENT_START,
        "sport": "NFL",
        "league": "NFL",
        "settlement_basis": "FULL_GAME",
        "home_team": "Alpha",
        "away_team": "Beta",
        "source_snapshot_id": "snapshot-1",
        "latest_material_update_timestamp": "2026-11-12T18:10:00+00:00",
        "sport_specific_evidence": evidence,
    }


@pytest.fixture(autouse=True)
def isolated_registry():
    original = dict(TEAM_EVENT_BRIDGES)
    TEAM_EVENT_BRIDGES.clear()
    try:
        yield
    finally:
        TEAM_EVENT_BRIDGES.clear()
        TEAM_EVENT_BRIDGES.update(original)


def _mounted_client(monkeypatch):
    register_team_event_bridge(
        "NFL",
        adapter_name="TEST_NFL_ADAPTER",
        controlling_specialist="TEST_NFL_SPECIALIST",
        scorer=lambda *_args, **_kwargs: _governed_package(),
        required_inputs=TEAM_EVENT_INPUT_CONTRACTS["NFL"],
    )
    # The production installer performs this exact assignment; do it directly so
    # the route is exercised without the unrelated health overlay.
    monkeypatch.setattr(
        request_runtime, "score_team_event_request", score_registered_team_event_request
    )
    app = FastAPI()
    install_team_event_routes(app, event_api=_EmptyDb(), auth_dependency=Depends(lambda: None))
    return TestClient(app)


def test_mounted_score_team_event_route_serializes_a_held_receipt(monkeypatch):
    client = _mounted_client(monkeypatch)

    response = client.post("/score-team-event", json=_request_body())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    # The scored numbers reach the Action response; only the publication claim is
    # withheld, and the hold is typed rather than a substituted probability.
    assert body["calibrated_probability"] == 0.72
    assert body["calibrated_lower_bound"] == 0.66
    assert body["probability_publishable"] is False
    assert body["rank_eligible"] is False
    assert body["terminal_label"] == HOLD
    assert body["terminal_ceiling"] == HOLD
    assert body["evidence_triad_required"] is True
    assert body["evidence_triad_status"] == "INCOMPLETE"
    assert "MODEL_EVIDENCE_TRIAD_INCOMPLETE" in body["publication_blockers"]
    assert body["model_evidence_triad"]["bundle_complete"] is False
    assert body["model_evidence_triad"]["report_id"]
    assert body["terminal_reducer_input"]["terminal_output"] == HOLD
    assert body["terminal_reducer_input"]["global_terminal_reducer"] == "V17_TERMINAL_REDUCER"
    assert body["can_execute"] is False
    assert body["feature_consumption_receipt_id"]


def test_the_serialized_action_body_cannot_pass_official_publication(monkeypatch):
    client = _mounted_client(monkeypatch)
    body = client.post("/score-team-event", json=_request_body()).json()

    decision = evaluate_team_event_official_publication(body)

    assert decision["status"] == "HELD"
    assert decision["official_publication_allowed"] is False
    assert "TEAM_EVENT_MODEL_EVIDENCE_TRIAD_NOT_PROVEN" in decision["blockers"]
    assert decision["can_execute"] is False


def test_the_route_never_reaches_an_approving_terminal_when_the_store_is_empty(monkeypatch):
    client = _mounted_client(monkeypatch)

    body = client.post("/score-team-event", json=_request_body()).json()

    assert body["terminal_label"] != "FINAL_APPROVED"
    assert body["terminal_ceiling"] != "FINAL_APPROVED"
    assert body["probability_publishable"] is not True
    assert body["can_execute"] is not True
