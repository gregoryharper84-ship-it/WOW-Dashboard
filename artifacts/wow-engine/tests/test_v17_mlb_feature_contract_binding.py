from __future__ import annotations

from types import SimpleNamespace

import pytest

import mlb_event_specialist_v16 as specialist
from mlb_event_specialist_v16 import ProspectiveModelUnavailable
from v17.mlb_event_feature_contract import (
    FEATURE_ORDER,
    FEATURE_ORDER_SHA256,
    FEATURE_SCHEMA_VERSION,
)
from v17.mlb_feature_contract_binding import (
    DIRECT_CONTEXT_FAILURE_FEATURE_IDS,
    _layered_receipt_builder,
    _validated_feature_rows,
    score_prospective_event_with_feature_contract,
)
from v17.mlb_prospective_failure_taxonomy import (
    MODEL_INPUTS_INSUFFICIENT,
    classify_mlb_prospective_failure,
)


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _Client:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "wow_mlb_forward_feature_snapshots"
        return _Query(self.rows)


def _rows(names=FEATURE_ORDER, *, duplicate_home=False):
    values = [float(i + 1) for i in range(len(names))]
    home = {
        "feature_snapshot_id": "home-feature-snapshot",
        "shadow_event_id": "shadow-1",
        "side": "HOME",
        "feature_names": list(names),
        "feature_vector": values,
        "source_snapshot_ids": ["home-source"],
        "source_urls": ["https://example.invalid/home"],
        "hydration_status": "PASS",
        "created_at": "2026-09-24T15:00:00+00:00",
    }
    away = {
        "feature_snapshot_id": "away-feature-snapshot",
        "shadow_event_id": "shadow-1",
        "side": "AWAY",
        "feature_names": list(names),
        "feature_vector": values,
        "source_snapshot_ids": ["away-source"],
        "source_urls": ["https://example.invalid/away"],
        "hydration_status": "PASS",
        "created_at": "2026-09-24T15:00:01+00:00",
    }
    return [home, dict(home)] + [away] if duplicate_home else [home, away]


def _bridge_payload():
    return {"shadow_event_id": "shadow-1", "score_snapshot_id": "score-1"}


def test_exact_certified_home_and_away_feature_orders_bind_successfully():
    bound = _validated_feature_rows(_Client(_rows()), _bridge_payload())
    assert set(bound) == {"HOME", "AWAY"}
    assert bound["HOME"]["feature_names"] == FEATURE_ORDER
    assert bound["AWAY"]["feature_names"] == FEATURE_ORDER
    assert len(bound["HOME"]["feature_vector"]) == len(FEATURE_ORDER) == 38


def test_same_length_reordered_or_arbitrary_feature_names_fail_closed():
    reordered = list(FEATURE_ORDER)
    reordered[0], reordered[1] = reordered[1], reordered[0]
    with pytest.raises(ProspectiveModelUnavailable, match="feature_contract_mismatch"):
        _validated_feature_rows(_Client(_rows(tuple(reordered))), _bridge_payload())

    arbitrary = tuple(f"unknown_{i}" for i in range(len(FEATURE_ORDER)))
    with pytest.raises(ProspectiveModelUnavailable, match="feature_contract_mismatch"):
        _validated_feature_rows(_Client(_rows(arbitrary)), _bridge_payload())


def test_ambiguous_pass_snapshot_identity_fails_closed():
    with pytest.raises(ProspectiveModelUnavailable, match="feature_snapshot_contract_ambiguous"):
        _validated_feature_rows(_Client(_rows(duplicate_home=True)), _bridge_payload())


def test_feature_contract_failures_preserve_model_inputs_insufficient_taxonomy():
    for reason in (
        "feature_snapshot_contract_query_failed",
        "feature_snapshot_contract_ambiguous",
        "feature_contract_mismatch",
        "feature_contract_digest_mismatch",
        "feature_vector_invalid",
    ):
        failure = classify_mlb_prospective_failure(ProspectiveModelUnavailable(reason))
        assert failure.code == MODEL_INPUTS_INSUFFICIENT
        assert failure.status_code == 422
        assert failure.can_execute is False


