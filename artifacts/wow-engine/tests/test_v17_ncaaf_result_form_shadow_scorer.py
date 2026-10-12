"""Never allow a research fitted candidate to be misread as governed picks."""
from __future__ import annotations

from hashlib import sha256
import json
import math

import pytest

from v17.ncaaf_result_form_candidate import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, MODEL_FAMILY
from v17.ncaaf_result_form_shadow_scorer import (
    NCAAFShadowScoreBlocked, score_ncaaf_research_shadow,
)


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _candidate():
    artifact = {
        "model_family": MODEL_FAMILY,
        "artifact_format": "STANDARDIZED_LOGISTIC_JSON_V1",
        "feature_names": list(FEATURE_NAMES),
        "scaler_mean": [0.0] * len(FEATURE_NAMES),
        "scaler_scale": [1.0] * len(FEATURE_NAMES),
        "coefficients": [1.0] + [0.0] * (len(FEATURE_NAMES) - 1),
        "intercept": 0.0,
    }
    return {
        "sport": "NCAAF",
        "model_family": MODEL_FAMILY,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "model_artifact_version": "NCAAF_RESULT_FORM_LOGIT_V1_abcd0123_abcd0123",
        "lifecycle_state": "CANDIDATE",
        "research_screen_pass": True,
        "promoted": False,
        "active": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
        "training_dataset_hash": "e" * 64,
        "artifact_payload": artifact,
        "artifact_checksum": _digest(artifact),
    }


def _features():
    manifest = {
        "official_event_id": "401858254",
        "event_start_time": "2026-10-10T01:00:00+00:00",
        "feature_as_of": "2026-10-09T23:00:00+00:00",
        "canonical_identity_verified_by_caller": True,
        "canonical_identity_source": "CFBD:/games",
        "canonical_identity_resolution": "CFBD_EXACT_PARTICIPANTS_START_MATCH",
        "canonical_event_start_time": "2026-10-10T01:00:00+00:00",
        "canonical_neutral_site": False,
        "market_features_used": False,
        "archived_pregame_snapshot": False,
    }
    features = dict.fromkeys(FEATURE_NAMES, 0.0)
    features[FEATURE_NAMES[0]] = 0.5
    manifest["features_sha256"] = _digest(features)
    return {
        "status": "RESEARCH_FORWARD_FEATURES_ONLY",
        "candidate_model_family": MODEL_FAMILY,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "official_event_id": "401858254",
        "feature_as_of": manifest["feature_as_of"],
        "source_manifest": manifest,
        "source_manifest_sha256": _digest(manifest),
        "features": features,
        "features_sha256": _digest(features),
        "probability_publishable": False,
        "can_execute": False,
    }


def test_fitted_coefficient_math_is_research_only_and_reproducible():
    row = score_ncaaf_research_shadow(_candidate(), _features())
    assert row["research_raw_probability"] == pytest.approx(1 / (1 + math.exp(-0.5)))
    assert row["source_manifest_sha256"] == _features()["source_manifest_sha256"]
    assert row["status"] == "RESEARCH_SHADOW_UNCERTIFIED"
    assert row["model_probability"] is None
    assert row["calibrated_probability"] is None
    assert row["calibrated_lower_bound"] is None
    assert row["rank_eligible"] is False
    assert row["probability_publishable"] is False
    assert row["automatic_certification"] is False
    assert row["can_execute"] is False


