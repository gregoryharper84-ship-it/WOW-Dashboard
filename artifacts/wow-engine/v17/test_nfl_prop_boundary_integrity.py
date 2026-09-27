from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import pick_request_runtime_core as pick_core
from prop_auto_hydration import PropAutoHydrationError
from v17 import nfl_prop_boundary_integrity as subject
from v17 import prediction_receipt_lookup_runtime as receipt_runtime


CANONICAL = "2026_02_CAR_CLE"


def _row(**overrides):
    payload = {
        "row_key": "bryce-pass",
        "event_id": "f4b8d6d5-9f7c-4e6b-8e2d-display-only",
        "event_start_time": "2026-09-27T17:00:00+00:00",
        "sport": "NFL",
        "player": "Bryce Young",
        "stat_type": "PASS_YARDS",
        "line": 223.5,
        "direction": "MORE",
        "opponent": "CLE",
    }
    payload.update(overrides)
    return pick_core.PickRequestRow.model_validate(payload)


class _BaseApi:
    def __init__(self, specialist="WOW_NFL_PROP_SPECIALIST@v1"):
        self.specialist = specialist

    def _controlling_specialist_provider(self, _sport, _stat):
        return {"controlling_specialist": self.specialist}


class _Prod:
    PROP_CAPABILITY_KEY = "PROP_PROBABILITY"

    def __init__(self, specialist="WOW_NFL_PROP_SPECIALIST@v1"):
        self.base_api = _BaseApi(specialist)

    @staticmethod
    def _runtime_capability(_key):
        return {"capability_status": "AVAILABLE", "evidence": {}}


class _Market:
    def __init__(self, specialist="WOW_NFL_PROP_SPECIALIST@v1", artifact_ready=True):
        self.prod = _Prod(specialist)
        self.artifact_ready = artifact_ready

    def _prop_route_artifact(self, _sport, _stat):
        if self.artifact_ready:
            return {
                "ok": True,
                "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
                "model_artifact_version": "NFL_PROP_TEST",
                "specialist_version": "WOW_NFL_PROP_SPECIALIST@v1",
            }
        return {"ok": False, "code": "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"}


def test_display_uuid_is_rewritten_only_from_verified_canonical_identity(monkeypatch):
    monkeypatch.setattr(subject, "prop_lane_capability", lambda *_args, **_kwargs: {"scoreable": True})
    monkeypatch.setattr(
        subject,
        "resolve_nfl_event_identity",
        lambda **kwargs: {
            "status": "PASS",
            "canonical_event_id": CANONICAL,
            "official_event_id": CANONICAL,
            "provider_event_ids": {"ESPN": "401999001"},
            "source_event_id_alias": kwargs["current_event_id"],
            "identity_binding_status": "VERIFIED_PROVIDER_TO_CANONICAL",
            "can_execute": False,
        },
    )
    batch = pick_core.PickRequestBatch(rows=[_row()])

    prepared, receipts = subject._resolve_batch_nfl_identity(batch, market_api=_Market())

    assert prepared.rows[0].event_id == CANONICAL
    assert batch.rows[0].event_id != CANONICAL
    assert receipts["bryce-pass"]["source_event_id_alias"] == batch.rows[0].event_id
    assert receipts["bryce-pass"]["canonical_event_id"] == CANONICAL
    assert receipts["bryce-pass"]["can_execute"] is False