def test_wrapper_leaves_probability_values_unchanged_and_claims_only_direct_features(monkeypatch):
    original_result = {
        "raw_home_probability": 0.53123456789,
        "raw_away_probability": 0.46876543211,
        "calibrated_home_probability": 0.52123456789,
        "calibrated_away_probability": 0.47876543211,
        "calibrated_home_lower_bound": 0.501,
        "calibrated_home_upper_bound": 0.541,
        "calibrated_away_lower_bound": 0.459,
        "calibrated_away_upper_bound": 0.499,
        "model_version": "unchanged-model-version",
        "can_execute": False,
    }

    def unchanged_scorer(req, bridge_payload, client, **kwargs):
        return dict(original_result)

    monkeypatch.setattr(
        specialist,
        "_v17_feature_contract_original_score_prospective_event",
        unchanged_scorer,
        raising=False,
    )
    result = score_prospective_event_with_feature_contract(
        object(), _bridge_payload(), _Client(_rows())
    )

    for field, value in original_result.items():
        assert result[field] == value
    assert tuple(result["consumed_feature_ids"]) == DIRECT_CONTEXT_FAILURE_FEATURE_IDS
    assert len(result["consumed_feature_ids"]) == 8
    assert set(DIRECT_CONTEXT_FAILURE_FEATURE_IDS).issubset(FEATURE_ORDER)
    assert result["upstream_fitted_baseline_feature_contract"]["feature_ids"] == list(FEATURE_ORDER)
    assert result["upstream_fitted_baseline_feature_contract"]["feature_count"] == 38
    assert result["upstream_fitted_baseline_feature_contract"]["feature_order_sha256"] == FEATURE_ORDER_SHA256
    assert result["upstream_fitted_baseline_feature_contract"]["schema_version"] == FEATURE_SCHEMA_VERSION
    assert result["upstream_fitted_baseline_feature_contract"]["direct_specialist_consumption_claimed"] is False
    assert set(result["feature_observations"]) == set(FEATURE_ORDER)
    for feature_id in DIRECT_CONTEXT_FAILURE_FEATURE_IDS:
        observation = result["feature_observations"][feature_id]
        assert observation["value_status"] == "AVAILABLE"
        assert observation["source"] == "wow_mlb_forward_feature_snapshots:HOME+AWAY"
        assert observation["provenance_id"]
    assert result["can_execute"] is False


def test_layered_receipt_explicitly_separates_upstream_and_direct_consumption():
    base_result = {
        "upstream_fitted_baseline_feature_contract": {
            "feature_ids": list(FEATURE_ORDER),
            "feature_count": 38,
            "direct_specialist_consumption_claimed": False,
            "can_execute": False,
        },
        "direct_context_failure_consumption": {
            "feature_ids": list(DIRECT_CONTEXT_FAILURE_FEATURE_IDS),
            "feature_count": 8,
            "can_execute": False,
        },
    }

    def base_builder(*args, **kwargs):
        return {
            "feature_consumption_receipt_id": "old",
            "features_consumed": 8,
            "can_execute": False,
        }

    receipt = _layered_receipt_builder(base_builder)(result=base_result)
    assert receipt["features_consumed"] == 8
    assert receipt["upstream_fitted_baseline_features_bound"] == 38
    assert receipt["direct_specialist_features_consumed"] == 8
    assert receipt["consumption_layers"]["upstream_fitted_baseline"]["direct_specialist_consumption_claimed"] is False
    assert receipt["consumption_layers"]["direct_context_failure"]["feature_ids"] == list(DIRECT_CONTEXT_FAILURE_FEATURE_IDS)
    assert receipt["feature_consumption_receipt_id"] != "old"
    assert receipt["can_execute"] is False


def test_probability_producing_specialist_source_is_not_edited_for_binding():
    source = __import__("inspect").getsource(specialist)
    assert "def _simulate(" in source
    assert "def _bounds(" in source
    assert "DIRECT_CONTEXT_FAILURE_FEATURE_IDS" not in source
    assert "feature_contract_mismatch" not in source