@pytest.mark.parametrize("name,mutator,expected", [
    (
        "candidate-active",
        lambda c, f: c.update(active=True),
        "NCAAF_SHADOW_CANDIDATE_NOT_INERT",
    ),
    (
        "model-family-cross-route",
        lambda c, f: c.update(model_family="NFL_MODEL"),
        "NCAAF_SHADOW_MODEL_ROUTE_MISMATCH",
    ),
    (
        "artifact-hash-tamper",
        lambda c, f: c["artifact_payload"].update(intercept=0.75),
        "NCAAF_SHADOW_ARTIFACT_CHECKSUM_MISMATCH",
    ),
    (
        "manifest-event-tamper",
        lambda c, f: f["source_manifest"].update(official_event_id="wrong"),
        "NCAAF_SHADOW_MANIFEST_INVALID",
    ),
    (
        "manifest-signature-tamper",
        lambda c, f: f["source_manifest"].update(feature_as_of="2026-10-09T22:00:00Z"),
        "NCAAF_SHADOW_MANIFEST_CHECKSUM_MISMATCH",
    ),
    (
        "future-leakage",
        lambda c, f: f.update(feature_as_of="2026-10-11T01:00:00+00:00"),
        "NCAAF_SHADOW_FEATURE_TIME_INVALID",
    ),
    (
        "missing-feature",
        lambda c, f: f["features"].pop(FEATURE_NAMES[2]),
        "NCAAF_SHADOW_FEATURE_SCHEMA_MISMATCH",
    ),
    (
        "market-not-proven-clean",
        lambda c, f: f["source_manifest"].update(market_features_used=True),
        "NCAAF_SHADOW_MANIFEST_INVALID",
    ),
    (
        "boolean-feature",
        lambda c, f: f["features"].update({FEATURE_NAMES[0]: True}),
        "NCAAF_SHADOW_FEATURE_VALUE_INVALID",
    ),
])
def test_untrusted_shadow_inputs_fail_closed(name, mutator, expected):
    candidate, forward = _candidate(), _features()
    mutator(candidate, forward)
    with pytest.raises(NCAAFShadowScoreBlocked) as err:
        score_ncaaf_research_shadow(candidate, forward)
    assert err.value.code == expected, name


def test_zero_scale_cannot_be_used_even_with_self_consistent_payload_checksum():
    candidate = _candidate()
    candidate["artifact_payload"]["scaler_scale"][0] = 0.0
    candidate["artifact_checksum"] = _digest(candidate["artifact_payload"])
    with pytest.raises(NCAAFShadowScoreBlocked) as err:
        score_ncaaf_research_shadow(candidate, _features())
    assert err.value.code == "NCAAF_SHADOW_ARTIFACT_SCALE_INVALID"


def test_extreme_negative_logit_returns_finite_bounded_diagnostic():
    candidate = _candidate()
    candidate["artifact_payload"]["coefficients"][0] = -50000.0
    candidate["artifact_checksum"] = _digest(candidate["artifact_payload"])
    result = score_ncaaf_research_shadow(candidate, _features())
    assert result["research_raw_probability"] == 0.0
    assert result["probability_publishable"] is False


def test_duplicate_or_cross_sport_feature_names_are_rejected():
    candidate = _candidate()
    candidate["artifact_payload"]["feature_names"] = list(FEATURE_NAMES[:-1]) + [FEATURE_NAMES[0]]
    candidate["artifact_checksum"] = _digest(candidate["artifact_payload"])
    with pytest.raises(NCAAFShadowScoreBlocked) as err:
        score_ncaaf_research_shadow(candidate, _features())
    assert err.value.code == "NCAAF_SHADOW_ARTIFACT_SCHEMA_MISMATCH"


@pytest.mark.parametrize("key,value", [
    ("canonical_identity_source", "ESPN"),
    ("canonical_identity_resolution", "ALIAS_ONLY_UNRESOLVED"),
    ("canonical_identity_source", None),
])
def test_research_shadow_requires_cfbd_source_resolution(key, value):
    candidate, forward = _candidate(), _features()
    forward["source_manifest"][key] = value
    forward["source_manifest_sha256"] = _digest(forward["source_manifest"])
    with pytest.raises(NCAAFShadowScoreBlocked) as error:
        score_ncaaf_research_shadow(candidate, forward)
    assert error.value.code == "NCAAF_SHADOW_MANIFEST_INVALID"


def test_research_shadow_cannot_override_cfbd_source_kickoff():
    candidate, forward = _candidate(), _features()
    forward["source_manifest"]["canonical_event_start_time"] = "2026-10-09T22:00:00+00:00"
    forward["source_manifest_sha256"] = _digest(forward["source_manifest"])
    with pytest.raises(NCAAFShadowScoreBlocked) as error:
        score_ncaaf_research_shadow(candidate, forward)
    assert error.value.code == "NCAAF_SHADOW_CANONICAL_START_MISMATCH"



