"""NCAAB candidate-bound fitted research inference, never a production bridge.

Consumes the separate NCAAB pregame hydration receipt already generated from
source-pinned SportsDataverse team boxes. It neither hydrates, certifies nor
publishes a model, and cannot supply a governed calibrated lower bound.
"""
from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import Mapping
from hashlib import sha256
from math import exp, isfinite
import json
from typing import Any

CAN_EXECUTE = False
MODEL_FAMILY = "NCAAB_RESULT_FORM_LOGIT_V1"
FEATURE_SCHEMA = "NCAAB_TEAM_FORM_PRIOR_V1"
SOURCE_POLICY = "SPORTSDATAVERSE_NCAAB_TEAM_BOX_CC_BY_4_V1"
FEATURE_NAMES = (
    "home_recent_win_rate", "away_recent_win_rate", "home_recent_point_diff", "away_recent_point_diff",
    "home_recent_points_for", "away_recent_points_for", "home_recent_rebound_margin_proxy",
    "away_recent_rebound_margin_proxy", "home_recent_turnovers", "away_recent_turnovers",
    "home_recent_fg_pct", "away_recent_fg_pct", "home_recent_3p_pct", "away_recent_3p_pct",
    "home_games_prior_log", "away_games_prior_log", "home_rest_days_capped", "away_rest_days_capped",
)


class NCAABShadowBlocked(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(valid: bool, code: str) -> None:
    if not valid:
        raise NCAABShadowBlocked(code)


def _hash(value: Any) -> str:
    return sha256(json.dumps(value,sort_keys=True,separators=(",", ":"),default=str).encode()).hexdigest()


def _dt(value: Any) -> datetime:
    try:
        date = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError,TypeError,AttributeError) as exc:
        raise NCAABShadowBlocked("NCAAB_SHADOW_TIMESTAMP_INVALID") from exc
    _require(date.utcoffset() is not None, "NCAAB_SHADOW_TIMESTAMP_INVALID")
    return date.astimezone(timezone.utc)


