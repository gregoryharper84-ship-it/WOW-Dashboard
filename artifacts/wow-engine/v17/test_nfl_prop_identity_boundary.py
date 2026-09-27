from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import pick_request_runtime_core as pick_runtime
from v17 import nfl_prop_identity_boundary as subject


def _row(*, player: str, event_id: str, row_key: str | None = None):
    return {
        "row_key": row_key,
        "event_id": event_id,
        "event_start_time": "2026-09-27T17:00:00+00:00",
        "sport": "NFL",
        "player": player,
        "stat_type": "PASS_YARDS",
        "line": 223.5,
        "direction": "MORE",
        "opponent": "CLE",
        "source_type": "SCREENSHOT",
    }


class _Market:
    pass


def _completed(row_key: str) -> dict:
    return {
        "row_key": row_key,
        "terminal_status": "COMPLETED",
        "code": "MODEL_QUALIFIED_HOLD",
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "model_evaluated": True,
        "pick_rejected": False,
        "infrastructure_blocked": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def test_unresolved_display_id_is_not_scored_and_resolved_sibling_continues(monkeypatch):
    app = FastAPI()
    scored_row_keys: list[str] = []

    @app.post("/score-pick-request", operation_id="scoreWowPickRequest")
    def score_pick_request(batch: pick_runtime.PickRequestBatch):
        scored_row_keys.extend(str(row.row_key) for row in batch.rows)
        outcomes = [_completed(str(row.row_key)) for row in batch.rows]
        return {
            "ok": bool(outcomes),
            "request_id": batch.request_id,
            "run_controller_status": "COMPLETE",
            "rows_in": len(outcomes),
            "rows_completed": len(outcomes),
            "rows_held": 0,
            "rows_rejected": 0,
            "reconciliation_pass": True,
            "response_mode": batch.response_mode,
            "rows": outcomes,
            "telemetry": {},
            "specialist_utilization_summary": {},
            "probability_objective": "GOVERNED_MODEL_ONLY",
            "can_execute": False,
        }

    def resolve(batch, *, market_api):
        rows = list(batch.rows)
        rows[1] = rows[1].model_copy(update={"event_id": "2026_02_CAR_CLE"})
        prepared = batch.model_copy(update={"rows": rows})
        return prepared, {
            "row-1": {
                "status": "BLOCKED",
                "code": "PROP_EVENT_IDENTITY_UNRESOLVED",
                "source_event_id_alias": rows[0].event_id,
                "detail": {"provider": "ESPN", "reason": "NO_VERIFIED_CANONICAL_ID"},
                "can_execute": False,
            },
            "row-2": {
                "status": "PASS",
                "canonical_event_id": "2026_02_CAR_CLE",
                "source_event_id_alias": rows[1].event_id,
                "can_execute": False,
            },
        }

    monkeypatch.setattr(subject, "_resolve_batch_nfl_identity", resolve)
    monkeypatch.setattr(subject, "enforce_top10_completion", lambda response, _rows: response)
    assert subject.install_nfl_prop_identity_boundary(app, market_api=_Market()) is True

    with TestClient(app) as client:
        response = client.post(
            "/score-pick-request",
            json={
                "request_id": "identity-mixed",
                "response_mode": "FULL",
                "rows": [
                    _row(player="Bryce Young", event_id="display-uuid-1"),
                    _row(player="Other QB", event_id="display-uuid-2"),
                ],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert scored_row_keys == ["row-2"]
    assert payload["rows_in"] == 2
    assert payload["rows_completed"] == 1
    assert payload["rows_held"] == 1
    assert payload["rows_rejected"] == 0
    assert payload["reconciliation_pass"] is True
    assert [row["row_key"] for row in payload["rows"]] == ["row-1", "row-2"]

    blocked = payload["rows"][0]
    assert blocked["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert blocked["detail"]["blocker"] == "PROP_EVENT_IDENTITY_UNRESOLVED"
    assert blocked["detail"]["model_evaluated"] is False
    assert blocked["detail"]["specialist_scoring_attempted"] is False
    assert blocked["detail"]["scoring_attempted"] is False
    assert blocked["detail"]["specialist_invoked"] is False
    assert blocked["detail"]["prediction_id"] is None
    assert blocked["detail"]["source_snapshot_id"] is None
    assert payload["nfl_event_identity_resolution"]["rows_blocked_before_scoring"] == 1
    assert payload["can_execute"] is False


def test_all_unresolved_rows_never_invoke_captured_scorer(monkeypatch):
    app = FastAPI()
    scorer_called = False

    @app.post("/score-pick-request", operation_id="scoreWowPickRequest")
    def score_pick_request(_batch: pick_runtime.PickRequestBatch):
        nonlocal scorer_called
        scorer_called = True
        raise AssertionError("scorer must not run when every NFL identity is unresolved")

    def resolve(batch, *, market_api):
        receipts = {
            str(row.row_key): {
                "status": "BLOCKED",
                "code": "PROP_EVENT_IDENTITY_UNRESOLVED",
                "source_event_id_alias": row.event_id,
                "detail": {"provider": "ESPN", "reason": "NO_VERIFIED_CANONICAL_ID"},
                "can_execute": False,
            }
            for row in batch.rows
        }
        return batch, receipts

    monkeypatch.setattr(subject, "_resolve_batch_nfl_identity", resolve)
    monkeypatch.setattr(subject, "enforce_top10_completion", lambda response, _rows: response)
    assert subject.install_nfl_prop_identity_boundary(app, market_api=_Market()) is True

    with TestClient(app) as client:
        response = client.post(
            "/score-pick-request",
            json={
                "request_id": "identity-all-blocked",
                "response_mode": "COMPACT",
                "rows": [
                    _row(player="Bryce Young", event_id="display-uuid-1"),
                    _row(player="Other QB", event_id="display-uuid-2"),
                ],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert scorer_called is False
    assert payload["run_controller_status"] == "BLOCKED"
    assert payload["rows_in"] == 2
    assert payload["rows_completed"] == 0
    assert payload["rows_held"] == 2
    assert payload["reconciliation_pass"] is True
    assert all(row["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE" for row in payload["rows"])
    assert all(row["blocker"] == "PROP_EVENT_IDENTITY_UNRESOLVED" for row in payload["rows"])
    assert all(row["model_evaluated"] is False for row in payload["rows"])
    assert payload["can_execute"] is False