def test_unchanged_source_manifest_does_not_authorize_changed_numeric_features():
    candidate, forward = _candidate(), _features()
    forward["features"][FEATURE_NAMES[0]] = 0.9
    # Before the new bound hash this silently changed fitted raw output while
    # retaining exactly the same source_manifest_sha256.
    with pytest.raises(NCAAFShadowScoreBlocked) as error:
        score_ncaaf_research_shadow(candidate, forward)
    assert error.value.code == "NCAAF_SHADOW_FEATURE_HASH_MISMATCH"


def test_shadow_fails_if_feature_digest_is_removed_from_attested_manifest():
    candidate, forward = _candidate(), _features()
    forward["source_manifest"].pop("features_sha256")
    forward["source_manifest_sha256"] = _digest(forward["source_manifest"])
    with pytest.raises(NCAAFShadowScoreBlocked) as error:
        score_ncaaf_research_shadow(candidate, forward)
    assert error.value.code == "NCAAF_SHADOW_FEATURE_HASH_MISMATCH"


def test_shadow_fails_if_returned_feature_hash_disagrees_with_manifest():
    candidate, forward = _candidate(), _features()
    forward["features_sha256"] = "f" * 64
    with pytest.raises(NCAAFShadowScoreBlocked) as error:
        score_ncaaf_research_shadow(candidate, forward)
    assert error.value.code == "NCAAF_SHADOW_FEATURE_HASH_MISMATCH"



@pytest.mark.parametrize("source_flag", [None, "false", 0])
def test_shadow_rejects_missing_neutral_source_verification(source_flag):
    candidate, forward = _candidate(), _features()
    forward["source_manifest"]["canonical_neutral_site"] = source_flag
    forward["source_manifest_sha256"] = _digest(forward["source_manifest"])
    with pytest.raises(NCAAFShadowScoreBlocked) as err:
        score_ncaaf_research_shadow(candidate, forward)
    assert err.value.code == "NCAAF_SHADOW_NEUTRAL_SITE_SOURCE_MISSING"


def test_shadow_rejects_neutral_site_disagreement_even_if_manifest_rehashed():
    candidate, forward = _candidate(), _features()
    forward["source_manifest"]["canonical_neutral_site"] = True
    forward["source_manifest_sha256"] = _digest(forward["source_manifest"])
    with pytest.raises(NCAAFShadowScoreBlocked) as err:
        score_ncaaf_research_shadow(candidate, forward)
    assert err.value.code == "NCAAF_SHADOW_NEUTRAL_SITE_SOURCE_CONTRADICTION"


# Incident #665 engineering-audit closure: each registered research-only failure
# must be reached by a deterministic negative regression, not just documented.
@pytest.mark.parametrize("bad", [None, [], 42, "research"])
def test_shadow_invalid_input_is_typed_and_cannot_score(bad):
    with pytest.raises(NCAAFShadowScoreBlocked) as exc:
        score_ncaaf_research_shadow(bad, _features())
    assert exc.value.code == "NCAAF_SHADOW_INPUT_INVALID"


@pytest.mark.parametrize("bad_version,bad_hash", [
    ("BAD_VERSION", None),
    (None, "not-a-sha256"),
])
def test_shadow_rejects_invalid_candidate_artifact_identity(bad_version, bad_hash):
    candidate = _candidate()
    if bad_version is not None:
        candidate["model_artifact_version"] = bad_version
    if bad_hash is not None:
        candidate["training_dataset_hash"] = bad_hash
    with pytest.raises(NCAAFShadowScoreBlocked) as exc:
        score_ncaaf_research_shadow(candidate, _features())
    assert exc.value.code == "NCAAF_SHADOW_ARTIFACT_IDENTITY_INVALID"


