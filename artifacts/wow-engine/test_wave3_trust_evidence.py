"""Wave 3 trust-evidence tests: coverage, lineup trust, bounds authority, attestation.

Each test asserts a typed HOLD for untrusted evidence and a review pass only
for fully bound, independently attested evidence. Research-only.
"""

from __future__ import annotations

import pytest

from v17.wave3_evidence_triad import compute_binding_proof, verify_triad
from v17.wave3_nba_evidence import (
    nba_evidence_preflight,
    verify_nba_bounds_authority,
    verify_nba_lineup_evidence,
    verify_nba_orientation_coverage,
)

SHA = "b" * 64
OTHER = "c" * 64


def _triad(sport: str = "NBA") -> dict:
    triad = {
        "sport": sport,
        "market": "MONEYLINE",
        "model_family": f"{sport}_FAMILY",
        "model_version": "v1",
        "fitted_artifact_sha256": SHA,
        "feature_schema_version": f"{sport}_SCHEMA_V1",
        "calibrator_artifact_sha256": SHA,
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "pregame_corpus_digest": SHA,
        "settlement_basis": "OFFICIAL_FINAL",
    }
    triad["binding_proof"] = compute_binding_proof(triad)
    return triad


def _attestation() -> dict:
    return {
        "artifact_registration": {
            "registry_id": "wow-prop-fitted-model-artifacts",
            "registered_artifact_sha256": SHA,
            "registrar": "governed-registry",
            "attested_by": "SIRT",
            "lifecycle_state": "PROSPECTIVE_CERTIFIED",
            "active": True,
            "promoted": True,
        },
        "calibrator_validation": {
            "validation_status": "PASS",
            "health_status": "PASS",
            "calibrator_artifact_sha256": SHA,
            "scope_sport": "NBA",
            "scope_market": "MONEYLINE",
            "attested_by": "SIRT",
        },
        "corpus_provenance": {
            "source_id": "nba-official-pregame-corpus",
            "manifest_sha256": SHA,
            "frozen_at": "2026-01-01T10:00:00Z",
            "retrieved_at": "2026-01-01T11:00:00Z",
            "prelock_attested": True,
            "attested_by": "INDEPENDENT_VERIFICATION",
        },
    }


def _attested_triad() -> dict:
    triad = _triad()
    triad.update(_attestation())
    triad["binding_proof"] = compute_binding_proof(triad)
    return triad


def _lineup(ids=("g1", "g2", "g3")) -> dict:
    return {
        "rotation_changed": False,
        "latest_material_update_ts": None,
        "official_event_ids": list(ids),
        "source_id": "nba-official-injury-report",
        "evidence_as_of": "2026-01-01T11:00:00Z",
        "corroboration": {
            "official_event_ids": list(ids),
            "source_id": "nba-team-pr-wire",
            "evidence_as_of": "2026-01-01T11:30:00Z",
            "rotation_changed": False,
        },
    }


def _bounds() -> dict:
    return {
        "policy_id": "NBA_DYNAMIC_BOUNDS_POLICY",
        "policy_version": "V1",
        "policy_artifact_sha256": SHA,
        "scope_sport": "NBA",
        "scope_market": "MONEYLINE",
        "approved_by": "SIRT",
        "submitted_by": "MODEL_RESEARCH",
        "approval_receipt_id": "receipt-001",
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_until": "2026-06-30T00:00:00Z",
    }


def _preflight_kwargs(**overrides):
    kwargs = {
        "triad": _attested_triad(),
        "train": ["2026-01-01T00:00:00Z"],
        "calibration": ["2026-02-01T00:00:00Z"],
        "holdout": ["2026-03-01T00:00:00Z"],
        "settlement_includes_ot": True,
        "post_tipoff_features_used": False,
        "train_ids": ["g1"],
        "calibration_ids": ["g2"],
        "holdout_ids": ["g3"],
        "source_snapshot_ts": "2025-12-31T10:00:00Z",
        "feature_ts": "2025-12-31T12:00:00Z",
        "tipoff_ts": "2026-01-02T00:00:00Z",
        "event_records": [
            {"official_event_id": "g1", "home_team_id": "h1", "away_team_id": "a1", "event_start_time": "2026-01-01T00:00:00Z"},
            {"official_event_id": "g2", "home_team_id": "h2", "away_team_id": "a2", "event_start_time": "2026-02-01T00:00:00Z"},
            {"official_event_id": "g3", "home_team_id": "h3", "away_team_id": "a3", "event_start_time": "2026-03-01T00:00:00Z"},
        ],
        "event_times": {
            "g1": "2026-01-01T00:00:00Z",
            "g2": "2026-02-01T00:00:00Z",
            "g3": "2026-03-01T00:00:00Z",
        },
        "lineup_evidence": _lineup(),
        "bounds_policy": _bounds(),
    }
    kwargs.update(overrides)
    return kwargs


