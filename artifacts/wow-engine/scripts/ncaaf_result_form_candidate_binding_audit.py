"""Nonpublishing, read-only NCAAF result/form fitted-candidate binding audit.

No source or candidate DB connection. Caller may supply an in-memory candidate
export from an authorized session; do not commit fitted coefficients or secrets.
Research shadow calculations are aggregate-only, never row probabilities/picks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
FAMILY = "NCAAF_RESULT_FORM_LOGIT_V1"
SCHEMA = "NCAAF_RESULT_FORM_PRIOR_V1"
POLICY = "NCAAF_SETTLED_PRIOR_RESULTS_RECONSTRUCTION_V1"
CALIBRATOR = "IDENTITY_RAW_PROBABILITY_V1"
FEATURE_NAMES = (
    "home_win_rate_prior", "away_win_rate_prior",
    "home_point_diff_prior", "away_point_diff_prior",
    "home_games_prior_log", "away_games_prior_log",
    "home_rest_days_capped", "away_rest_days_capped", "neutral_site",
)


class CandidateBindingBlocked(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _assert(condition: bool, code: str) -> None:
    if not condition:
        raise CandidateBindingBlocked(code)


def _hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _num(value: object) -> float:
    _assert(type(value) in (int, float), "NCAAF_CANDIDATE_NUMERIC_INPUT_INVALID")
    number = float(value)
    _assert(math.isfinite(number), "NCAAF_CANDIDATE_NUMERIC_INPUT_INVALID")
    return number


def audit_metadata(binding: dict, features: dict) -> dict:
    _assert(binding.get("model_family") == FAMILY
            and binding.get("feature_schema_version") == SCHEMA
            and binding.get("source_policy_id") == POLICY
            and binding.get("calibrator_method") == CALIBRATOR
            and binding.get("calibrator_selection_version") == "FORWARD_CALIBRATION_SELECTION_V2"
            and binding.get("split_policy") == "CHRONOLOGICAL_60_20_20"
            and tuple(binding.get("feature_names") or ()) == FEATURE_NAMES,
            "NCAAF_CANDIDATE_EXACT_CONTRACT_MISMATCH")
    _assert(binding.get("candidate_lifecycle_state") == "CANDIDATE"
            and binding.get("candidate_active") is False
            and binding.get("candidate_promoted") is False
            and binding.get("candidate_probability_publishable") is False
            and binding.get("candidate_bound_certification") == "NOT_ESTABLISHED"
            and binding.get("candidate_bound_replay_receipt_count") == 0
            and binding.get("rank_eligible") is False
            and binding.get("probability_publishable") is False
            and binding.get("can_execute") is False,
            "NCAAF_CANDIDATE_FORBIDDEN_PUBLICATION_CLAIM")
    _assert(binding.get("model_artifact_checksum") == binding.get("model_artifact_checksum_recomputed")
            and len(str(binding.get("model_artifact_checksum") or "")) == 64,
            "NCAAF_CANDIDATE_CHECKSUM_EVIDENCE_INVALID")
    _assert(features.get("sport") == "NCAAF"
            and features.get("candidate_model_family") == FAMILY
            and features.get("feature_schema_version") == SCHEMA
            and features.get("source_policy_id") == POLICY
            and features.get("probability_publishable") is False
            and features.get("can_execute") is False,
            "NCAAF_CANDIDATE_FEATURE_SOURCE_MISMATCH")
    events = features.get("events")
    _assert(isinstance(events, list) and len(events) == 44
            and binding.get("forward_cohort_events") == 44
            and len({e.get("official_event_id") for e in events}) == 44,
            "NCAAF_CANDIDATE_FORWARD_COHORT_INCOMPLETE")
    for e in events:
        _assert(tuple((e.get("features") or {}).keys()) == FEATURE_NAMES
                and e.get("probability_publishable") is False
                and e.get("can_execute") is False
                and e.get("model_probability") is None
                and e.get("calibrated_probability") is None,
                "NCAAF_CANDIDATE_UNSUPPORTED_ROW_AUTHORITY")
        [_num(e["features"][name]) for name in FEATURE_NAMES]
    return {
        "status": "NCAAF_CANDIDATE_METADATA_AND_44_INPUTS_REPLAY_PASS",
        "candidate_id": binding["candidate_id"],
        "model_artifact_checksum": binding["model_artifact_checksum"],
        "events_verified": 44,
        "candidate_bound_certification": "NOT_ESTABLISHED",
        "probability_publishable": False,
        "can_execute": False,
    }


def audit_private_artifact(binding: dict, features: dict, candidate: dict) -> dict:
    audit_metadata(binding, features)
    payload = candidate.get("artifact_payload")
    calibrator = candidate.get("calibrator_payload")
    _assert(isinstance(payload, dict) and isinstance(calibrator, dict),
            "NCAAF_CANDIDATE_MISSING_FITTED_PACKAGE")
    _assert(candidate.get("candidate_id") == binding.get("candidate_id")
            and candidate.get("model_artifact_version") == binding.get("model_artifact_version")
            and candidate.get("artifact_checksum") == binding.get("model_artifact_checksum"),
            "NCAAF_CANDIDATE_IDENTITY_MISMATCH")
    _assert(_hash(payload) == binding["model_artifact_checksum"],
            "NCAAF_CANDIDATE_MODEL_HASH_MISMATCH")
    _assert(payload.get("artifact_format") == "STANDARDIZED_LOGISTIC_JSON_V1"
            and payload.get("model_family") == FAMILY
            and tuple(payload.get("feature_names") or ()) == FEATURE_NAMES
            and payload.get("calibrator_selection_version") == "FORWARD_CALIBRATION_SELECTION_V2"
            and payload.get("split_policy") == "CHRONOLOGICAL_60_20_20",
            "NCAAF_CANDIDATE_FITTED_PAYLOAD_MISMATCH")
    _assert(calibrator.get("method") == CALIBRATOR
            and calibrator.get("bins") == []
            and calibrator.get("selection_version") == "FORWARD_CALIBRATION_SELECTION_V2"
            and (calibrator.get("selection") or {}).get("test_block_used_for_selection") is False,
            "NCAAF_CANDIDATE_CALIBRATOR_UNSUPPORTED")
    _assert(candidate.get("active") is False and candidate.get("promoted") is False
            and candidate.get("probability_publishable") is False
            and candidate.get("can_execute") is False,
            "NCAAF_CANDIDATE_FORBIDDEN_PUBLICATION_CLAIM")
    coeff, mean, scale = (payload.get(k) for k in ("coefficients", "scaler_mean", "scaler_scale"))
    _assert(all(isinstance(v, list) and len(v) == 9 for v in (coeff, mean, scale)),
            "NCAAF_CANDIDATE_VECTOR_DIMENSION_INVALID")
    _assert(all(_num(z) > 0 for z in scale), "NCAAF_CANDIDATE_SCALE_INVALID")
    intercept = _num(payload.get("intercept"))
    probs = []
    for event in features["events"]:
        x = [_num(event["features"][name]) for name in FEATURE_NAMES]
        logit = intercept + sum(_num(coeff[i]) * (x[i] - _num(mean[i])) / _num(scale[i]) for i in range(9))
        score = 1 / (1 + math.exp(-logit)) if logit >= 0 else math.exp(logit) / (1 + math.exp(logit))
        _assert(0 < score < 1, "NCAAF_CANDIDATE_RESEARCH_NUMERIC_INVALID")
        probs.append(score)
    return {
        "status": "NCAAF_CANDIDATE_RESEARCH_SHADOW_BINDING_PASS",
        "candidate_id": binding["candidate_id"],
        "events_verified": 44,
        "finite_probability_count": len(probs),
        "research_only_probability_min": min(probs),
        "research_only_probability_max": max(probs),
        "candidate_bound_certification": "NOT_ESTABLISHED",
        "model_probability": None,
        "calibrated_lower_bound": None,
        "probability_publishable": False,
        "can_execute": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--candidate-private", type=Path)
    args = parser.parse_args()
    try:
        b = json.loads(args.binding.read_text())
        f = json.loads(args.features.read_text())
        result = audit_private_artifact(b, f, json.loads(args.candidate_private.read_text())) if args.candidate_private else audit_metadata(b, f)
    except (CandidateBindingBlocked, ValueError, KeyError, OverflowError) as e:
        print(json.dumps({"status": "BLOCKED", "code": getattr(e, "code", "NCAAF_CANDIDATE_AUDIT_INPUT_INVALID"), "can_execute": False}))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