def test_shadow_rejects_non_mapping_artifact_before_checksum_checks():
    candidate = _candidate()
    candidate["artifact_payload"] = []
    with pytest.raises(NCAAFShadowScoreBlocked) as exc:
        score_ncaaf_research_shadow(candidate, _features())
    assert exc.value.code == "NCAAF_SHADOW_ARTIFACT_INVALID"


@pytest.mark.parametrize("field,bad", [
    ("status", "FINAL_APPROVED"),
    ("can_execute", True),
])
def test_shadow_forward_package_must_stay_research_only(field, bad):
    forward = _features()
    forward[field] = bad
    with pytest.raises(NCAAFShadowScoreBlocked) as exc:
        score_ncaaf_research_shadow(_candidate(), forward)
    assert exc.value.code == "NCAAF_SHADOW_FORWARD_PACKAGE_INVALID"


@pytest.mark.parametrize("bad_timestamp", ["not-a-date", "2026-10-10T01:00:00"])
def test_shadow_rejects_malformed_or_naive_canonical_timestamp(bad_timestamp):
    forward = _features()
    forward["source_manifest"]["event_start_time"] = bad_timestamp
    forward["source_manifest_sha256"] = _digest(forward["source_manifest"])
    with pytest.raises(NCAAFShadowScoreBlocked) as exc:
        score_ncaaf_research_shadow(_candidate(), forward)
    assert exc.value.code == "NCAAF_SHADOW_TIME_INVALID"


@pytest.mark.parametrize("bad_vector", [
    [0.0],                             # wrong number of fitted coefficients
    None,                              # missing vector
    [True] * len(FEATURE_NAMES),       # booleans are not fitted floats
])
def test_shadow_rejects_invalid_artifact_vectors_with_rehashed_payload(bad_vector):
    candidate = _candidate()
    candidate["artifact_payload"]["scaler_mean"] = bad_vector
    candidate["artifact_checksum"] = _digest(candidate["artifact_payload"])
    with pytest.raises(NCAAFShadowScoreBlocked) as exc:
        score_ncaaf_research_shadow(candidate, _features())
    assert exc.value.code == "NCAAF_SHADOW_ARTIFACT_VECTOR_INVALID"


def test_shadow_detects_finite_inputs_that_overflow_fitted_linear_sum():
    candidate = _candidate()
    forward = _features()
    # Both inputs are individually finite. Their product must still fail closed.
    candidate["artifact_payload"]["coefficients"][0] = 1e308
    candidate["artifact_checksum"] = _digest(candidate["artifact_payload"])
    forward["features"][FEATURE_NAMES[0]] = 1e308
    features_hash = _digest(forward["features"])
    forward["features_sha256"] = features_hash
    forward["source_manifest"]["features_sha256"] = features_hash
    forward["source_manifest_sha256"] = _digest(forward["source_manifest"])
    with pytest.raises(NCAAFShadowScoreBlocked) as exc:
        score_ncaaf_research_shadow(candidate, forward)
    assert exc.value.code == "NCAAF_SHADOW_NUMERIC_OVERFLOW"


def test_research_only_probability_cannot_be_imported_by_runtime_entrypoints():
    # A diagnostic raw-probability key must never become a production input.
    # Scan every non-test module in the engine (not a hand-picked list that can
    # silently go stale) and require the scan to be non-vacuous.
    from pathlib import Path
    engine = Path(__file__).resolve().parents[1]
    scorer = engine / "v17" / "ncaaf_result_form_shadow_scorer.py"
    scanned = 0
    offenders = []
    for file in engine.rglob("*.py"):
        rel = file.relative_to(engine)
        if (
            file == scorer
            or rel.parts[0] == "tests"
            or file.name.startswith("test_")
            or "__pycache__" in rel.parts
        ):
            continue
        scanned += 1
        text = file.read_text(encoding="utf-8", errors="replace")
        if "research_raw_probability" in text or "ncaaf_result_form_shadow_scorer" in text:
            offenders.append(str(rel))
    assert scanned > 100, f"isolation scan looked at only {scanned} modules"
    assert offenders == [], f"production modules consume research-only output: {offenders}"
