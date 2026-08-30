"""End-to-end wiring proof using the REAL trained MLB pitcher-strikeout
artifact (data/wow_mlb_pitcher_strikeouts_artifact_v1.json -- produced by
scripts/train_mlb_pitcher_strikeouts.py from real Retrosheet-derived data,
see that script's module docstring for provenance).

This is not a live production run: it does not reach a real Supabase
project or a real wow_prop_evidence_snapshots row. It proves that the real,
out-of-sample-validated fitted constants flow correctly through
resolve_certified_artifact -> the registered model-family adapter ->
derive_line_probabilities -> the registered calibration adapter ->
determine_publishability, end to end in-process, with the exact same code path
api_prod_market.score_prop calls in production. The evidence snapshot below is
an illustrative example, not a real player's real game log.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from prop_calibration_adapters import register as register_calibration_adapter
from prop_discrete_engine import clear_prop_calibration_adapters, score_discrete_prop_end_to_end
from prop_distribution_contract import PropInferenceRequest
from prop_fitted_provider import clear_model_family_adapters
from prop_model_adapters import register as register_model_adapter
from qualification_policy_v2 import classify_prop_probability

ARTIFACT_JSON_PATH = os.path.join(os.path.dirname(__file__), "data", "wow_mlb_pitcher_strikeouts_artifact_v1.json")


def setup_function():
    clear_model_family_adapters()
    clear_prop_calibration_adapters()
    register_model_adapter()
    register_calibration_adapter()


def teardown_function():
    clear_model_family_adapters()
    clear_prop_calibration_adapters()


class FakeRPC:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return SimpleNamespace(data=self.payload)


class FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def rpc(self, name, params):
        assert name == "wow_prop_certified_model_artifact"
        return FakeRPC(self.payload)


def _real_trained_payload() -> dict:
    with open(ARTIFACT_JSON_PATH) as f:
        return json.load(f)


def _rpc_payload(trained: dict) -> dict:
    return {
        "ok": True,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        "artifact_id": "33333333-3333-4333-8333-333333333333",
        "provider_identity": "WOW_PROP_FITTED_MODEL_V1",
        "model_family": trained["model_family"],
        "model_artifact_version": trained["model_artifact_version"],
        "calibrator_version": trained["calibrator_version"],
        "sport": trained["sport"],
        "stat_type": trained["stat_type"],
        "feature_schema_version": trained["feature_schema_version"],
        "feature_transform_version": trained["feature_transform_version"],
        "specialist_version": trained["specialist_version"],
        "certification_id": "CERT-MLB-PITCHER-SO-V1-2026-08-29",
        "lifecycle_state": "PROSPECTIVE_CERTIFIED",
        "training_dataset_hash": trained["training_dataset_hash"],
        "training_code_sha": trained["training_code_sha"],
        "artifact_checksum": "d" * 64,
        "artifact_format": "JSON_V1",
        "artifact_payload": trained,
        "supported_line_min": trained["supported_line_min"],
        "supported_line_max": trained["supported_line_max"],
        "training_rows": trained["training_rows"],
        "validation_metrics": trained["validation_metrics"],
        "probability_publishable": False,
        "can_execute": False,
    }


def _request(evidence_snapshot_id="44444444-4444-4444-8444-444444444444"):
    return PropInferenceRequest(
        event_id="MLB:2026-08-29:NYY-BOS",
        player_id="wow-name:mlb-illustrative-starter",
        sport="MLB",
        league_season="2026",
        stat_type="PITCHER_STRIKEOUTS",
        evidence_snapshot_id=evidence_snapshot_id,
        market_identity_id="wow-market:mlb-illustrative-starter-so",
        as_of_timestamp="2026-08-29T17:00:00+00:00",
        request_id="req-real-artifact-e2e",
        feature_schema_version="PROP_FEATURES_V1",
    )


def _illustrative_features():
    game_log = [6, 5, 7, 4, 6, 8, 5, 3, 6, 7]
    box_score_log = [{"outs": o} for o in [18, 17, 19, 12, 18, 20, 17, 11, 18, 19]]
    return {"game_log": game_log, "box_score_log": box_score_log, "opponent_context": None}


def test_real_trained_artifact_scores_more_end_to_end():
    trained = _real_trained_payload()
    client = FakeClient(_rpc_payload(trained))
    request = _request()

    result = score_discrete_prop_end_to_end(
        client=client,
        request=request,
        event_start_time="2026-08-29T23:00:00+00:00",
        player="Illustrative Starter",
        line=5.5,
        direction="MORE",
        source_snapshot_id=request.evidence_snapshot_id,
        features=_illustrative_features(),
        seed=11,
    )

    row = result.row
    assert row.model_provider_identity == "WOW_PROP_FITTED_MODEL_V1"
    assert row.model_family == "MLB_PITCHER_SO_FAILURE_PATH_NB_V1"
    assert row.distribution_type == "DISCRETE_PMF"
    assert row.probability_more + row.probability_less + row.push_probability == pytest.approx(1.0)
    assert 0.0 < row.raw_model_probability < 1.0
    assert row.calibration_status == "PRECALIBRATION_SHRINKAGE"
    assert 0.0 < row.calibrated_probability_lower_bound <= row.calibrated_probability <= row.calibrated_probability_upper_bound < 1.0
    assert row.probability_publishable is True

    expected = classify_prop_probability(
        calibrated_probability=row.calibrated_probability,
        calibrated_lower_bound=row.calibrated_probability_lower_bound,
        calibration_status=row.calibration_status,
        blockers=row.data_gaps,
        probability_publishable=row.probability_publishable,
    )
    assert row.probability_ceiling == expected.terminal_label
    # Phase-A may be research-supported or rejected on probability, but it can
    # never advance into downstream money/final approval.
    assert expected.downstream_money_evaluation_allowed is False
    assert expected.final_approved_allowed is False


def test_real_trained_artifact_more_and_less_derive_from_same_pmf():
    trained = _real_trained_payload()
    client = FakeClient(_rpc_payload(trained))

    more_result = score_discrete_prop_end_to_end(
        client=client, request=_request(), event_start_time="2026-08-29T23:00:00+00:00",
        player="Illustrative Starter", line=5.0, direction="MORE",
        source_snapshot_id="44444444-4444-4444-8444-444444444444",
        features=_illustrative_features(), seed=11,
    )
    less_result = score_discrete_prop_end_to_end(
        client=client, request=_request(), event_start_time="2026-08-29T23:00:00+00:00",
        player="Illustrative Starter", line=5.0, direction="LESS",
        source_snapshot_id="44444444-4444-4444-8444-444444444444",
        features=_illustrative_features(), seed=11,
    )
    assert more_result.row.probability_more == pytest.approx(less_result.row.probability_more)
    assert more_result.row.push_probability == pytest.approx(less_result.row.push_probability)
    assert more_result.row.probability_more + more_result.row.probability_less + more_result.row.push_probability == pytest.approx(1.0)


def test_real_trained_artifact_reports_model_beats_baseline_out_of_sample():
    trained = _real_trained_payload()
    assert trained["validation_metrics"]["model_mean_nll"] < trained["validation_metrics"]["baseline_mean_nll"]
