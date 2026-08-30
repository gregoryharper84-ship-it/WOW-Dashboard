from datetime import datetime, timedelta, timezone
import uuid

from fastapi import HTTPException

import api_prod_market
from pick_request_pipeline import PickRequestBatch, run_pick_request_batch


def _candidate(*, event_id: str, player: str):
    return {
        "event_id": event_id,
        "event_start_time": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "sport": "WNBA",
        "player": player,
        "stat_type": "REB",
        "line": 10.5,
        "direction": "MORE",
        "source_snapshot_id": str(uuid.uuid4()),
        "money_lane_status": "PAYOUT_UNRESOLVED",
    }


def _batch():
    return PickRequestBatch.model_validate(
        {
            "request_id": "P0-BATCH-1",
            "rows": [
                {
                    "row_id": "row-complete",
                    "source_type": "SCREENSHOT",
                    "platform": "PrizePicks",
                    "league": "WNBA",
                    "opponent": "Opponent A",
                    "settlement_operator": "PrizePicks",
                    "candidate": _candidate(event_id="WNBA:P0:1", player="Player A"),
                },
                {
                    "row_id": "row-held",
                    "source_type": "AUTONOMOUS_DISCOVERY",
                    "platform": "PrizePicks",
                    "league": "WNBA",
                    "opponent": "Opponent B",
                    "settlement_operator": "PrizePicks",
                    "candidate": _candidate(event_id="WNBA:P0:2", player="Player B"),
                },
                {
                    "row_id": "row-rejected",
                    "source_type": "PDF",
                    "platform": "PrizePicks",
                    "league": "WNBA",
                    "opponent": "Opponent C",
                    "settlement_operator": "PrizePicks",
                    "candidate": _candidate(event_id="WNBA:P0:3", player="Player C"),
                },
            ],
        }
    )


def test_batch_contains_row_local_failures_and_reconciles_exactly_once():
    def scorer(req, *, x_wow_model_identity=None):
        if req.player == "Player B":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "MODEL_UNAVAILABLE",
                    "blocker_code": "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
                    "evidence_hydration": "NOT_ATTEMPTED_ROUTE_BLOCKED",
                    "probability_publishable": False,
                    "can_execute": False,
                },
            )
        if req.player == "Player C":
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                    "failure_class": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                    "probability_publishable": False,
                    "can_execute": False,
                },
            )
        return {
            "ok": True,
            "prediction": {"prediction_id": "pred-a", "terminal_label": "MODEL_QUALIFIED_HOLD"},
            "probability_publishable": True,
            "can_execute": False,
        }

    result = run_pick_request_batch(
        batch=_batch(),
        score_prop_model=api_prod_market.ScorePropRequest,
        score_prop_callable=scorer,
        model_identity="WOW_BETTING_ENGINE",
    )

    assert result["run_controller_status"] == "DEGRADED"
    assert result["rows_in"] == 3
    assert result["rows_completed"] == 1
    assert result["rows_held"] == 1
    assert result["rows_rejected"] == 1
    assert result["rows_terminal"] == 3
    assert result["reconciliation"]["passed"] is True
    assert result["telemetry"]["route_preflight_blocked"] == 1
    assert result["telemetry"]["evidence_hydration_not_attempted_route_blocked"] == 1
    assert result["telemetry"]["acquisition_failures"] == 1
    assert result["telemetry"]["model_completed"] == 1
    assert result["telemetry"]["false_global_failure_count"] == 0
    assert [row["row_id"] for row in result["rows"]] == [
        "row-complete",
        "row-held",
        "row-rejected",
    ]
    assert all(row["can_execute"] is False for row in result["rows"])
    assert result["can_execute"] is False


