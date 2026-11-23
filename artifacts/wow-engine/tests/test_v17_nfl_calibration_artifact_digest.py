"""Prospective NFL Platt calibration-artifact provenance only.

No production calibration is rewritten and no independent certification is
minted by these tests or by the fitted-artifact producer.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

import nfl_event_model_v17 as nfl


def _fitted():
    return {
        "certified": True,
        "artifact_checksum": "a" * 64,
        "dataset_hash": "b" * 64,
        "split_hash": "c" * 64,
        "training_code_sha": "d" * 40,
        "bundle_fingerprint": "e" * 64,
        "model_artifact_version": "NFL_EVENT_LOGREG_PLATT_V1_TEST",
        "calibration_version": "NFL_PLATT_TIME_SPLIT_V1_TEST",
        "artifact_payload": {
            "calibration_method": nfl.CALIBRATION_METHOD,
            "platt_a": 1.025,
            "platt_b": -0.125,
            "bounds_method_version": nfl.BOUNDS_METHOD_VERSION,
            "uncertainty_margin": 0.051,
        },
        "metrics": {
            "validation_brier_score": 0.21,
            "validation_log_loss": 0.62,
            "validation_ece_10": 0.037,
            "uncertainty_margin": 0.051,
        },
        "training_rows": 880,
        "calibration_rows": 290,
        "validation_rows": 275,
        "fit_start": "2021-09-01",
        "fit_end": "2024-12-29",
    }


def test_calibrator_digest_is_deterministic_and_distinct_from_training_hash():
    f = _fitted()
    payload, digest = nfl._calibration_artifact_evidence(f)
    assert payload == nfl._calibration_artifact_evidence(deepcopy(f))[0]
    assert digest == nfl._sha256_json(payload)
    assert len(digest) == 64 and digest != f["dataset_hash"]
    assert payload["model_artifact_sha256"] == f["artifact_checksum"]
    assert payload["temporal_split_sha256"] == f["split_hash"]
    assert payload["validation_metrics_sha256"] == nfl._sha256_json(f["metrics"])
    assert "probability_publishable" not in payload and "can_execute" not in payload


@pytest.mark.parametrize("field,new_value", [
    ("platt_a", 1.026),
    ("platt_b", -0.126),
    ("uncertainty_margin", 0.052),
])
def test_changed_calibrator_parameters_invalidate_digest(field, new_value):
    f = _fitted()
    original_digest = nfl._calibration_artifact_evidence(f)[1]
    changed = deepcopy(f)
    changed["artifact_payload"][field] = new_value
    assert nfl._calibration_artifact_evidence(changed)[1] != original_digest


@pytest.mark.parametrize("field", ["artifact_checksum", "dataset_hash", "split_hash"])
def test_model_or_source_mutation_invalidates_digest(field):
    f = _fitted()
    old_sha = nfl._calibration_artifact_evidence(f)[1]
    changed = deepcopy(f)
    changed[field] = "f" * 64
    assert nfl._calibration_artifact_evidence(changed)[1] != old_sha
    changed[field] = "f" * 63 + "!"
    with pytest.raises(nfl.NFLModelOutputInvalid, match="NFL_CALIBRATION_ARTIFACT_SOURCE_HASH_INVALID"):
        nfl._calibration_artifact_evidence(changed)


def test_validation_metric_and_training_code_mutations_change_digest():
    f = _fitted()
    digest = nfl._calibration_artifact_evidence(f)[1]
    changed = deepcopy(f)
    changed["metrics"]["validation_brier_score"] = 0.22
    assert nfl._calibration_artifact_evidence(changed)[1] != digest
    changed = deepcopy(f)
    changed["training_code_sha"] = "f" * 40
    assert nfl._calibration_artifact_evidence(changed)[1] != digest


class _Db:
    def __init__(self):
        self.promote = None

    def rpc(self, name, args):
        assert name == "wow_v17_promote_nfl_model_bundle"
        self.promote = args
        return SimpleNamespace(execute=lambda: SimpleNamespace(data={"status": "PROMOTED"}))


def test_future_fit_passes_digest_payload_to_existing_governed_rpc(monkeypatch):
    f = _fitted()
    monkeypatch.setattr(nfl, "_load_training_feature_rows", lambda db: [{"game_id": "test"}])
    monkeypatch.setattr(nfl, "_dataset_hash", lambda rows: f["dataset_hash"])
    monkeypatch.setattr(nfl, "_active_artifact_rows", lambda db: [])
    monkeypatch.setattr(nfl, "_training_code_sha", lambda: f["training_code_sha"])
    monkeypatch.setattr(nfl, "fit_candidate", lambda rows, *, training_code_sha: deepcopy(f))
    db = _Db()
    result = nfl.ensure_champion_model(db)
    metrics = db.promote["p_calibrator"]["fit_metrics_json"]
    artifact_payload = metrics["calibration_artifact_payload"]
    assert metrics["calibration_artifact_sha256"] == nfl._sha256_json(artifact_payload)
    assert artifact_payload["model_artifact_sha256"] == db.promote["p_artifact"]["artifact_checksum"]
    assert artifact_payload["training_dataset_sha256"] == db.promote["p_artifact"]["training_dataset_hash"]
    assert metrics["validation_brier_score"] == f["metrics"]["validation_brier_score"]
    assert "independent_verification_status" not in db.promote["p_calibrator"]
    assert "independent_certification" not in db.promote["p_calibrator"]
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_existing_champion_is_not_silently_backfilled_or_recertified(monkeypatch):
    f = _fitted()
    monkeypatch.setattr(nfl, "_load_training_feature_rows", lambda db: [{"game_id": "test"}])
    monkeypatch.setattr(nfl, "_dataset_hash", lambda rows: f["dataset_hash"])
    monkeypatch.setattr(nfl, "_active_artifact_rows", lambda db: [{
        "artifact_id": "old-active", "model_artifact_version": "old-version",
        "training_dataset_hash": f["dataset_hash"],
    }])
    db = _Db()
    result = nfl.ensure_champion_model(db)
    assert result["status"] == "EXISTING_CHAMPION"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert db.promote is None