# --- Fix 4: artifact / calibrator / corpus attestation ---


def test_attestation_missing_fails_closed():
    verdict = verify_triad(_triad(), require_attestation=True)
    assert verdict.ok is False
    assert "EVIDENCE_TRIAD_ARTIFACT_ATTESTATION_MISSING" in verdict.blockers
    assert "EVIDENCE_TRIAD_CALIBRATOR_ATTESTATION_MISSING" in verdict.blockers
    assert "EVIDENCE_TRIAD_CORPUS_ATTESTATION_MISSING" in verdict.blockers


def test_full_attestation_passes():
    triad = _attested_triad()
    verdict = verify_triad(
        {**triad, "expected_sport": "NBA", "expected_market": "MONEYLINE"},
        require_attestation=True,
    )
    assert verdict.ok is True


def test_registry_sha_mismatch_fails():
    triad = _attested_triad()
    triad["artifact_registration"]["registered_artifact_sha256"] = OTHER
    verdict = verify_triad(triad, require_attestation=True)
    assert verdict.ok is False
    assert "EVIDENCE_TRIAD_ARTIFACT_REGISTRY_MISMATCH" in verdict.blockers


def test_unpromoted_lifecycle_fails():
    triad = _attested_triad()
    triad["artifact_registration"]["lifecycle_state"] = "RESEARCH"
    triad["artifact_registration"]["promoted"] = False
    verdict = verify_triad(triad, require_attestation=True)
    assert verdict.ok is False
    assert "EVIDENCE_TRIAD_ARTIFACT_LIFECYCLE_UNCERTIFIED" in verdict.blockers
    assert "EVIDENCE_TRIAD_ARTIFACT_NOT_PROMOTED" in verdict.blockers


def test_calibrator_not_validated_fails():
    triad = _attested_triad()
    triad["calibrator_validation"]["validation_status"] = "PENDING"
    verdict = verify_triad(triad, require_attestation=True)
    assert verdict.ok is False
    assert "EVIDENCE_TRIAD_CALIBRATOR_NOT_VALIDATED" in verdict.blockers


def test_corpus_manifest_mismatch_fails():
    triad = _attested_triad()
    triad["corpus_provenance"]["manifest_sha256"] = OTHER
    verdict = verify_triad(triad, require_attestation=True)
    assert verdict.ok is False
    assert "EVIDENCE_TRIAD_CORPUS_MANIFEST_MISMATCH" in verdict.blockers


# --- Fix 1: orientation coverage of every supplied ID ---


def test_orientation_missing_id_fails():
    result = verify_nba_orientation_coverage(
        [{"official_event_id": "g1", "home_team_id": "h1", "away_team_id": "a1"}],
        ["g1", "g2"],
    )
    assert result["ok"] is False
    assert "NBA_EVENT_ORIENTATION_COVERAGE_MISSING" in result["blockers"]


def test_orientation_unrelated_id_fails():
    result = verify_nba_orientation_coverage(
        [
            {"official_event_id": "g1", "home_team_id": "h1", "away_team_id": "a1"},
            {"official_event_id": "zzz", "home_team_id": "h9", "away_team_id": "a9"},
        ],
        ["g1"],
    )
    assert result["ok"] is False
    assert "NBA_EVENT_ORIENTATION_UNRELATED_EVENT" in result["blockers"]


def test_orientation_duplicate_record_fails():
    record = {"official_event_id": "g1", "home_team_id": "h1", "away_team_id": "a1"}
    result = verify_nba_orientation_coverage([record, dict(record)], ["g1"])
    assert result["ok"] is False
    assert "NBA_EVENT_ORIENTATION_DUPLICATE_RECORD" in result["blockers"]


def test_orientation_full_coverage_passes():
    result = verify_nba_orientation_coverage(
        [
            {"official_event_id": "g1", "home_team_id": "h1", "away_team_id": "a1"},
            {"official_event_id": "g2", "home_team_id": "h2", "away_team_id": "a2"},
        ],
        ["g1", "g2"],
    )
    assert result == {"ok": True, "blockers": []}


# --- Fix 2: lineup evidence trust ---


def test_bare_unchanged_lineup_fails_without_binding():
    result = verify_nba_lineup_evidence(
        {"rotation_changed": False},
        expected_ids=["g1"],
        source_snapshot_ts="2026-01-01T10:00:00Z",
        feature_ts="2026-01-01T12:00:00Z",
        tipoff_ts="2026-01-02T00:00:00Z",
    )
    assert result["ok"] is False
    assert "NBA_LINEUP_EVENT_BINDING_MISSING" in result["blockers"]
    assert "NBA_LINEUP_SOURCE_IDENTITY_MISSING" in result["blockers"]
    assert "NBA_LINEUP_AS_OF_MISSING" in result["blockers"]


