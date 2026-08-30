from datetime import datetime, timedelta, timezone
import uuid

from fastapi import HTTPException

import api_prod_market
import pick_request_pipeline
from pick_request_pipeline import PickRequestBatch, run_pick_request_batch
from prop_auto_hydration import PropAutoHydrationError


def _candidate(*, player, with_snapshot):
    payload = {
        "event_id": f"MLB:P0:AUTO:{player}",
        "event_start_time": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "sport": "MLB",
        "player": player,
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 5.5,
        "direction": "MORE",
        "money_lane_status": "PAYOUT_UNRESOLVED",
    }
    if with_snapshot:
        payload["source_snapshot_id"] = str(uuid.uuid4())
    return payload


def test_missing_snapshot_is_auto_hydrated_before_governed_scorer(monkeypatch):
    generated_id = str(uuid.uuid4())
    calls = []
    monkeypatch.setattr(pick_request_pipeline.prod, "get_client", lambda: object())

    def fake_hydrate(req, *, client, board_source, board_capture):
        calls.append((req.source_snapshot_id, board_source, board_capture))
        return {
            "ok": True,
            "code": "PROP_AUTO_HYDRATION_WRITTEN",
            "source_snapshot_id": generated_id,
            "provider": "MLB_STATS_API_OFFICIAL_V1",
            "official_game_pk": 123,
            "starter_status": "STARTER_PROBABLE_OFFICIAL_SCHEDULE",
            "historical_start_count": 10,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "can_execute": False,
        }

    monkeypatch.setattr(pick_request_pipeline, "auto_hydrate_prop_candidate", fake_hydrate)

    batch = PickRequestBatch.model_validate(
        {
            "request_id": "AUTO-1",
            "rows": [
                {
                    "row_id": "r1",
                    "source_type": "SCREENSHOT",
                    "platform": "PrizePicks",
                    "source_capture_timestamp": "2026-08-29T20:00:00+00:00",
                    "candidate": _candidate(player="Pitcher A", with_snapshot=False),
                }
            ],
        }
    )

    def scorer(req, *, x_wow_model_identity=None):
        assert req.source_snapshot_id == generated_id
        return {
            "ok": True,
            "prediction": {"prediction_id": "p1", "terminal_label": "MODEL_QUALIFIED_HOLD"},
            "probability_publishable": True,
            "can_execute": False,
        }

    result = run_pick_request_batch(
        batch=batch,
        score_prop_model=api_prod_market.ScorePropRequest,
        score_prop_callable=scorer,
        model_identity="WOW_BETTING_ENGINE",
    )

    assert result["run_controller_status"] == "COMPLETE"
    assert result["rows_completed"] == 1
    assert result["rows"][0]["preparation"]["auto_hydration_attempted"] is True
    assert result["rows"][0]["preparation"]["auto_hydration_status"] == "PASS"
    assert result["rows"][0]["preparation"]["source_snapshot_id"] == generated_id
    assert result["telemetry"]["auto_hydration_attempted"] == 1
    assert result["telemetry"]["auto_hydration_succeeded"] == 1
    assert calls[0][1] == "PrizePicks"
    assert calls[0][2] == "2026-08-29T20:00:00+00:00"
    assert result["can_execute"] is False


def test_auto_hydration_failure_is_row_local_and_other_row_completes(monkeypatch):
    monkeypatch.setattr(pick_request_pipeline.prod, "get_client", lambda: object())

    def fake_hydrate(req, *, client, board_source, board_capture):
        raise PropAutoHydrationError(
            "PROP_AUTO_HYDRATION_PROVIDER_UNAVAILABLE",
            "official source unavailable",
            detail={"source": "MLB_STATS_API_OFFICIAL_V1"},
        )

    monkeypatch.setattr(pick_request_pipeline, "auto_hydrate_prop_candidate", fake_hydrate)

    batch = PickRequestBatch.model_validate(
        {
            "request_id": "AUTO-DEGRADED",
            "rows": [
                {"row_id": "auto-fails", "source_type": "SCREENSHOT", "candidate": _candidate(player="Pitcher A", with_snapshot=False)},
                {"row_id": "existing", "source_type": "NORMALIZED", "candidate": _candidate(player="Pitcher B", with_snapshot=True)},
            ],
        }
    )

    def scorer(req, *, x_wow_model_identity=None):
        if req.player == "Pitcher A":
            raise AssertionError("failed hydration row must never reach scorer")
        return {"ok": True, "prediction": {"prediction_id": "p2"}, "probability_publishable": True, "can_execute": False}

    result = run_pick_request_batch(
        batch=batch,
        score_prop_model=api_prod_market.ScorePropRequest,
        score_prop_callable=scorer,
        model_identity=None,
    )

    assert result["run_controller_status"] == "DEGRADED"
    assert result["rows_in"] == 2
    assert result["rows_completed"] == 1
    assert result["rows_held"] == 1
    assert result["rows_rejected"] == 0
    assert result["rows"][0]["termination_code"] == "PROP_AUTO_HYDRATION_PROVIDER_UNAVAILABLE"
    assert result["rows"][1]["row_bucket"] == "COMPLETED"
    assert result["reconciliation"]["passed"] is True
    assert result["telemetry"]["false_global_failure_count"] == 0
    assert result["can_execute"] is False


def test_invalid_candidate_is_rejected_before_auto_hydration(monkeypatch):
    called = {"hydrate": False}

    def should_not_hydrate(*args, **kwargs):
        called["hydrate"] = True
        raise AssertionError("invalid normalized row must not call external acquisition")

    monkeypatch.setattr(pick_request_pipeline, "auto_hydrate_prop_candidate", should_not_hydrate)
    batch = PickRequestBatch.model_validate(
        {"rows": [{"row_id": "bad", "source_type": "PDF", "candidate": {"sport": "MLB", "player": "Missing Everything"}}]}
    )

    result = run_pick_request_batch(
        batch=batch,
        score_prop_model=api_prod_market.ScorePropRequest,
        score_prop_callable=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not score")),
        model_identity=None,
    )

    assert result["run_controller_status"] == "BLOCKED"
    assert result["rows_rejected"] == 1
    assert result["rows"][0]["termination_code"] == "CANDIDATE_NORMALIZATION_INVALID"
    assert called["hydrate"] is False
    assert result["can_execute"] is False
