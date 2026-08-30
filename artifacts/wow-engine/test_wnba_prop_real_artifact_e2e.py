"""End-to-end wiring proof using the REAL trained WNBA prop artifacts
(data/wow_wnba_prop_artifacts_v1.json -- produced by
scripts/train_wnba_props_offset.py from real WNBA Stats-derived 2026
season data; see that script and data/wow_wnba_prop_training_report_v1.json
for full provenance).

This mirrors test_prop_real_artifact_e2e.py's MLB proof for the WNBA lane.
It is not a live production run: it does not reach a real Supabase project
or a real wow_prop_evidence_snapshots row (this session has no production
credentials, by design -- see CLAUDE.md Section 9/10). It proves that each
of the four certified WNBA routes (POINTS, REBOUNDS, ASSISTS,
THREE_POINTERS_MADE) -- each independently holdout-validated with a
deviance ratio under the 1.02 gate in the real training report -- flows
correctly through resolve_certified_artifact -> the registered
WNBA_PROP_POISSON_LOGGLM_V1 model adapter -> derive_line_probabilities ->
the registered WNBA_PROP_PRECALIBRATION_BOOTSTRAP_V1 calibration adapter ->
determine_publishability, end to end in-process, with the exact same code
path api_prod_market.score_prop calls in production. The evidence snapshot
below is an illustrative example (a plausible player's last-10-game log),
not a real player's real game log, and lifecycle_state is set to
PROSPECTIVE_CERTIFIED only in this fake registry response to prove the
wiring -- no artifact has actually been promoted or written to any
production registry.
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

ARTIFACTS_JSON_PATH = os.path.join(os.path.dirname(__file__), "data", "wow_wnba_prop_artifacts_v1.json")

# Illustrative last-10-game logs per stat (plausible rotation-player
# volume), each paired with a plausible minutes log -- not any real
# player's real data.
_ILLUSTRATIVE_HISTORIES = {
    "POINTS": {
        "game_log": [8, 10, 9, 11, 12, 13, 12, 14, 13, 15],
        "line": 11.5,
    },
    "REBOUNDS": {
        "game_log": [3, 5, 4, 6, 5, 7, 4, 6, 5, 6],
        "line": 4.5,
    },
    "ASSISTS": {
        "game_log": [2, 4, 3, 5, 3, 4, 2, 5, 4, 3],
        "line": 3.5,
    },
    "THREE_POINTERS_MADE": {
        "game_log": [1, 2, 0, 3, 1, 2, 1, 3, 2, 1],
        "line": 1.5,
    },
}
_ILLUSTRATIVE_MINUTES = [28.0, 30.0, 27.0, 31.0, 29.0, 32.0, 28.0, 30.0, 29.0, 31.0]


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


def _real_trained_records() -> dict:
    with open(ARTIFACTS_JSON_PATH) as f:
        records = json.load(f)
    return {record["stat_type"]: record for record in records}


def _rpc_payload(trained: dict) -> dict:
    return {
        "ok": True,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        "artifact_id": "55555555-5555-4555-8555-555555555555",
        "provider_identity": trained["provider_identity"],
        "model_family": trained["model_family"],
        "model_artifact_version": trained["model_artifact_version"],
        "calibrator_version": trained["calibrator_version"],
        "sport": trained["sport"],
        "stat_type": trained["stat_type"],
        "feature_schema_version": trained["feature_schema_version"],
        "feature_transform_version": trained["feature_transform_version"],
        "specialist_version": trained["specialist_version"],
        "certification_id": trained["certification_id"],
        "lifecycle_state": "PROSPECTIVE_CERTIFIED",
        "training_dataset_hash": trained["training_dataset_hash"],
        "training_code_sha": trained["training_code_sha"],
        "artifact_checksum": "e" * 64,  # registry-assigned at insertion time; not recomputed here
        "artifact_format": trained["artifact_format"],
        "artifact_payload": trained["artifact_payload"],
        "supported_line_min": trained["supported_line_min"],
        "supported_line_max": trained["supported_line_max"],
        "training_rows": trained["training_rows"],
        "validation_metrics": trained["validation_metrics"],
        "probability_publishable": False,
        "can_execute": False,
    }


def _request(stat_type: str, evidence_snapshot_id="66666666-6666-4666-8666-666666666666"):
    return PropInferenceRequest(
        event_id="WNBA:2026-08-30:TEST-TEST",
        player_id="wow-name:wnba-illustrative-player",
        sport="WNBA",
        league_season="2026",
        stat_type=stat_type,
        evidence_snapshot_id=evidence_snapshot_id,
        market_identity_id=f"wow-market:wnba-illustrative-player-{stat_type.lower()}",
        as_of_timestamp="2026-08-30T17:00:00+00:00",
        request_id=f"req-wnba-real-artifact-e2e-{stat_type.lower()}",
        feature_schema_version="PROP_FEATURES_V1",
    )


def _illustrative_features(stat_type: str) -> dict:
    game_log = _ILLUSTRATIVE_HISTORIES[stat_type]["game_log"]
    box_score_log = [
        {"date": f"2026-06-{i:02d}", "minutes": minutes}
        for i, minutes in enumerate(_ILLUSTRATIVE_MINUTES, start=1)
    ]
    return {"game_log": game_log, "box_score_log": box_score_log}


@pytest.mark.parametrize("stat_type", ["POINTS", "REBOUNDS", "ASSISTS", "THREE_POINTERS_MADE"])
def test_real_trained_wnba_artifact_scores_more_end_to_end(stat_type):
    trained = _real_trained_records()[stat_type]
    client = FakeClient(_rpc_payload(trained))
    request = _request(stat_type)

    result = score_discrete_prop_end_to_end(
        client=client,
        request=request,
        event_start_time="2026-08-30T23:00:00+00:00",
        player="Illustrative WNBA Player",
        line=_ILLUSTRATIVE_HISTORIES[stat_type]["line"],
        direction="MORE",
        source_snapshot_id=request.evidence_snapshot_id,
        features=_illustrative_features(stat_type),
        seed=11,
    )

    row = result.row
    assert row.model_provider_identity == "WOW_PROP_FITTED_MODEL_V1"
    assert row.model_family == "WNBA_PROP_POISSON_LOGGLM_V1"
    assert row.distribution_type == "DISCRETE_PMF"
    assert row.probability_more + row.probability_less + row.push_probability == pytest.approx(1.0)
    assert 0.0 < row.raw_model_probability < 1.0
    assert row.calibration_status == "PRECALIBRATION_SHRINKAGE"
    assert 0.0 < row.calibrated_probability_lower_bound <= row.calibrated_probability <= row.calibrated_probability_upper_bound < 1.0
    assert row.probability_publishable is True
    # Phase A shrinkage is never money/final approved, regardless of how
    # strong the raw model result looks (calibration.py Section 8B.4) --
    # identical invariant to the MLB pitcher-strikeout route.
    assert row.probability_ceiling.startswith("MODEL_QUALIFIED_HOLD_PROHIBITED_PRECALIBRATION")


@pytest.mark.parametrize("stat_type", ["POINTS", "REBOUNDS", "ASSISTS", "THREE_POINTERS_MADE"])
def test_real_trained_wnba_artifact_more_and_less_derive_from_same_pmf(stat_type):
    trained = _real_trained_records()[stat_type]
    client = FakeClient(_rpc_payload(trained))
    line = _ILLUSTRATIVE_HISTORIES[stat_type]["line"]

    more_result = score_discrete_prop_end_to_end(
        client=client, request=_request(stat_type), event_start_time="2026-08-30T23:00:00+00:00",
        player="Illustrative WNBA Player", line=line, direction="MORE",
        source_snapshot_id="66666666-6666-4666-8666-666666666666",
        features=_illustrative_features(stat_type), seed=11,
    )
    less_result = score_discrete_prop_end_to_end(
        client=client, request=_request(stat_type), event_start_time="2026-08-30T23:00:00+00:00",
        player="Illustrative WNBA Player", line=line, direction="LESS",
        source_snapshot_id="66666666-6666-4666-8666-666666666666",
        features=_illustrative_features(stat_type), seed=11,
    )
    assert more_result.row.probability_more == pytest.approx(less_result.row.probability_more)
    assert more_result.row.push_probability == pytest.approx(less_result.row.push_probability)
    assert more_result.row.probability_more + more_result.row.probability_less + more_result.row.push_probability == pytest.approx(1.0)


@pytest.mark.parametrize("stat_type", ["POINTS", "REBOUNDS", "ASSISTS", "THREE_POINTERS_MADE"])
def test_real_trained_wnba_artifact_holdout_deviance_under_gate(stat_type):
    """Re-assert, directly from the checked-in training report, the exact
    holdout certification result this branch's WNBA fitted-model
    certification claims: an 803-row untouched holdout with a Poisson
    deviance ratio vs. a naive L10-mean baseline strictly under the 1.02
    gate, for every one of the four certified routes."""
    trained = _real_trained_records()[stat_type]
    metrics = trained["validation_metrics"]
    assert metrics["validation_status"] == "PASS"
    assert metrics["holdout_rows"] == 803
    assert metrics["deviance_ratio_vs_naive"] < metrics["deviance_ratio_gate_max"] == 1.02
