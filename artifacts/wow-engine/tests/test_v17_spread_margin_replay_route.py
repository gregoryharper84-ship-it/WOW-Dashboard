from __future__ import annotations

from fastapi import Depends, FastAPI
from pydantic import ValidationError
import pytest

import v17.spread_margin_replay_route as route
from v17.spread_margin_challenger import SpreadChallengerUnavailable
from v17.spread_market_evidence import SpreadMarketEvidenceError


def _success_result():
    return {
        "artifact": {
            "program": "WOW_V17_SPREAD_MARGIN_DISTRIBUTION_CHALLENGER_V1",
            "model_family": "NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
            "feature_schema_version": "NFL_SPREAD_MARGIN_TEAM_STATE_V1",
            "training_dataset_hash": "dataset-hash",
            "train_rows": 600,
            "calibration_rows": 200,
            "test_rows": 200,
            "coefficients": [1.0, 2.0],
            "calibration_residuals": [1.0, -1.0] * 100,
        },
        "receipt": {
            "artifact_checksum": "artifact-hash",
            "historical_row_count": 1000,
            "next_required_stage": "REAL_HISTORICAL_REPLAY_AND_GOVERNED_CERTIFICATION",
            "metrics": {
                "margin_mae": 8.1,
                "margin_rmse": 10.4,
                "three_way_brier": 0.52,
                "cover_brier": 0.25,
                "cover_log_loss": 0.69,
                "cover_ece": 0.03,
                "market_features_used": False,
                "moneyline_probability_used": False,
                "spread_line_used_as_feature": False,
            },
        },
    }


def test_success_receipt_is_compact_shadow_only_and_non_executable(monkeypatch):
    seen = {}

    def fake_run(*, sport, client, min_rows, ridge_alpha):
        seen.update({"sport": sport, "client": client, "min_rows": min_rows, "ridge_alpha": ridge_alpha})
        return _success_result()

    monkeypatch.setattr(route, "run_historical_replay", fake_run)
    db = object()
    payload = route.execute_spread_margin_replay(
        db,
        route.SpreadMarginReplayRequest(sport="NFL", min_rows=300, ridge_alpha=4.0),
    )

    assert seen == {"sport": "NFL", "client": db, "min_rows": 300, "ridge_alpha": 4.0}
    assert payload["status"] == "EXPERIMENT_CREATED"
    assert payload["code"] == "SPREAD_HISTORICAL_REPLAY_COMPLETE"
    assert payload["artifact_checksum"] == "artifact-hash"
    assert payload["historical_row_count"] == 1000
    assert payload["automatic_certification"] is False
    assert payload["automatic_promotion"] is False
    assert payload["probability_publishable"] is False
    assert payload["database_mutated"] is False
    assert payload["production_registry_mutated"] is False
    assert payload["dry_run_only_no_live_trading_no_market_orders"] is True
    assert payload["can_execute"] is False
    assert payload["global_terminal_reducer"] == "V17_TERMINAL_REDUCER"
    assert payload["market_features_used"] is False
    assert payload["moneyline_probability_used"] is False
    assert payload["spread_line_used_as_feature"] is False
    assert "coefficients" not in payload["candidate"]
    assert "calibration_residuals" not in payload["candidate"]


def test_typed_dataset_blocker_is_preserved_not_model_unavailable(monkeypatch):
    def blocked(**_kwargs):
        raise SpreadChallengerUnavailable(
            "SPREAD_REPLAY_DATASET_UNAVAILABLE",
            "NCAAB governed training data unavailable",
        )

    monkeypatch.setattr(route, "run_historical_replay", blocked)
    payload = route.execute_spread_margin_replay(
        object(), route.SpreadMarginReplayRequest(sport="NCAAB")
    )
    assert payload["status"] == "BLOCKED"
    assert payload["code"] == "SPREAD_REPLAY_DATASET_UNAVAILABLE"
    assert payload["code"] != "MODEL_UNAVAILABLE"
    assert payload["probability_publishable"] is False
    assert payload["dry_run_only_no_live_trading_no_market_orders"] is True
    assert payload["can_execute"] is False


def test_unexpected_runtime_failure_keeps_transport_infrastructure_semantics(monkeypatch):
    def broken(**_kwargs):
        raise RuntimeError("database transport failed")

    monkeypatch.setattr(route, "run_historical_replay", broken)
    payload = route.execute_spread_margin_replay(
        object(), route.SpreadMarginReplayRequest(sport="NBA")
    )
    assert payload["status"] == "BLOCKED"
    assert payload["code"] == "SPREAD_REPLAY_RUNTIME_FAILED"
    assert payload["code"] != "MODEL_UNAVAILABLE"
    assert payload["error_type"] == "RuntimeError"
    assert "database transport failed" not in str(payload)
    assert payload["dry_run_only_no_live_trading_no_market_orders"] is True
    assert payload["can_execute"] is False


