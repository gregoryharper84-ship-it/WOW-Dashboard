from __future__ import annotations

from copy import deepcopy

from wnba_prop_certification_replay import (
    CERTIFICATION_REPLAY_BLOCKED,
    EXPECTED_STATS,
    READY_FOR_LIFECYCLE_REVIEW,
    audit_wnba_certification_replay,
)


def _manifest(*, approved: bool = True, attribution: bool = True):
    return {
        "schema_version": "WOW_HISTORICAL_SOURCE_MANIFEST_V1",
        "can_execute": False,
        "sources": [
            {
                "sport": "WNBA",
                "provider": "SPORTSDATAVERSE_WNBA_STATS",
                "evidence_domain": "SPORTING",
                "rights_state": "V17_APPROVED" if approved else "VALIDATION_ONLY",
                "credential_required": False,
                "license_id": "CC-BY-4.0",
                "license_url": "https://github.com/sportsdataverse/wehoop-wnba-stats-data/blob/main/LICENSE.md",
                "source_repository": "sportsdataverse/wehoop-wnba-stats-data",
                "attribution_required": attribution,
                "grants_model_capability": False,
            }
        ],
    }


def _artifact(stat: str, *, training_code_sha: str):
    return {
        "provider_identity": "WOW_PROP_FITTED_MODEL_V1",
        "model_family": "WNBA_PROP_POISSON_LOGGLM_V1",
        "model_artifact_version": f"WNBA_{stat}_V1",
        "calibrator_version": "WNBA_PROP_PRECALIBRATION_BOOTSTRAP_V1",
        "sport": "WNBA",
        "stat_type": stat,
        "feature_schema_version": "PROP_FEATURES_V1",
        "feature_transform_version": "WNBA_PROP_ROLLING_FORM_V1",
        "specialist_version": "wow.wnba-player-prop-probability-expert@1",
        "certification_id": f"WNBA-{stat}-OFFLINE",
        "lifecycle_state": "CANDIDATE",
        "training_dataset_hash": f"dataset-{stat}",
        "training_code_sha": training_code_sha,
        "artifact_checksum": f"artifact-{stat}",
        "artifact_format": "JSON_POISSON_LOGGLM_V1",
        "artifact_payload": {
            "source_sha256": "f326bd597a607a574de488b153d76032ee5ec9c4cacd36c8380f229ed96e6288",
            "stat_type": stat,
        },
        "supported_line_min": 0.0,
        "supported_line_max": 50.0,
        "training_rows": 1300,
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


def _pair():
    checked = [_artifact(stat, training_code_sha="UNRESOLVED_TRAINING_CODE_SHA") for stat in sorted(EXPECTED_STATS)]
    replay = deepcopy(checked)
    for item in replay:
        item["training_code_sha"] = "a" * 40
    return checked, replay


def test_exact_replay_is_ready_for_separate_lifecycle_review_only() -> None:
    checked, replay = _pair()
    result = audit_wnba_certification_replay(
        checked_artifacts=checked,
        replay_artifacts=replay,
        source_manifest=_manifest(),
    )
    assert result["certification_replay_status"] == READY_FOR_LIFECYCLE_REVIEW
    assert result["ready_for_lifecycle_review"] is True
    assert result["artifact_replay_match"] is True
    assert result["runtime_model_status"] == "MODEL_UNAVAILABLE"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_replay_does_not_allow_unresolved_code_sha() -> None:
    checked, replay = _pair()
    replay[0]["training_code_sha"] = "UNRESOLVED_TRAINING_CODE_SHA"
    result = audit_wnba_certification_replay(
        checked_artifacts=checked,
        replay_artifacts=replay,
        source_manifest=_manifest(),
    )
    assert result["certification_replay_status"] == CERTIFICATION_REPLAY_BLOCKED
    assert any(code.endswith("REPLAY_CODE_SHA_UNRESOLVED") for code in result["blockers"])


def test_replay_mismatch_blocks_lifecycle_review() -> None:
    checked, replay = _pair()
    replay[0]["artifact_payload"]["stat_type"] = "MUTATED"
    result = audit_wnba_certification_replay(
        checked_artifacts=checked,
        replay_artifacts=replay,
        source_manifest=_manifest(),
    )
    assert result["ready_for_lifecycle_review"] is False
    assert any(code.endswith("REPLAY_MISMATCH") for code in result["blockers"])


def test_source_rights_must_be_production_approved() -> None:
    checked, replay = _pair()
    result = audit_wnba_certification_replay(
        checked_artifacts=checked,
        replay_artifacts=replay,
        source_manifest=_manifest(approved=False),
    )
    assert "WNBA_SOURCE_RIGHTS_NOT_APPROVED" in result["blockers"]


def test_cc_by_attribution_is_machine_enforced() -> None:
    checked, replay = _pair()
    result = audit_wnba_certification_replay(
        checked_artifacts=checked,
        replay_artifacts=replay,
        source_manifest=_manifest(attribution=False),
    )
    assert "WNBA_SOURCE_ATTRIBUTION_NOT_ENFORCED" in result["blockers"]


def test_replay_cannot_promote_or_enable_execution() -> None:
    checked, replay = _pair()
    replay[0]["active"] = True
    replay[1]["can_execute"] = True
    replay[2]["probability_publishable"] = True
    result = audit_wnba_certification_replay(
        checked_artifacts=checked,
        replay_artifacts=replay,
        source_manifest=_manifest(),
    )
    assert result["ready_for_lifecycle_review"] is False
    assert any(code.endswith("PREMATURE_LIFECYCLE_PROMOTION") for code in result["blockers"])
    assert any(code.endswith("EXECUTION_MUST_REMAIN_DISABLED") for code in result["blockers"])
    assert any(code.endswith("PREMATURE_PROBABILITY_PUBLICATION") for code in result["blockers"])
