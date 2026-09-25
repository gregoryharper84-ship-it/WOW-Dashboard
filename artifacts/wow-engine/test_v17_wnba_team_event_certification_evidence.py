from types import SimpleNamespace

import pytest

from v17 import wnba_team_event_certification_evidence as evidence


def _metrics():
    return {
        "raw_brier": 0.22,
        "calibrated_brier": 0.21,
        "baseline_brier": 0.25,
        "raw_log_loss": 0.64,
        "calibrated_log_loss": 0.62,
        "baseline_log_loss": 0.69,
        "ece": 0.05,
    }


def _candidate():
    artifact = {"feature_names": ["x"], "model_family": evidence.MODEL_FAMILY}
    calibrator = {"method": "IDENTITY_RAW_PROBABILITY_V1", "training_n": 1, "bins": []}
    return {
        "candidate_id": "11111111-1111-1111-1111-111111111111",
        "sport": "WNBA",
        "league": "WNBA",
        "model_family": evidence.MODEL_FAMILY,
        "model_artifact_version": "wnba-v1",
        "feature_schema_version": evidence.FEATURE_SCHEMA_VERSION,
        "source_policy_id": evidence.SOURCE_POLICY_ID,
        "training_dataset_hash": "d" * 64,
        "training_code_sha": "e" * 40,
        "artifact_checksum": evidence._hash(artifact),
        "artifact_payload": artifact,
        "calibrator_payload": calibrator,
        "validation_metrics": _metrics(),
        "training_rows": 1,
        "calibration_rows": 1,
        "test_rows": 1,
        "research_screen_pass": True,
        "source_review_status": "REQUIRED",
        "lifecycle_state": "CANDIDATE",
        "promoted": False,
        "active": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def _training_rows():
    rows = []
    for i, day in enumerate((1, 2, 3), start=1):
        game_id = f"espn-{i}"
        rows.append({
            "official_event_id": f"WNBA:{game_id}",
            "event_start_time": f"2026-09-0{day}T12:00:00+00:00",
            "feature_as_of": f"2026-09-0{day}T11:59:59+00:00",
            "features": {"x": float(i)},
            "outcome_json": {"home_win": bool(i % 2)},
            "source_manifest": {
                "program": "LLP_DYNAMIC_TEAM_STATE_CHALLENGER_V1",
                "source_manifest": {"source": evidence.UPSTREAM_TABLE, "game_id": game_id},
            },
            "source_manifest_sha256": f"{i}" * 64,
            "historical_reconstruction": True,
            "market_features_used": False,
            "can_execute": False,
        })
    return rows


def _upstream_rows():
    return [
        {
            "game_id": f"espn-{i}",
            "settled": True,
            "source_provider": evidence.SOURCE_ID,
            "source_endpoint": "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_wnba_schedules/wnba_schedule_2026.csv",
            "source_retrieved_at": "2026-09-25T00:00:00+00:00",
            "source_payload_sha256": "a" * 64,
        }
        for i in (1, 2, 3)
    ]


def _trainer(candidate):
    metrics = _metrics()
    return SimpleNamespace(
        dataset_hash=candidate["training_dataset_hash"],
        artifact_payload=candidate["artifact_payload"],
        calibrator_payload=candidate["calibrator_payload"],
        metrics=SimpleNamespace(
            train_n=1,
            calibration_n=1,
            test_n=1,
            **metrics,
        ),
    )


def test_exact_candidate_source_and_replay_evidence_passes_without_promotion():
    candidate = _candidate()
    result = evidence.evaluate_candidate_evidence(
        candidate,
        _training_rows(),
        _upstream_rows(),
        trainer=lambda *args, **kwargs: _trainer(candidate),
    )
    assert result["status"] == "CERTIFICATION_EVIDENCE_PASS"
    assert result["source_review_pass"] is True
    assert result["replay_evidence_pass"] is True
    assert result["candidate_mutated"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert result["upstream_provenance"]["source_provider"] == "SPORTSDATAVERSE_ESPN"
    assert result["source_review"]["license_id"] == "CC-BY-4.0"


def test_temporal_leakage_fails_closed():
    candidate = _candidate()
    rows = _training_rows()
    rows[0]["feature_as_of"] = rows[0]["event_start_time"]
    with pytest.raises(evidence.WNBACertificationEvidenceError) as exc:
        evidence.evaluate_candidate_evidence(
            candidate,
            rows,
            _upstream_rows(),
            trainer=lambda *args, **kwargs: _trainer(candidate),
        )
    assert exc.value.code == "WNBA_CERT_EVIDENCE_TEMPORAL_LEAKAGE"


def test_market_feature_or_wrong_upstream_provider_cannot_clear_review():
    candidate = _candidate()
    rows = _training_rows()
    rows[1]["market_features_used"] = True
    with pytest.raises(evidence.WNBACertificationEvidenceError) as exc:
        evidence.evaluate_candidate_evidence(
            candidate,
            rows,
            _upstream_rows(),
            trainer=lambda *args, **kwargs: _trainer(candidate),
        )
    assert exc.value.code == "WNBA_CERT_EVIDENCE_MARKET_FEATURE_FORBIDDEN"

    rows = _training_rows()
    upstream = _upstream_rows()
    upstream[0]["source_provider"] = "UNAPPROVED"
    with pytest.raises(evidence.WNBACertificationEvidenceError) as exc:
        evidence.evaluate_candidate_evidence(
            candidate,
            rows,
            upstream,
            trainer=lambda *args, **kwargs: _trainer(candidate),
        )
    assert exc.value.code == "WNBA_CERT_EVIDENCE_UPSTREAM_PROVIDER_MISMATCH"


def test_dataset_or_artifact_mismatch_cannot_create_replay_pass():
    candidate = _candidate()
    replayed = _trainer(candidate)
    replayed.dataset_hash = "f" * 64
    with pytest.raises(evidence.WNBACertificationEvidenceError) as exc:
        evidence.evaluate_candidate_evidence(
            candidate,
            _training_rows(),
            _upstream_rows(),
            trainer=lambda *args, **kwargs: replayed,
        )
    assert exc.value.code == "WNBA_CERT_EVIDENCE_DATASET_HASH_MISMATCH"