def test_spread_market_evidence_success_only_mutates_evidence_ledger(monkeypatch):
    monkeypatch.setattr(
        route,
        "collect_spread_snapshot",
        lambda *_args, **_kwargs: {
            "status": "COMPLETE",
            "sport": "NCAAF",
            "sport_key": "americanfootball_ncaaf",
            "slate_date": "2026-09-26",
            "canonical_market_key": "spreads",
            "market_ids": ["2"],
            "book_names": ["Pinnacle", "Draftkings", "Fanduel"],
            "rows_written": 18,
            "datapoints": 18,
            "market_probability_substitution_used": False,
            "spread_line_used_as_feature": False,
            "moneyline_to_spread_conversion_used": False,
            "prediction_authority": False,
            "probability_publishable": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "can_execute": False,
        },
    )
    payload = route.execute_spread_market_evidence_collection(
        object(),
        route.SpreadMarketEvidenceRequest(sport="NCAAF", slate_date="2026-09-26"),
    )
    assert payload["status"] == "EVIDENCE_CAPTURED"
    assert payload["code"] == "SPREAD_MARKET_EVIDENCE_CAPTURED"
    assert payload["rows_written"] == 18
    assert payload["evidence_database_mutated"] is True
    assert payload["production_probability_database_mutated"] is False
    assert payload["production_registry_mutated"] is False
    assert payload["probability_publishable"] is False
    assert payload["spread_line_used_as_feature"] is False
    assert payload["can_execute"] is False


def test_spread_market_evidence_typed_failure_is_preserved(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise SpreadMarketEvidenceError("RUNDOWN_QUOTA_EXHAUSTED", "quota exhausted")

    monkeypatch.setattr(route, "collect_spread_snapshot", blocked)
    payload = route.execute_spread_market_evidence_collection(
        object(), route.SpreadMarketEvidenceRequest(sport="NFL", slate_date="2026-09-27")
    )
    assert payload["status"] == "BLOCKED"
    assert payload["code"] == "RUNDOWN_QUOTA_EXHAUSTED"
    assert payload["code"] != "MODEL_UNAVAILABLE"
    assert payload["evidence_database_mutated"] is False
    assert payload["production_probability_database_mutated"] is False
    assert payload["can_execute"] is False


def test_request_schema_is_closed_and_sport_allowlisted():
    with pytest.raises(ValidationError):
        route.SpreadMarginReplayRequest(sport="MLB")
    with pytest.raises(ValidationError):
        route.SpreadMarginReplayRequest(sport="NFL", unexpected=True)
    with pytest.raises(ValidationError):
        route.SpreadMarketEvidenceRequest(sport="MLB", slate_date="2026-09-27")
    with pytest.raises(ValidationError):
        route.SpreadMarketEvidenceRequest(sport="NFL", slate_date="09/27/2026")


def test_route_installation_is_idempotent_and_has_auth_dependencies():
    app = FastAPI()
    route.install_spread_margin_replay_route(
        app,
        auth_dependency=Depends(lambda: None),
        db_client_fn=lambda: object(),
    )
    route.install_spread_margin_replay_route(
        app,
        auth_dependency=Depends(lambda: None),
        db_client_fn=lambda: object(),
    )
    replay = [r for r in app.router.routes if getattr(r, "path", None) == "/internal/v17/spread-margin-replay"]
    evidence = [r for r in app.router.routes if getattr(r, "path", None) == "/internal/v17/spread-market-evidence"]
    assert len(replay) == 1
    assert replay[0].operation_id == "runWowV17SpreadMarginReplay"
    assert replay[0].dependant.dependencies
    assert len(evidence) == 1
    assert evidence[0].operation_id == "collectWowV17SpreadMarketEvidence"
    assert evidence[0].dependant.dependencies


def test_governance_constants_are_fail_closed():
    assert route.CAN_EXECUTE is False
    assert route.DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS is True
    assert route.AUTOMATIC_CERTIFICATION is False
    assert route.AUTOMATIC_PROMOTION is False
    assert route.PROBABILITY_PUBLISHABLE is False
    assert route.DATABASE_MUTATED is False
    assert route.PRODUCTION_REGISTRY_MUTATED is False
    assert route.GLOBAL_TERMINAL_REDUCER == "V17_TERMINAL_REDUCER"
