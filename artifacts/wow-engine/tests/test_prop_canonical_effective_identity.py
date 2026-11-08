"""End-to-end regression for canonical effective scoring identity.

Two equivalent ``/score-prop`` requests are submitted through the real FastAPI
route. One is canonical; the other uses the governed MLB ``K`` alias, padded
event/player text, and a different requested snapshot id. The test exercises
the actual evidence-repair fallback, the real discrete scoring engine and the
real ledger idempotency constructor. It fails if equivalent requests can
produce different evidence, market or prediction identities, or a duplicate
prediction on retry.

The caller request is also asserted to remain visible for audit even when its
raw spelling is not canonical.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

TEST_KEY = "test-g11-action-key"
os.environ["WOW_ACTION_API_KEY"] = TEST_KEY

import api_prod_market
import ledger
import prop_discrete_engine
from calibration import CalibrationStatus
from prop_discrete_engine import (
    PropCalibrationOutput,
    register_prop_calibration_adapter,
    score_discrete_prop_end_to_end as real_score_discrete_prop_end_to_end,
)
from prop_distribution_contract import (
    CertifiedBundle,
    CoverageDecision,
    RawDiscreteDistribution,
)
from prop_fitted_provider import CertifiedInference, ResolvedArtifact


AUTH = {"Authorization": f"Bearer {TEST_KEY}"}
CLIENT = TestClient(api_prod_market.app)

EVENT_ID = "MLB:2026-11-09:NYY-BOS"
PLAYER = "Test Pitcher"
STAT = "PITCHER_STRIKEOUTS"
SNAPSHOT_ID = "11111111-1111-4111-8111-111111111111"
OTHER_SNAPSHOT_ID = "22222222-2222-4222-8222-222222222222"
CALIBRATOR_VERSION = "CANONICAL_EFFECTIVE_IDENTITY_CAL_V1"

_BUNDLE = CertifiedBundle(
    model_artifact_version="MLB_SO_CANONICAL_IDENTITY_TEST_V1",
    calibrator_version=CALIBRATOR_VERSION,
    feature_transform_version="PROP_TRANSFORM_V1",
    specialist_version="wow.mlb-pitcher-strikeouts-expert@canonical-test",
    certification_id="CERT-CANONICAL-IDENTITY-TEST",
    feature_schema_version="PROP_FEATURES_V1",
    training_dataset_hash="a" * 64,
    training_code_sha="b" * 40,
    artifact_checksum="c" * 64,
    lifecycle_state="PROSPECTIVE_CERTIFIED",
    supported_sport="MLB",
    supported_stat_type=STAT,
    supported_line_min=0.0,
    supported_line_max=15.0,
)
_ARTIFACT = ResolvedArtifact(
    artifact_id="33333333-3333-4333-8333-333333333333",
    model_family="MLB_SO_CANONICAL_IDENTITY_TEST",
    artifact_format="TEST_ONLY",
    artifact_payload={},
    training_rows=600,
    validation_metrics={"brier": 0.18},
    bundle=_BUNDLE,
)
_INFERENCE = CertifiedInference(
    artifact=_ARTIFACT,
    distribution=RawDiscreteDistribution(
        support={3: 0.10, 4: 0.20, 5: 0.20, 6: 0.25, 7: 0.15, 8: 0.10},
        coverage=CoverageDecision(True, 0.10, ()),
        model_artifact_version=_BUNDLE.model_artifact_version,
        training_code_sha=_BUNDLE.training_code_sha,
        training_dataset_hash=_BUNDLE.training_dataset_hash,
        feature_schema_version=_BUNDLE.feature_schema_version,
        feature_transform_sha="d" * 64,
        feature_snapshot_hash="e" * 64,
        artifact_checksum=_BUNDLE.artifact_checksum,
        inference_timestamp="2026-11-08T12:00:01+00:00",
    ),
)


def _calibrator(_inference, raw_probability, _line_probs, _features, _seed):
    """Return deterministic calibrator output for the test bundle."""
    assert raw_probability == pytest.approx(0.50)
    return PropCalibrationOutput(
        calibration_status=CalibrationStatus.PLATT_TIME_SPLIT_V1,
        calibration_method=CalibrationStatus.PLATT_TIME_SPLIT_V1,
        calibrated_probability=0.56,
        lower_bound=0.50,
        upper_bound=0.62,
        bounds_method_version="PREDICTIVE_BOUNDS_V1",
        effective_sample_size=9.5,
    )


@pytest.fixture(autouse=True)
def _registered_calibration_adapter():
    """Register this module's calibrator per test and restore prior state.

    Other test modules clear the process-global adapter registry. Registering
    at import time therefore makes this test pass alone but fail in a shard.
    """
    registry = prop_discrete_engine._CALIBRATION_ADAPTERS
    previous = dict(registry)
    register_prop_calibration_adapter(CALIBRATOR_VERSION, _calibrator)
    try:
        yield
    finally:
        registry.clear()
        registry.update(previous)


def _infer(_client, *, request, line, features):
    """Assert the canonical inference request and return the test bundle."""
    assert request.event_id == EVENT_ID
    assert request.sport == "MLB"
    assert request.stat_type == STAT
    assert request.market_identity_id.startswith("wow-market:")
    assert request.evidence_snapshot_id == SNAPSHOT_ID
    assert line == 5.5
    assert features["game_log"]
    return _INFERENCE


def _evidence():
    """Build a complete, immutable evidence snapshot."""
    now = datetime.now(timezone.utc)
    return {
        "ok": True,
        "code": "PROP_EVIDENCE_READY",
        "hydration_status": "PASS",
        "source_snapshot_id": SNAPSHOT_ID,
        "event_id": EVENT_ID,
        "sport": "MLB",
        "player": PLAYER,
        "stat_type": STAT,
        "line": 5.5,
        "captured_at": (now - timedelta(hours=2)).isoformat(),
        "game_log": [5, 6, 4, 7, 5, 6, 3, 5, 6, 4],
        "box_score_log": [{"outs": 18}] * 10,
        "role_status": {"status": "ACTIVE", "role": "STARTER"},
        "role_timestamp": (now - timedelta(hours=3)).isoformat(),
        "opportunity_ledger": {"status": "PASS"},
        "source_timestamps": {"box_score_log": (now - timedelta(hours=2)).isoformat()},
        "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "BOX_SCORE_L10_MINUTES_WEIGHTED_PER_MINUTE_V1",
        "probability_publishable": False,
        "can_execute": False,
    }


class _Response:
    def __init__(self, data):
        self.data = data


class _EvidenceQuery:
    """Minimal PostgREST query double for the governed fallback lookup."""

    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def lt(self, key, value):
        self.filters.append((f"lt:{key}", value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, _count):
        return self

    def execute(self):
        required = {
            ("event_id", EVENT_ID),
            ("sport", "MLB"),
            ("player", PLAYER),
            ("stat_type", STAT),
            ("line", 5.5),
            ("hydration_status", "PASS"),
        }
        if not required.issubset(set(self.filters)):
            return _Response([])
        return _Response([dict(row) for row in self.rows])


class _RepairClient:
    def __init__(self):
        self.rows = [
            {
                "source_snapshot_id": SNAPSHOT_ID,
                "captured_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                "event_id": EVENT_ID,
                "sport": "MLB",
                "player": PLAYER,
                "stat_type": STAT,
                "line": 5.5,
                "hydration_status": "PASS",
            }
        ]

    def table(self, name):
        assert name == "wow_prop_evidence_snapshots"
        return _EvidenceQuery(self.rows)


class _PredictionSelect:
    def __init__(self, store):
        self.store = store
        self.prediction_id = None

    def eq(self, key, value):
        assert key == "prediction_id"
        self.prediction_id = value
        return self

    def limit(self, _count):
        return self

    def execute(self):
        row = self.store.get(self.prediction_id)
        return _Response([dict(row)] if row is not None else [])


class _PredictionInsert:
    def __init__(self, store, payload, counters):
        self.store = store
        self.payload = payload
        self.counters = counters

    def execute(self):
        self.counters["inserts"] += 1
        self.store.setdefault(self.payload["prediction_id"], dict(self.payload))
        return _Response([dict(self.store[self.payload["prediction_id"]])])


class _PredictionTable:
    def __init__(self, store, counters):
        self.store = store
        self.counters = counters

    def select(self, *_args, **_kwargs):
        return _PredictionSelect(self.store)

    def insert(self, payload):
        return _PredictionInsert(self.store, payload, self.counters)


class _PredictionClient:
    def __init__(self, store, counters):
        self.store = store
        self.counters = counters

    def table(self, name):
        assert name == "wow_predictions"
        return _PredictionTable(self.store, self.counters)


def _payload(*, source_snapshot_id, noncanonical=False):
    """Build canonical or alias-spelled score payloads."""
    payload = {
        "event_id": EVENT_ID,
        "event_start_time": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "sport": "MLB",
        "player": PLAYER,
        "stat_type": STAT,
        "line": 5.5,
        "direction": "MORE",
        "source_snapshot_id": source_snapshot_id,
        "money_lane_status": "PAYOUT_UNRESOLVED",
        "seed": 7,
    }
    if noncanonical:
        payload.update(
            {
                "event_id": f"  {EVENT_ID}  ",
                "player": "  Test   Pitcher ",
                "stat_type": "K",
            }
        )
    return payload


def _install_route(monkeypatch):
    """Install route doubles and return captured requests and insert counters."""
    repair_client = _RepairClient()
    prediction_store: dict[str, dict] = {}
    counters = {"inserts": 0}
    prediction_client = _PredictionClient(prediction_store, counters)
    captured_requests: list = []

    def primary_fetch(req):
        """Return evidence only for the exact canonical fallback request."""
        canonical = (
            str(req.source_snapshot_id) == SNAPSHOT_ID
            and req.event_id == EVENT_ID
            and req.sport == "MLB"
            and req.player == PLAYER
            and req.stat_type == STAT
        )
        if canonical:
            return _evidence()
        return {
            "ok": False,
            "code": "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND",
            "probability_publishable": False,
            "can_execute": False,
        }

    def fake_score(**kwargs):
        """Capture the effective request and delegate to the real scorer."""
        captured_requests.append(kwargs["request"])
        return real_score_discrete_prop_end_to_end(**kwargs, infer_fn=_infer)

    monkeypatch.setattr(
        api_prod_market.prod,
        "_runtime_capability",
        lambda _key: {"capability_status": "AVAILABLE", "evidence": {}, "can_execute": False},
    )
    monkeypatch.setattr(api_prod_market.prod, "_prop_evidence", primary_fetch)
    monkeypatch.setattr(api_prod_market.prod, "_reject_llp_prop_identity", lambda _identity: "TEST")

    def specialist_provider(_sport, _stat):
        """Return the route specialist and assert canonical route lookup."""
        assert _sport == "MLB"
        assert _stat == STAT
        return {
            "sport": "MLB",
            "canonical_prop_type": STAT,
            "controlling_specialist": "wow.mlb-pitcher-strikeouts-expert@canonical-test",
            "min_event_tree_simulations": 0,
        }

    def route_artifact(_sport, _stat):
        """Return the exact-route artifact and assert canonical route lookup."""
        assert _sport == "MLB"
        assert _stat == STAT
        return {
            "ok": True,
            "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
            "sport": "MLB",
            "stat_type": STAT,
            "feature_schema_version": "PROP_FEATURES_V1",
            "model_family": _ARTIFACT.model_family,
            "model_artifact_version": _BUNDLE.model_artifact_version,
            "lifecycle_state": _BUNDLE.lifecycle_state,
            "probability_publishable": False,
            "can_execute": False,
        }

    monkeypatch.setattr(
        api_prod_market.prod.base_api,
        "_controlling_specialist_provider",
        specialist_provider,
    )
    monkeypatch.setattr(
        api_prod_market,
        "_prop_route_artifact",
        route_artifact,
    )
    monkeypatch.setattr(api_prod_market.prod, "get_client", lambda: repair_client)
    monkeypatch.setattr(api_prod_market, "score_discrete_prop_end_to_end", fake_score)
    monkeypatch.setattr(
        api_prod_market,
        "resolve_prop_settlement_rule",
        lambda **_kwargs: SimpleNamespace(
            status="PASS",
            blocker=None,
            rule=None,
            authority="TEST",
            provider="TEST",
            rule_id="TEST-RULE",
            rule_version="1",
            source_ref=None,
            source_hash=None,
            money_semantics="FIXED_ODDS_RETURN_STAKE",
            observed_rule_status="PASS",
            can_execute=False,
        ),
    )
    monkeypatch.setattr(api_prod_market, "wolfram_audit_enabled", lambda: False)
    monkeypatch.setattr(
        api_prod_market,
        "audit_wolfram_claims",
        lambda _claims, required: {
            "verdict": "PASS",
            "provider": "PYTHON_PRIMARY",
            "audit_required": required,
            "receipts": [],
            "blocks_model_probability": False,
            "can_execute": False,
        },
    )
    monkeypatch.setattr(api_prod_market.prod.base_api, "_persist_fn", ledger.insert_prediction)
    monkeypatch.setattr(ledger, "get_client", lambda: prediction_client)
    return captured_requests, prediction_store, counters


def test_equivalent_canonical_and_alias_requests_share_one_identity(monkeypatch):
    """Prove canonical and alias requests share one governed identity."""
    captured_requests, prediction_store, counters = _install_route(monkeypatch)

    canonical = CLIENT.post(
        "/score-prop",
        json=_payload(source_snapshot_id=SNAPSHOT_ID),
        headers=AUTH,
    )
    assert canonical.status_code == 200, canonical.text
    alias = CLIENT.post(
        "/score-prop",
        json=_payload(source_snapshot_id=OTHER_SNAPSHOT_ID, noncanonical=True),
        headers=AUTH,
    )
    assert alias.status_code == 200, alias.text

    canonical_body = canonical.json()
    alias_body = alias.json()
    assert len(captured_requests) == 2

    # One evidence snapshot, one canonical market identity and one prediction
    # identity for two equivalent caller spellings.
    assert {request.evidence_snapshot_id for request in captured_requests} == {SNAPSHOT_ID}
    assert len({request.market_identity_id for request in captured_requests}) == 1
    assert {request.stat_type for request in captured_requests} == {STAT}
    assert {request.event_id for request in captured_requests} == {EVENT_ID}
    assert {
        request.player_id for request in captured_requests
    } == {captured_requests[0].player_id}

    canonical_id = canonical_body["prediction"]["prediction_id"]
    alias_id = alias_body["prediction"]["prediction_id"]
    assert canonical_id == alias_id
    assert len(prediction_store) == 1
    assert counters["inserts"] == 1
    persisted_row = next(iter(prediction_store.values()))
    assert persisted_row["line"] == 5.5
    assert persisted_row["direction"] == "MORE"
    assert persisted_row["stat_type"] == STAT
    assert persisted_row["event_id"] == EVENT_ID
    assert persisted_row["player"] == PLAYER
    assert canonical_body["can_execute"] is False
    assert alias_body["can_execute"] is False

    # The raw caller spelling remains auditable; only the effective identity
    # used for evidence, market and persistence decisions is canonical.
    assert canonical_body["request_identity_audit"]["caller_request"]["stat_type"] == STAT
    assert alias_body["request_identity_audit"]["caller_request"]["stat_type"] == "K"
    assert alias_body["request_identity_audit"]["caller_request"]["event_id"] == f"  {EVENT_ID}  "
    assert alias_body["request_identity_audit"]["caller_request"]["player"] == "  Test   Pitcher "
    assert alias_body["request_identity_audit"]["effective_request"]["stat_type"] == STAT
    assert alias_body["request_identity_audit"]["effective_request"]["event_id"] == EVENT_ID
    assert alias_body["request_identity_audit"]["canonicalized"] is True
    assert canonical_body["request_identity_audit"]["canonicalized"] is False
    assert (
        canonical_body["request_identity_audit"]["market_identity_id"]
        == alias_body["request_identity_audit"]["market_identity_id"]
        == captured_requests[0].market_identity_id
    )


def test_equivalent_retry_reuses_the_immutable_prediction(monkeypatch):
    """Prove an equivalent retry reuses the immutable prediction."""
    _captured, prediction_store, counters = _install_route(monkeypatch)

    first = CLIENT.post(
        "/score-prop",
        json=_payload(source_snapshot_id=SNAPSHOT_ID),
        headers=AUTH,
    )
    assert first.status_code == 200, first.text

    # A retry with the alias/different requested snapshot must resolve the same
    # immutable row and must not call insert again.
    retry = CLIENT.post(
        "/score-prop",
        json=_payload(source_snapshot_id=OTHER_SNAPSHOT_ID, noncanonical=True),
        headers=AUTH,
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["prediction"]["prediction_id"] == first.json()["prediction"]["prediction_id"]
    assert len(prediction_store) == 1
    assert counters["inserts"] == 1
