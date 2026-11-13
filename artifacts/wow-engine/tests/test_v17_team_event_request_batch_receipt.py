"""End-to-end batch receipt: a held row through the mounted /score-team-event-request ingress."""
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
from team_event_request_runtime import install_team_event_request_routes

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

    def limit(self, *_count: int):
        return self

    def execute(self):
        return _EmptyResult()


class _EmptyResult:
    data: list = []


def _governed_package() -> dict:
    return {
        "candidate_id": "nfl-candidate-batch-1",
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


def _row(**overrides):
    row = {
        "research_run_id": "batch-receipt-run-1",
        "objective_lane": "OUTRIGHT_WIN_PROBABILITY",
        "sport": "NFL",
        "league": "NFL",
        "event_key": "NFL:401000001",
        "event_state": "PREGAME",
        "event_date": "2026-11-12",
        "timezone": "UTC",
        "price_required_for_objective": False,
        "event_start_time_utc": EVENT_START,
        "home_team": "Alpha",
        "away_team": "Beta",
        "source_snapshot_id": "snapshot-1",
        "latest_material_update_timestamp": "2026-11-12T18:10:00+00:00",
        "sport_specific_evidence": {
            field: f"verified:{field}" for field in TEAM_EVENT_INPUT_CONTRACTS["NFL"]
        },
    }
    row.update(overrides)
    return row


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
        adapter_name="TEST_NFL_BATCH_ADAPTER",
        controlling_specialist="TEST_NFL_SPECIALIST",
        scorer=lambda *_args, **_kwargs: _governed_package(),
        required_inputs=TEAM_EVENT_INPUT_CONTRACTS["NFL"],
    )
    # The production bridge installer performs this exact assignment; do it
    # directly so the batch ingress is exercised without the health overlay.
    monkeypatch.setattr(
        request_runtime, "score_team_event_request", score_registered_team_event_request
    )
    app = FastAPI()
    db = _EmptyDb()
    install_team_event_request_routes(
        app,
        auth_dependency=Depends(lambda: None),
        db_client_fn=lambda: db,
        event_api=db,
    )
    return TestClient(app)


def _post(monkeypatch, rows):
    response = _mounted_client(monkeypatch).post(
        "/score-team-event-request", json={"rows": rows}
    )
    return response


def test_mounted_batch_route_serializes_a_held_receipt(monkeypatch):
    response = _post(monkeypatch, [_row()])

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    row = body["rows"][0]
    # The batch boundary holds the row instead of promoting it.
    assert row["terminal_status"] == "HELD"
    assert row["probability_publishable"] is False
    assert row["rank_eligible"] is False
    assert row["calibrated_probability"] is None
    assert row["internal_ceiling"] != "FULL_MODEL_PROBABILITY"
    # The scored numbers and the typed triad hold survive inside the row detail.
    detail = row["detail"]
    assert detail["calibrated_probability"] == 0.72
    assert detail["calibrated_lower_bound"] == 0.66
    assert detail["evidence_triad_required"] is True
    assert detail["evidence_triad_status"] == "INCOMPLETE"
    assert "MODEL_EVIDENCE_TRIAD_INCOMPLETE" in detail["publication_blockers"]
    assert detail["model_evidence_triad"]["report_id"]
    assert detail["terminal_reducer_input"]["terminal_output"] == HOLD
    assert detail["terminal_reducer_input"]["global_terminal_reducer"] == "V17_TERMINAL_REDUCER"
    # Batch reconciliation and card policy see the hold, not a completed row.
    assert body["rows_in"] == 1
    assert body["rows_completed"] == 0
    assert body["rows_held"] == 1
    assert body["run_status"] == "BLOCKED"
    assert body["rows_card_admissible"] == 0
    assert body["card_pool_status"] == "NONE_QUALIFIED"
    assert body["reconciliation_pass"] is True
    assert body["can_execute"] is False
    assert row["card_admission_eligible"] is False
    assert row["can_execute"] is False


def test_batch_reconciliation_counts_the_held_rows(monkeypatch):
    body = _post(
        monkeypatch,
        [_row(research_run_id="a"), _row(research_run_id="b", event_key="NFL:401000002")],
    ).json()

    assert body["rows_in"] == 2
    assert body["rows_completed"] + body["rows_held"] == body["rows_in"]
    assert body["rows_held"] == 2
    assert body["rows_completed"] == 0
    assert [row["terminal_status"] for row in body["rows"]] == ["HELD", "HELD"]
    assert body["rows_card_admissible"] == 0
    assert body["can_execute"] is False


def test_batch_row_never_reports_an_approving_terminal_when_the_store_is_empty(monkeypatch):
    row = _post(monkeypatch, [_row()]).json()["rows"][0]

    assert row["terminal_status"] != "COMPLETED"
    assert row["detail"]["terminal_label"] != "FINAL_APPROVED"
    assert row["detail"]["terminal_label"] == HOLD
    assert row["detail"]["terminal_ceiling"] == HOLD
    assert row["detail"]["probability_publishable"] is not True
    assert row["can_execute"] is not True