def score_ncaab_candidate_shadow(
    hydrated: Mapping[str, Any], candidate: Mapping[str, Any], *, as_of_utc: Any
) -> dict[str, Any]:
    """Numerically invoke exact fitted research artifact on a frozen research feature receipt.

    This is pure candidate research: the outer caller must own authoritative
    hydration, trusted historical source and any eventual certification.
    """
    now = _dt(as_of_utc)
    _require(isinstance(hydrated, Mapping)
             and hydrated.get("status") == "RESEARCH_FEATURES_READY"
             and hydrated.get("sport") == "NCAAB"
             and hydrated.get("model_family") == MODEL_FAMILY
             and hydrated.get("feature_schema_version") == FEATURE_SCHEMA
             and hydrated.get("source_policy_id") == SOURCE_POLICY
             and hydrated.get("market_features_used") is False
             and hydrated.get("research_only") is True
             and hydrated.get("probability_publishable") is False
             and hydrated.get("can_execute") is False,
             "NCAAB_SHADOW_FEATURE_PACKAGE_INVALID")
    evidence = hydrated.get("source_evidence")
    _require(isinstance(evidence, Mapping) and hydrated.get("input_sha256") == _hash(evidence),
             "NCAAB_SHADOW_SOURCE_RECEIPT_HASH_MISMATCH")
    event_id = str(hydrated.get("official_event_id") or "")
    _require(event_id.startswith("NCAAB:") and evidence.get("target_event_id") == event_id
             and isinstance(evidence.get("prior_game_sources"), list)
             and len(evidence["prior_game_sources"]) >= 1
             and isinstance(evidence.get("target_source_manifest_sha256"), str)
             and len(evidence["target_source_manifest_sha256"]) == 64,
             "NCAAB_SHADOW_SOURCE_RECEIPT_INVALID")
    kickoff = _dt(evidence.get("event_start_time"))
    source_as_of = _dt(evidence.get("feature_as_of"))
    _require(source_as_of <= now < kickoff and source_as_of < kickoff
             and (kickoff-source_as_of).total_seconds()<=24*3600,
             "NCAAB_SHADOW_SOURCE_NOT_PREGAME")
    _require(candidate.get("sport") == "NCAAB" and candidate.get("league") == "NCAAB"
             and candidate.get("market_family") == "OUTRIGHT_WINNER"
             and candidate.get("model_family") == MODEL_FAMILY
             and candidate.get("feature_schema_version") == FEATURE_SCHEMA
             and candidate.get("source_policy_id") == SOURCE_POLICY
             and candidate.get("source_review_status") in ("CC_BY_4_0_PROVENANCE_READY", "PASS")
             and candidate.get("lifecycle_state") == "CANDIDATE"
             and candidate.get("research_screen_pass") is True
             and all(candidate.get(key) is False for key in (
                 "active", "promoted", "automatic_certification", "automatic_promotion",
                 "can_execute", "probability_publishable"
             )), "NCAAB_SHADOW_CANDIDATE_LIFECYCLE_INVALID")
    _require(_dt(candidate.get("created_at")) <= now, "NCAAB_SHADOW_CANDIDATE_POST_AS_OF")
    data_hash = str(candidate.get("training_dataset_hash") or "")
    code_sha = str(candidate.get("training_code_sha") or "")
    _require(len(data_hash)==64 and len(code_sha)>=12
             and candidate.get("model_artifact_version") == f"{MODEL_FAMILY}_{data_hash[:16]}_{code_sha[:12]}",
             "NCAAB_SHADOW_CANDIDATE_VERSION_INVALID")
    model = candidate.get("artifact_payload")
    _require(isinstance(model, Mapping), "NCAAB_SHADOW_ARTIFACT_MISSING")
    _require(_hash(model) == candidate.get("artifact_checksum"), "NCAAB_SHADOW_ARTIFACT_CHECKSUM_MISMATCH")
    _require(model.get("model_family") == MODEL_FAMILY
             and model.get("artifact_format") == "STANDARDIZED_LOGISTIC_JSON_V1"
             and model.get("split_policy") == "CHRONOLOGICAL_60_20_20"
             and tuple(model.get("feature_names") or ()) == FEATURE_NAMES,
             "NCAAB_SHADOW_ARTIFACT_SCHEMA_INVALID")
    for name in ("coefficients", "scaler_mean", "scaler_scale"):
        values = model.get(name)
        _require(isinstance(values,list) and len(values)==len(FEATURE_NAMES)
                 and all(type(value) in (int,float) and isfinite(float(value)) for value in values),
                 "NCAAB_SHADOW_ARTIFACT_NUMERIC_INVALID")
    _require(all(float(x)>0 for x in model["scaler_scale"])
             and type(model.get("intercept")) in (int,float)
             and isfinite(float(model["intercept"])), "NCAAB_SHADOW_ARTIFACT_NUMERIC_INVALID")
    cal=candidate.get("calibrator_payload")
    _require(isinstance(cal,Mapping) and cal.get("method") == "IDENTITY_RAW_PROBABILITY_V1"
             and cal.get("bins") == []
             and cal.get("selection_version") == "FORWARD_CALIBRATION_SELECTION_V2"
             and isinstance(cal.get("selection"),Mapping)
             and cal["selection"].get("test_block_used_for_selection") is False,
             "NCAAB_SHADOW_CALIBRATOR_UNSUPPORTED")
    names,vector,features=hydrated.get("feature_names"),hydrated.get("feature_values"),hydrated.get("features")
    _require(tuple(names or ()) == FEATURE_NAMES and isinstance(vector,list)
             and isinstance(features,Mapping) and set(features)==set(FEATURE_NAMES)
             and len(vector)==len(FEATURE_NAMES)
             and all(type(v) in (float,int) and isfinite(float(v)) for v in vector),
             "NCAAB_SHADOW_FEATURE_VECTOR_MISMATCH")
    _require(all(type(features[name]) in (float,int) and isfinite(float(features[name]))
                 and float(features[name]) == float(vector[i]) for i,name in enumerate(FEATURE_NAMES)),
             "NCAAB_SHADOW_FEATURE_VECTOR_MISMATCH")
    z=float(model["intercept"])
    for i,v in enumerate(vector):
        z += float(model["coefficients"][i]) * (
            (float(v)-float(model["scaler_mean"][i]))/float(model["scaler_scale"][i])
        )
    p = 1/(1+exp(-z)) if z>=0 else exp(z)/(1+exp(z))
    return {
        "status":"FITTED_SHADOW_ONLY", "sport":"NCAAB", "official_event_id":event_id,
        "event_start_time_utc":kickoff.isoformat(),"feature_as_of_utc":source_as_of.isoformat(),
        "model_family":MODEL_FAMILY,"feature_schema_version":FEATURE_SCHEMA,
        "candidate_id":candidate.get("candidate_id"),"model_artifact_version":candidate["model_artifact_version"],
        "model_artifact_checksum":candidate["artifact_checksum"],"source_evidence_sha256":hydrated["input_sha256"],
        "research_probability_home":round(p,9),"research_probability_away":round(1-p,9),
        "calibrator_method":"IDENTITY_RAW_PROBABILITY_V1","calibrated_lower_bound":None,
        "blocking_reason":"INDEPENDENT_CERTIFICATION_AND_CURRENT_SOURCE_PROVENANCE_REQUIRED",
        "immutable_prediction_receipt":None,"automatic_certification":False,"automatic_promotion":False,
        "probability_publishable":False,"rank_eligible":False,"can_execute":False,
    }


__all__=["CAN_EXECUTE","NCAABShadowBlocked","MODEL_FAMILY","FEATURE_SCHEMA","score_ncaab_candidate_shadow"]