def test_all_unavailable_rows_return_blocked_without_global_exception():
    batch = PickRequestBatch.model_validate(
        {
            "request_id": "P0-BATCH-BLOCKED",
            "rows": [
                {
                    "row_id": "r1",
                    "source_type": "SCREENSHOT",
                    "candidate": _candidate(event_id="WNBA:P0:B1", player="Player B1"),
                },
                {
                    "row_id": "r2",
                    "source_type": "AUTONOMOUS_DISCOVERY",
                    "candidate": _candidate(event_id="WNBA:P0:B2", player="Player B2"),
                },
            ],
        }
    )

    def unavailable(_req, *, x_wow_model_identity=None):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MODEL_UNAVAILABLE",
                "evidence_hydration": "NOT_ATTEMPTED_ROUTE_BLOCKED",
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    result = run_pick_request_batch(
        batch=batch,
        score_prop_model=api_prod_market.ScorePropRequest,
        score_prop_callable=unavailable,
        model_identity="WOW_BETTING_ENGINE",
    )

    assert result["ok"] is False
    assert result["run_controller_status"] == "BLOCKED"
    assert result["rows_in"] == 2
    assert result["rows_completed"] == 0
    assert result["rows_held"] == 2
    assert result["rows_rejected"] == 0
    assert result["reconciliation"]["passed"] is True
    assert result["telemetry"]["false_global_failure_count"] == 0


def test_invalid_candidate_is_rejected_without_preventing_valid_row():
    batch = PickRequestBatch.model_validate(
        {
            "request_id": "P0-NORMALIZATION",
            "rows": [
                {
                    "row_id": "invalid",
                    "source_type": "PDF",
                    "candidate": {"sport": "WNBA"},
                },
                {
                    "row_id": "valid",
                    "source_type": "SCREENSHOT",
                    "candidate": _candidate(event_id="WNBA:P0:VALID", player="Player Valid"),
                },
            ],
        }
    )

    def scorer(_req, *, x_wow_model_identity=None):
        return {
            "ok": True,
            "prediction": {"prediction_id": "pred-valid"},
            "probability_publishable": True,
            "can_execute": False,
        }

    result = run_pick_request_batch(
        batch=batch,
        score_prop_model=api_prod_market.ScorePropRequest,
        score_prop_callable=scorer,
        model_identity="WOW_BETTING_ENGINE",
    )

    assert result["run_controller_status"] == "DEGRADED"
    assert result["rows_completed"] == 1
    assert result["rows_rejected"] == 1
    assert result["rows"][0]["termination_code"] == "CANDIDATE_NORMALIZATION_INVALID"
    assert result["rows"][1]["row_bucket"] == "COMPLETED"
    assert result["reconciliation"]["passed"] is True


def test_canonical_key_is_source_agnostic_for_same_normalized_candidate():
    candidate = _candidate(event_id="WNBA:P0:KEY", player="Player Key")
    batch = PickRequestBatch.model_validate(
        {
            "rows": [
                {
                    "row_id": "upload",
                    "source_type": "SCREENSHOT",
                    "league": "WNBA",
                    "opponent": "Opponent",
                    "settlement_operator": "PrizePicks",
                    "candidate": candidate,
                },
                {
                    "row_id": "discover",
                    "source_type": "AUTONOMOUS_DISCOVERY",
                    "league": "WNBA",
                    "opponent": "Opponent",
                    "settlement_operator": "PrizePicks",
                    "candidate": candidate,
                },
            ]
        }
    )

    def scorer(_req, *, x_wow_model_identity=None):
        return {"ok": True, "prediction": {"prediction_id": "p"}, "probability_publishable": True, "can_execute": False}

    result = run_pick_request_batch(
        batch=batch,
        score_prop_model=api_prod_market.ScorePropRequest,
        score_prop_callable=scorer,
        model_identity=None,
    )

    assert result["rows"][0]["canonical_key"] == result["rows"][1]["canonical_key"]
    assert result["rows"][0]["source_type"] != result["rows"][1]["source_type"]
    assert result["reconciliation"]["passed"] is True
