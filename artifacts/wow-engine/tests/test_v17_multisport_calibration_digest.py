"""Canonical calibration-artifact serialization and digest verification.

The calibrator payload is the object that carries Platt coefficients and
residual bounds. Its SHA-256 must be reproducible by the database, so the
canonical serializer here is a contract, not a convenience helper:

* sorted keys (code-point order, which matches PostgreSQL UTF-8 byte order),
* compact separators, no whitespace,
* UTF-8 text preserved (no ASCII escaping),
* numbers rendered without exponent notation, with signed zero normalized to
  ``0.0`` and integral floats kept as ``240.0`` -- exactly what a PostgreSQL
  jsonb numeric renders.

Any change to these rules invalidates digests that independent verification has
already approved, so the rules are pinned by tests.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v17.basketball_team_event_calibration_artifact import (
    build_shadow_binary_calibration_artifact,
)
from v17.multisport_team_event_calibration import (
    CalibrationArtifactDigestError,
    artifact_fingerprint,
    calibration_payload_sha256,
    canonical_calibration_json,
    normalize_payload_sha256,
    verify_calibration_payload_digest,
)

SHA = "a" * 64


def _artifact_payload():
    return {
        "artifact_type": "BINARY_PLATT_CALIBRATOR",
        "sport": "NBA",
        "model_family": "BASKETBALL_TEAM_EVENT_LOGISTIC_V1",
        "model_version": "NBA_TEAM_EVENT_LOGISTIC_V1_20261101T182242Z",
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "calibration_version": "NBA_TEAM_EVENT_PLATT_V1_20261101T182242Z",
        "training_n": 240,
        "platt_a": 0.9123456789012345,
        "platt_b": -0.083,
        "residual_quantile_90": 0.08000000000000002,
        "brier_score": 0.2412345678901234,
        "log_loss": 0.6634,
        "calibration_error": 0.031,
        "calibration_bias": 0.004,
        "source_data_hash": SHA,
        "split_hash": "b" * 64,
        "fold_train_audit_hash": "c" * 64,
        "residual_basis": "FULL_DATA_IN_SAMPLE_NOT_CERTIFIED_FOR_BOUNDS",
        "bounds_preapproval_status": "REVIEW_REQUIRED",
        "fit_end": "2026-11-01T18:22:42+00:00",
        "health_status": "PASS",
        "certification_status": "REVIEW_REQUIRED",
        "independent_verification_status": "REVIEW_REQUIRED",
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def test_canonical_serialization_is_compact_sorted_and_unambiguous():
    payload = {"b": 1, "a": [1, 2.5, None, True], "c": {"z": "é", "y": 2}}
    assert canonical_calibration_json(payload) == (
        '{"a":[1,2.5,null,true],"b":1,"c":{"y":2,"z":"é"}}'
    )


def test_canonical_serialization_preserves_json_number_rendering():
    payload = {
        "int": 7,
        "zero": 0.0,
        "signed_zero": -0.0,
        "integral_float": 240.0,
        "expanded": 1e-05,
        "long": 0.30000000000000004,
    }
    rendered = canonical_calibration_json(payload)
    # PostgreSQL jsonb numeric renders 1e-05 as 0.00001 and -0.0 as 0.0.
    assert rendered == (
        '{"expanded":0.00001,"int":7,"integral_float":240.0,'
        '"long":0.30000000000000004,"signed_zero":0.0,"zero":0.0}'
    )


def test_canonical_serialization_escapes_like_postgresql_json_output():
    payload = {"key": 'a"b\\c\n\t\b\f\u0001é中'}
    assert canonical_calibration_json(payload) == (
        '{"key":"a\\"b\\\\c\\n\\t\\b\\f\\u0001é中"}'
    )


def test_canonical_serialization_is_independent_of_dict_insertion_order():
    forward = {key: value for key, value in _artifact_payload().items()}
    reverse = dict(reversed(list(_artifact_payload().items())))
    assert calibration_payload_sha256(forward) == calibration_payload_sha256(reverse)


def test_payload_digest_survives_a_json_round_trip():
    # Stands in for the PostgreSQL jsonb parse/serialize cycle for values the
    # builder emits. Byte parity with real jsonb is proven in
    # test_v17_basketball_calibrator_rpc_postgres.
    import json

    payload = _artifact_payload()
    stripped = json.loads(json.dumps(payload))
    assert calibration_payload_sha256(stripped) == calibration_payload_sha256(payload)


def test_digest_rejects_non_finite_numbers():
    with pytest.raises(CalibrationArtifactDigestError):
        canonical_calibration_json({"value": float("nan")})
    with pytest.raises(CalibrationArtifactDigestError):
        canonical_calibration_json({"value": float("inf")})


def test_digest_rejects_non_string_keys():
    with pytest.raises(CalibrationArtifactDigestError):
        canonical_calibration_json({1: "x"})


@pytest.mark.parametrize(
    "mutation",
    [
        {"platt_a": 0.9123456789012346},
        {"platt_b": -0.08299999999999},
        {"residual_quantile_90": 0.09},
        {"bounds_policy": "APPROVED"},
        {"probability_publishable": True},
        {"training_n": 239},
    ],
)
def test_any_content_change_changes_the_digest(mutation):
    baseline = _artifact_payload()
    mutated = {**baseline, **mutation}
    assert calibration_payload_sha256(mutated) != calibration_payload_sha256(baseline)


def test_verify_requires_payload_declared_and_certified_digests_to_agree():
    payload = _artifact_payload()
    sha = calibration_payload_sha256(payload)
    assert (
        verify_calibration_payload_digest(
            payload, declared_sha256=sha.upper(), certified_sha256=sha
        )
        == sha
    )


@pytest.mark.parametrize(
    ("declared", "certified"),
    [
        ("b" * 64, "b" * 64),
        (None, SHA),
        (SHA, None),
        (SHA, "b" * 64),
        ("not-a-digest", "not-a-digest"),
    ],
)
def test_verify_rejects_missing_mismatched_or_malformed_digests(declared, certified):
    with pytest.raises(CalibrationArtifactDigestError):
        verify_calibration_payload_digest(
            _artifact_payload(), declared_sha256=declared, certified_sha256=certified
        )


def test_normalize_rejects_non_hex_and_short_tokens():
    for token in (None, "", "a" * 63, "a" * 65, "g" * 64, 12345):
        with pytest.raises(CalibrationArtifactDigestError):
            normalize_payload_sha256(token)


def _builder_fit():
    from types import SimpleNamespace

    from calibration import (
        CalibrationResult,
        CalibrationStatus,
        PlattCoefficients,
        PlattFitMetrics,
    )

    return SimpleNamespace(
        result=CalibrationResult(
            calibration_status=CalibrationStatus.PLATT_TIME_SPLIT_V1,
            calibration_method="PLATT_TIME_SPLIT_V1",
            calibrated_probability=float("nan"),
            lower_bound=float("nan"),
            upper_bound=float("nan"),
            money_qualified_allowed=True,
            final_approved_allowed=True,
        ),
        metrics=PlattFitMetrics(brier=0.22, log_loss=0.63, ece=0.04, calibration_bias=0.01),
        coefficients=PlattCoefficients(a=-0.1, b=0.9),
        fold_train_audit={i: list(range(i)) for i in range(1, 6)},
    )


def test_builder_digest_is_the_shared_canonical_digest_and_binds_content():
    count = 240
    raw = [0.3 + (i % 20) * 0.015 for i in range(count)]
    outcomes = [i % 2 for i in range(count)]
    folds = [i // 40 for i in range(count)]
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    timestamps = [(start + timedelta(days=i // 10)).isoformat() for i in range(count)]

    artifact, fingerprint = build_shadow_binary_calibration_artifact(
        sport="NBA",
        model_family="BASKETBALL_TEAM_EVENT_LOGISTIC_V1",
        model_version="NBA_DIGEST_TEST_MODEL",
        calibration_version="NBA_DIGEST_TEST_PLATT",
        raw_probabilities=raw,
        outcomes=outcomes,
        fold_assignments=folds,
        timestamps=timestamps,
        fit=_builder_fit(),
    )
    assert fingerprint == calibration_payload_sha256(artifact)
    assert fingerprint == artifact_fingerprint(artifact)

    tampered = {**artifact, "residual_quantile_90": artifact["residual_quantile_90"] + 0.01}
    assert calibration_payload_sha256(tampered) != fingerprint
    with pytest.raises(CalibrationArtifactDigestError):
        verify_calibration_payload_digest(
            tampered, declared_sha256=fingerprint, certified_sha256=fingerprint
        )
