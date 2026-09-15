from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from v17.wnba_prop_candidate_registry import (
    WNBAPropCandidateArtifact,
    WNBAPropCandidateBatch,
    register_candidates,
    validate_candidate,
)


def _payload(stat: str = "POINTS") -> dict:
    return {
        "model_family": "WNBA_PROP_POISSON_LOGGLM_V1",
        "stat_type": stat,
        "feature_names": ["l10_mean"],
        "feature_mean": [10.0],
        "feature_scale": [2.0],
        "coef": [0.2],
        "intercept": 1.0,
    }


def _checksum(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _candidate(stat: str = "POINTS", **updates) -> WNBAPropCandidateArtifact:
    payload = _payload(stat)
    row = {
        "provider_identity": "WOW_PROP_FITTED_MODEL_V1",
        "model_family": "WNBA_PROP_POISSON_LOGGLM_V1",
        "model_artifact_version": f"WNBA_{stat}_POISSON_LOGGLM_V1_TEST",
        "calibrator_version": "WNBA_PROP_CAL_V1",
        "sport": "WNBA",
        "stat_type": stat,
        "feature_schema_version": "PROP_FEATURES_V1",
        "feature_transform_version": "WNBA_PROP_FEATURE_TRANSFORM_V1",
        "specialist_version": "wow-prop-wnba-v1",
        "certification_id": f"WNBA-{stat}-OFFLINE-TEST",
        "lifecycle_state": "CANDIDATE",
        "training_dataset_hash": "a" * 64,
        "training_code_sha": "b" * 40,
        "artifact_checksum": _checksum(payload),
        "artifact_format": "JSON_POISSON_LOGGLM_V1",
        "artifact_payload": payload,
        "supported_line_min": 0.0,
        "supported_line_max": 50.0,
        "training_rows": 500,
        "validation_metrics": {
            "validation_status": "PASS",
            "blockers": [],
            "probability_publishable": False,
            "can_execute": False,
        },
        "certification_eligible": True,
        "promoted": False,
        "active": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    row.update(updates)
    return WNBAPropCandidateArtifact(**row)


def test_valid_candidate_is_normalized_to_inert_registry_row():
    row = validate_candidate(_candidate())
    assert row["sport"] == "WNBA"
    assert row["stat_type"] == "POINTS"
    assert row["lifecycle_state"] == "CANDIDATE"
    assert row["promoted"] is False
    assert row["active"] is False
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False
    assert "certification_eligible" not in row


def test_frozen_candidate_provenance_is_accepted_verified_and_stripped_from_table_row():
    artifacts_path = Path(__file__).resolve().parent / "data" / "wow_wnba_prop_artifacts_v1.json"
    artifact_doc = json.loads(artifacts_path.read_text(encoding="utf-8"))[0]
    candidate = WNBAPropCandidateArtifact(**artifact_doc)
    row = validate_candidate(candidate)

    assert candidate.source_snapshot_bundle_id == "wnba-2026-20260904"
    assert candidate.source_attribution_required is True
    assert row["validation_metrics"]["source"]["source_snapshot"]["bundle_id"] == "wnba-2026-20260904"
    assert row["validation_metrics"]["source"]["source_snapshot"]["grants_model_capability"] is False
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False
    for field in (
        "numeric_canonicalization_decimals",
        "source_snapshot_bundle_id",
        "source_provider",
        "source_license_id",
        "source_attribution_required",
    ):
        assert field not in row


def test_frozen_candidate_source_mismatch_fails_closed():
    source_snapshot = {
        "bundle_id": "wnba-2026-20260904",
        "provider": "SPORTSDATAVERSE_WNBA_STATS",
        "license_id": "CC-BY-4.0",
        "attribution_required": True,
        "grants_model_capability": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    candidate = _candidate(
        numeric_canonicalization_decimals=12,
        source_snapshot_bundle_id="wrong-bundle",
        source_provider="SPORTSDATAVERSE_WNBA_STATS",
        source_license_id="CC-BY-4.0",
        source_attribution_required=True,
        validation_metrics={
            "validation_status": "PASS",
            "blockers": [],
            "probability_publishable": False,
            "can_execute": False,
            "source": {"source_snapshot": source_snapshot},
        },
    )
    with pytest.raises(HTTPException) as caught:
        validate_candidate(candidate)
    assert "WNBA_PROP_FROZEN_SOURCE_BUNDLE_MISMATCH" in caught.value.detail["blockers"]


def test_checksum_mismatch_fails_closed():
    with pytest.raises(HTTPException) as caught:
        validate_candidate(_candidate(artifact_checksum="c" * 64))
    assert caught.value.status_code == 422
    assert "WNBA_PROP_ARTIFACT_CHECKSUM_MISMATCH" in caught.value.detail["blockers"]


@pytest.mark.parametrize("field,value", [("promoted", True), ("active", True), ("probability_publishable", True), ("can_execute", True)])
def test_unsafe_governance_flags_fail_closed(field, value):
    with pytest.raises(HTTPException) as caught:
        validate_candidate(_candidate(**{field: value}))
    assert "WNBA_PROP_CANDIDATE_GOVERNANCE_FLAGS_INVALID" in caught.value.detail["blockers"]


class Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.filters = {}
        self.pending = None
    def select(self, *_a, **_k): return self
    def eq(self, key, value): self.filters[key] = value; return self
    def limit(self, *_a, **_k): return self
    def insert(self, row): self.pending = dict(row); return self
    def execute(self):
        if self.pending is not None:
            persisted = {**self.pending, "artifact_id": f"id-{len(self.db.rows)+1}"}
            self.db.rows.append(persisted)
            return SimpleNamespace(data=[persisted])
        matches = [row for row in self.db.rows if all(row.get(k) == v for k, v in self.filters.items())]
        return SimpleNamespace(data=matches[:1])


class FakeDB:
    def __init__(self, rows=None): self.rows = list(rows or [])
    def table(self, table):
        assert table == "wow_prop_fitted_model_artifacts"
        return Query(self, table)


def test_registration_is_idempotent_for_identical_version_and_checksum():
    candidate = _candidate()
    db = FakeDB()
    first = register_candidates(db, WNBAPropCandidateBatch(artifacts=[candidate]))
    second = register_candidates(db, WNBAPropCandidateBatch(artifacts=[candidate]))
    assert first["registered"][0]["status"] == "CANDIDATE_REGISTERED"
    assert second["registered"][0]["status"] == "ALREADY_REGISTERED_IDENTICAL"
    assert len(db.rows) == 1
    assert second["can_execute"] is False


def test_version_collision_with_different_checksum_blocks():
    candidate = _candidate()
    existing = {
        "artifact_id": "existing",
        "provider_identity": "WOW_PROP_FITTED_MODEL_V1",
        "model_artifact_version": candidate.model_artifact_version,
        "artifact_checksum": "d" * 64,
        "lifecycle_state": "CANDIDATE",
        "active": False,
        "promoted": False,
    }
    with pytest.raises(HTTPException) as caught:
        register_candidates(FakeDB([existing]), WNBAPropCandidateBatch(artifacts=[candidate]))
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "WNBA_PROP_ARTIFACT_VERSION_COLLISION"


def test_duplicate_stat_route_in_one_batch_blocks():
    a = _candidate("POINTS", model_artifact_version="WNBA_POINTS_A")
    b = _candidate("POINTS", model_artifact_version="WNBA_POINTS_B")
    with pytest.raises(HTTPException) as caught:
        register_candidates(FakeDB(), WNBAPropCandidateBatch(artifacts=[a, b]))
    assert caught.value.detail["code"] == "WNBA_PROP_DUPLICATE_STAT_ROUTE"