def test_unsupported_exact_lane_is_not_identity_rewritten(monkeypatch):
    called = False

    def should_not_resolve(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("identity resolver must not run for a route-blocked stat")

    monkeypatch.setattr(subject, "resolve_nfl_event_identity", should_not_resolve)
    market = _Market(specialist="MODEL_UNAVAILABLE")
    batch = pick_core.PickRequestBatch(rows=[_row()])

    prepared, receipts = subject._resolve_batch_nfl_identity(batch, market_api=market)

    assert called is False
    assert prepared.rows[0].event_id == batch.rows[0].event_id
    assert receipts["bryce-pass"]["status"] == "SKIPPED_EXACT_LANE_UNAVAILABLE"


def test_lane_capability_does_not_treat_global_prop_availability_as_nfl_stat_authority():
    result = subject.prop_lane_capability(
        _Market(specialist="MODEL_UNAVAILABLE"),
        "NFL",
        "PASS_YARDS",
    )

    assert result["aggregate_prop_capability"] == "AVAILABLE"
    assert result["canonical_stat_type"] == "PASSING_YARDS"
    assert result["scoreable"] is False
    assert result["code"] == "MODEL_UNAVAILABLE"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_provider_alias_conflict_is_typed_identity_unresolved(monkeypatch):
    monkeypatch.setattr(subject.nfl, "_aware", lambda value: SimpleNamespace(isoformat=lambda: value))
    monkeypatch.setattr(subject.nfl, "_resolve_espn_athlete", lambda *_args, **_kwargs: ("42", "Bryce Young"))
    monkeypatch.setattr(subject.nfl, "_athlete_team", lambda *_args, **_kwargs: "CAR")
    monkeypatch.setattr(
        subject.nfl,
        "_target_event",
        lambda **_kwargs: {
            "event_id": "401999001",
            "opponent": "CLE",
            "provider_season": 2026,
            "provider_week": 2,
            "canonical_home_team": "CLE",
            "canonical_away_team": "CAR",
            "verified_canonical_event_id": CANONICAL,
        },
    )

    with pytest.raises(PropAutoHydrationError) as raised:
        subject.resolve_nfl_event_identity(
            player="Bryce Young",
            event_start_time="2026-09-27T17:00:00+00:00",
            opponent="CLE",
            provider_event_id="401WRONG",
            current_event_id="display-uuid",
        )

    assert raised.value.code == "PROP_EVENT_IDENTITY_UNRESOLVED"
    assert raised.value.detail["verified_provider_event_id"] == "401999001"


def test_parallel_http_exception_preserves_acquisition_blocker_without_false_scorer_attempt():
    outcome = subject._typed_parallel_failure(
        _row(),
        0,
        HTTPException(
            status_code=409,
            detail={
                "code": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                "blocker": "PROP_EVENT_IDENTITY_UNRESOLVED",
                "specialist_scoring_attempted": False,
                "scoring_attempted": False,
                "specialist_invoked": False,
            },
        ),
    )

    assert outcome["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert outcome["detail"]["blocker"] == "PROP_EVENT_IDENTITY_UNRESOLVED"
    assert outcome["detail"]["specialist_scoring_attempted"] is False
    assert outcome["detail"]["scoring_attempted"] is False
    assert outcome["detail"]["specialist_invoked"] is False
    assert outcome["model_evaluated"] is False
    assert outcome["can_execute"] is False


def test_unscored_durable_row_returns_no_prediction_created_without_prediction_query(monkeypatch):
    monkeypatch.setattr(receipt_runtime, "lookup_prediction_receipts", receipt_runtime.lookup_prediction_receipts)
    monkeypatch.setattr(receipt_runtime, "_wow_no_prediction_receipt_semantics", False, raising=False)
    monkeypatch.setattr(
        receipt_runtime,
        "_durable_recovery_outcome",
        lambda *_args, **_kwargs: {
            "row_key": "bryce-pass",
            "status": "DURABLE_TERMINAL",
            "code": "DURABLE_TERMINAL_NO_PREDICTION_RECEIPT",
            "matches": [],
            "retry_allowed": False,
            "durable_row_state": {
                "row_key": "bryce-pass",
                "current_stage": "INGESTED",
                "terminal_code": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                "failure_domain": "ACQUISITION",
                "model_evaluated": False,
                "prediction_id": None,
                "can_execute": False,
            },
            "can_execute": False,
        },
    )

    def prediction_query_must_not_run(*_args, **_kwargs):
        raise AssertionError("prediction ledger must not be queried for known pre-scorer terminal row")

    monkeypatch.setattr(subject, "_ORIGINAL_RECEIPT_LOOKUP", prediction_query_must_not_run)
    subject._install_receipt_semantics()

    batch = receipt_runtime.PredictionReceiptLookupBatch.model_validate(
        {
            "request_id": "debug-nfl-identity",
            "rows": [
                {
                    "row_key": "bryce-pass",
                    "event_id": "display-uuid",
                    "sport": "NFL",
                    "player": "Bryce Young",
                    "stat_type": "PASSING_YARDS",
                    "line": 223.5,
                    "direction": "MORE",
                }
            ],
        }
    )
    result = receipt_runtime.lookup_prediction_receipts(object(), batch)

    assert result["reconciliation_pass"] is True
    assert result["rows_durable_terminal"] == 1
    assert result["rows"][0]["code"] == "NO_PREDICTION_CREATED"
    assert result["rows"][0]["prediction_created"] is False
    assert result["rows"][0]["prediction_id"] is None
    assert result["rows"][0]["detail"]["current_stage"] == "INGESTED"
    assert result["can_execute"] is False