def test_lineup_event_binding_mismatch_fails():
    lineup = _lineup(ids=("g1", "zzz"))
    result = verify_nba_lineup_evidence(
        lineup,
        expected_ids=["g1", "g2", "g3"],
        source_snapshot_ts="2026-01-01T10:00:00Z",
        feature_ts="2026-01-01T12:00:00Z",
        tipoff_ts="2026-01-02T00:00:00Z",
    )
    assert result["ok"] is False
    assert "NBA_LINEUP_EVENT_BINDING_MISMATCH" in result["blockers"]


def test_lineup_as_of_after_lock_fails():
    lineup = _lineup()
    lineup["evidence_as_of"] = "2026-01-03T00:00:00Z"
    result = verify_nba_lineup_evidence(
        lineup,
        expected_ids=["g1", "g2", "g3"],
        source_snapshot_ts="2026-01-01T10:00:00Z",
        feature_ts="2026-01-01T12:00:00Z",
        tipoff_ts="2026-01-02T00:00:00Z",
    )
    assert result["ok"] is False
    assert "NBA_LINEUP_AS_OF_AFTER_LOCK" in result["blockers"]


def test_trusted_lineup_passes():
    result = verify_nba_lineup_evidence(
        _lineup(),
        expected_ids=["g1", "g2", "g3"],
        source_snapshot_ts="2026-01-01T10:00:00Z",
        feature_ts="2026-01-01T12:00:00Z",
        tipoff_ts="2026-01-02T00:00:00Z",
    )
    assert result == {"ok": True, "blockers": []}


# --- Fix 3: bounds authority, not a boolean ---


def test_missing_bounds_policy_holds():
    result = verify_nba_bounds_authority(None, tipoff_ts="2026-01-02T00:00:00Z")
    assert result["ok"] is False
    assert "NBA_DYNAMIC_BOUNDS_POLICY_NOT_APPROVED" in result["blockers"]


def test_bounds_self_approval_rejected():
    policy = _bounds()
    policy["submitted_by"] = "SIRT"
    result = verify_nba_bounds_authority(policy, tipoff_ts="2026-01-02T00:00:00Z")
    assert result["ok"] is False
    assert "NBA_BOUNDS_SELF_APPROVAL_REJECTED" in result["blockers"]


def test_bounds_scope_mismatch_rejected():
    policy = _bounds()
    policy["scope_market"] = "SPREAD"
    result = verify_nba_bounds_authority(policy, tipoff_ts="2026-01-02T00:00:00Z")
    assert result["ok"] is False
    assert "NBA_BOUNDS_POLICY_SCOPE_MISMATCH" in result["blockers"]


def test_bounds_out_of_window_rejected():
    policy = _bounds()
    policy["valid_until"] = "2026-01-01T00:00:00Z"
    result = verify_nba_bounds_authority(policy, tipoff_ts="2026-01-02T00:00:00Z")
    assert result["ok"] is False
    assert "NBA_BOUNDS_POLICY_NOT_IN_FORCE" in result["blockers"]


def test_bounds_malformed_artifact_rejected():
    policy = _bounds()
    policy["policy_artifact_sha256"] = "not-a-sha"
    result = verify_nba_bounds_authority(policy, tipoff_ts="2026-01-02T00:00:00Z")
    assert result["ok"] is False
    assert "NBA_BOUNDS_POLICY_ARTIFACT_UNVERIFIED" in result["blockers"]


def test_bounds_valid_authority_passes():
    result = verify_nba_bounds_authority(_bounds(), tipoff_ts="2026-01-02T00:00:00Z")
    assert result == {"ok": True, "blockers": []}


# --- Preflight integration: full trust passes, omissions hold ---


def test_preflight_full_trust_passes_with_empty_blockers():
    verdict = nba_evidence_preflight(**_preflight_kwargs())
    assert verdict["decision"] == "READY_FOR_INDEPENDENT_MODEL_REVIEW"
    assert verdict["blockers"] == []
    assert verdict["probability_publishable"] is False
    assert verdict["can_execute"] is False


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("lineup_evidence", {"rotation_changed": False}, "NBA_LINEUP_EVENT_BINDING_MISSING"),
        ("bounds_policy", None, "NBA_DYNAMIC_BOUNDS_POLICY_NOT_APPROVED"),
        ("triad", None, "EVIDENCE_TRIAD_BINDING_UNPROVEN"),
    ],
)
def test_preflight_untrusted_inputs_hold(field, value, expected):
    kwargs = _preflight_kwargs()
    if field == "triad":
        kwargs["triad"] = {
            k: v for k, v in _triad().items() if k != "binding_proof"
        }
    else:
        kwargs[field] = value
    verdict = nba_evidence_preflight(**kwargs)
    assert verdict["decision"] == "REMAIN_SHADOW_MORE_EVIDENCE_REQUIRED"
    assert expected in verdict["blockers"]
