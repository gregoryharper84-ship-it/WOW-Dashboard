"""V17 team/event feature-consumption governance foundation.

This module is observability-only. It does not calculate, adjust, calibrate, rank,
or substitute sporting probability. Its job is to make the distinction between
an input being present and a scorer proving that the input was numerically
consumed explicit and machine-readable.

A receipt must never infer consumption from research prose, market availability,
or generic model knowledge. If the scorer does not emit explicit
``consumed_feature_ids``, the receipt reports UNVERIFIED rather than claiming
use. Versioned runtime feature contracts may add internally derived model
features to the expected universe, but they still cannot prove consumption.

can_execute=False is unconditional.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False

CANONICAL_FEATURE_ROLES = frozenset(
    {
        "MODEL_PRIMARY",
        "MODEL_INTERACTION",
        "FAILURE_PATH",
        "CALIBRATION_CONTEXT",
        "UNCERTAINTY_INPUT",
        "MARKET_ROLE_ONLY",
        "MARKET_PRIOR_BOUNDED",
        "CONTRADICTION_ONLY",
        "VALUE_ONLY",
        "RESEARCH_ONLY",
    }
)
NON_MODEL_INPUT_ROLES = frozenset({"CONTRACT_ONLY", "UNDECLARED"})
FEATURE_ROLES = CANONICAL_FEATURE_ROLES | NON_MODEL_INPUT_ROLES

FEATURE_VALUE_STATES = frozenset(
    {
        "AVAILABLE",
        "MISSING",
        "STALE",
        "CONFLICTED",
        "NOT_APPLICABLE",
        "IMPUTED_CERTIFIED",
        "UNSUPPORTED",
    }
)
CONSUMPTION_STATES = frozenset({"CONSUMED", "REJECTED", "NOT_VERIFIED"})
CONSUMPTION_VERIFICATION_STATES = frozenset({"VERIFIED", "UNVERIFIED"})

CONTRACT_ONLY_FIELDS = frozenset(
    {
        "official_event_id",
        "event_field_identity",
        "home_team",
        "away_team",
        "participant",
        "opponent",
        "venue",
        "settlement_basis",
        "competition_rules",
        "retirement_settlement_rules",
        "tournament_or_h2h_settlement",
        "withdrawal_dq_rules",
        "tie_no_result_settlement_rules",
        "home_draw_away_outcome_space",
        "no_contest_draw_outcome_space",
        "market_type",
    }
)


@dataclass(frozen=True)
class FeatureContractEntry:
    feature_id: str
    role: str = "UNDECLARED"
    required: bool = True
    critical: bool = False

    def __post_init__(self) -> None:
        if self.role not in FEATURE_ROLES:
            raise ValueError(f"unsupported feature role: {self.role}")
        if not str(self.feature_id or "").strip():
            raise ValueError("feature_id is required")

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "role": self.role,
            "required": self.required,
            "critical": self.critical,
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _evidence(req: Any) -> Mapping[str, Any]:
    return _mapping(getattr(req, "sport_specific_evidence", None))


def _request_value(req: Any, feature_id: str) -> Any:
    direct = getattr(req, feature_id, None)
    if direct not in (None, "", [], {}):
        return direct
    return _evidence(req).get(feature_id)


def _request_feature_meta(req: Any, feature_id: str) -> Mapping[str, Any]:
    evidence = _evidence(req)
    raw = evidence.get(feature_id)
    metadata = evidence.get("feature_metadata")
    candidate = metadata.get(feature_id) if isinstance(metadata, Mapping) else None

    if isinstance(raw, Mapping) and isinstance(candidate, Mapping):
        # A mapping-valued feature (for example a calibration artifact) is the
        # feature payload, not a substitute for its separate provenance record.
        # Preserve both and let explicit feature_metadata win on audit keys.
        return {**raw, **candidate}
    if isinstance(candidate, Mapping):
        return candidate
    if isinstance(raw, Mapping):
        return raw
    return {}


def _result_feature_observation(result: Mapping[str, Any], feature_id: str) -> Mapping[str, Any]:
    observations = result.get("feature_observations")
    if not isinstance(observations, Mapping):
        return {}
    candidate = observations.get(feature_id)
    return candidate if isinstance(candidate, Mapping) else {}


def _value_state(req: Any, result: Mapping[str, Any], feature_id: str) -> str:
    observation = _result_feature_observation(result, feature_id)
    token = str(
        observation.get("value_status")
        or observation.get("quality_status")
        or observation.get("missingness_status")
        or ""
    ).strip().upper()
    if token in FEATURE_VALUE_STATES:
        return token

    value = _request_value(req, feature_id)
    available = value not in (None, "", [], {})
    if not available:
        return "MISSING"
    meta = _request_feature_meta(req, feature_id)
    for key in ("quality_status", "value_status", "missingness_status"):
        token = str(meta.get(key) or "").strip().upper()
        if token in FEATURE_VALUE_STATES:
            return token
    freshness = str(meta.get("freshness_status") or "").strip().upper()
    if freshness == "STALE":
        return "STALE"
    return "AVAILABLE"


def _freshness(req: Any, result: Mapping[str, Any], feature_id: str) -> str:
    observation = _result_feature_observation(result, feature_id)
    token = str(observation.get("freshness_status") or "").strip().upper()
    if token:
        return token
    meta = _request_feature_meta(req, feature_id)
    token = str(meta.get("freshness_status") or "").strip().upper()
    if token:
        return token
    evidence = _evidence(req)
    freshness = evidence.get("feature_freshness")
    if isinstance(freshness, Mapping):
        token = str(freshness.get(feature_id) or "").strip().upper()
        if token:
            return token
    return "UNKNOWN"


def _provenance(req: Any, result: Mapping[str, Any], feature_id: str) -> dict[str, Any]:
    observation = _result_feature_observation(result, feature_id)
    meta = _request_feature_meta(req, feature_id)
    evidence = _evidence(req)
    provenance = evidence.get("feature_provenance")
    provenance_entry = provenance.get(feature_id) if isinstance(provenance, Mapping) else None
    provenance_map = provenance_entry if isinstance(provenance_entry, Mapping) else {}
    return {
        "source": observation.get("source") or meta.get("source") or provenance_map.get("source"),
        "source_timestamp": observation.get("source_timestamp") or meta.get("source_timestamp") or provenance_map.get("source_timestamp"),
        "observed_at": observation.get("observed_at") or meta.get("observed_at") or provenance_map.get("observed_at"),
        "provenance_id": observation.get("provenance_id") or meta.get("provenance_id") or provenance_map.get("provenance_id"),
    }


def _string_ids(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return ()
    return tuple(
        dict.fromkeys(
            str(item).strip()
            for item in value
            if str(item or "").strip()
        )
    )


def _result_ids(result: Mapping[str, Any], key: str) -> tuple[str, ...]:
    return _string_ids(result.get(key))


def _runtime_feature_contract(sport: str) -> Any | None:
    """Load the runtime-only feature contract lazily to avoid an import cycle.

    Development-only schemas must never become live receipt expectations merely
    because their code imports successfully.
    """
    try:
        from v17.team_event_feature_contract_registry import (  # local import by design
            RUNTIME_CONTRACT,
            feature_contract_for_sport,
        )

        contract = feature_contract_for_sport(str(sport or "").upper().strip())
        if contract.schema_state == RUNTIME_CONTRACT:
            return contract
    except (ImportError, KeyError, ValueError):
        return None
    return None


def _feature_role(
    feature_id: str,
    declared_roles: Mapping[str, str],
    *,
    request_inputs: frozenset[str],
    runtime_model_features: frozenset[str],
) -> str:
    declared = str(declared_roles.get(feature_id) or "").strip().upper()
    if declared in FEATURE_ROLES:
        return declared
    if feature_id in CONTRACT_ONLY_FIELDS:
        return "CONTRACT_ONLY"
    if feature_id == "calibration_artifact":
        return "CALIBRATION_CONTEXT"
    # Once an exact runtime model vector is declared, request-level bridge fields
    # outside that vector are governance/evidence contract fields, not hidden
    # numerical model inputs. This prevents them from being silently promoted.
    if runtime_model_features and feature_id in request_inputs and feature_id not in runtime_model_features:
        return "CONTRACT_ONLY"
    return "UNDECLARED"


def _expected_source(feature_id: str, request_inputs: frozenset[str], runtime_model_features: frozenset[str]) -> str:
    in_request = feature_id in request_inputs
    in_model = feature_id in runtime_model_features
    if in_request and in_model:
        return "REQUEST_AND_MODEL_FEATURE_CONTRACT"
    if in_model:
        return "MODEL_FEATURE_CONTRACT"
    if in_request:
        return "REQUEST_CONTRACT"
    return "DECLARED_ROLE_ONLY"


def build_feature_consumption_receipt(
    *,
    req: Any,
    sport: str,
    controlling_specialist: str,
    required_inputs: Sequence[str],
    result: Mapping[str, Any],
    feature_schema_version: str | None = None,
    declared_feature_roles: Mapping[str, str] | None = None,
    critical_features: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build a non-invasive audit receipt for one successful score package.

    Consumption is verified only from the scorer's explicit
    ``consumed_feature_ids`` list. Request presence, a model-component name,
    registry membership, or a required-input declaration is never proof of
    numerical use.
    """
    explicit_roles = dict(declared_feature_roles or {})
    runtime_contract = _runtime_feature_contract(sport)
    runtime_features = tuple(
        feature.feature_id for feature in (runtime_contract.features if runtime_contract else ())
    )
    runtime_model_features = frozenset(runtime_features)
    runtime_roles = {
        feature.feature_id: feature.role
        for feature in (runtime_contract.features if runtime_contract else ())
    }
    role_map = {**runtime_roles, **explicit_roles}

    request_expected = tuple(
        dict.fromkeys(str(v).strip() for v in required_inputs if str(v or "").strip())
    )
    request_inputs = frozenset(request_expected)
    expected = tuple(
        dict.fromkeys(
            [
                *request_expected,
                *runtime_features,
                *(str(v).strip() for v in explicit_roles if str(v or "").strip()),
            ]
        )
    )

    registry_critical = {
        feature.feature_id
        for feature in (runtime_contract.features if runtime_contract else ())
        if feature.critical
    }
    critical = frozenset(
        registry_critical
        | {
            str(v).strip()
            for v in (critical_features or ())
            if str(v or "").strip()
        }
    )
    consumed = frozenset(_result_ids(result, "consumed_feature_ids"))
    rejected = frozenset(_result_ids(result, "rejected_feature_ids"))
    consumption_verified = "consumed_feature_ids" in result and isinstance(
        result.get("consumed_feature_ids"), (list, tuple)
    )

    feature_details: list[dict[str, Any]] = []
    available_count = 0
    missing_count = 0
    stale_count = 0
    rejected_count = 0
    consumed_count = 0

    unavailable_states = {"MISSING", "NOT_APPLICABLE", "UNSUPPORTED"}
    for feature_id in expected:
        value_state = _value_state(req, result, feature_id)
        freshness = _freshness(req, result, feature_id)
        if value_state not in unavailable_states:
            available_count += 1
        if value_state == "MISSING":
            missing_count += 1
        if value_state == "STALE" or freshness == "STALE":
            stale_count += 1

        if feature_id in rejected:
            consumption_status = "REJECTED"
            rejected_count += 1
        elif feature_id in consumed:
            consumption_status = "CONSUMED"
            consumed_count += 1
        else:
            consumption_status = "NOT_VERIFIED"

        provenance = _provenance(req, result, feature_id)
        feature_details.append(
            {
                "feature_id": feature_id,
                "role": _feature_role(
                    feature_id,
                    role_map,
                    request_inputs=request_inputs,
                    runtime_model_features=runtime_model_features,
                ),
                "expected_source": _expected_source(
                    feature_id, request_inputs, runtime_model_features
                ),
                "required": True,
                "critical": feature_id in critical,
                "value_status": value_state,
                "freshness_status": freshness,
                "consumption_status": consumption_status,
                **provenance,
            }
        )

    unexpected_consumed = sorted(consumed.difference(expected))
    unexpected_rejected = sorted(rejected.difference(expected))
    consumed_but_unavailable = sorted(
        detail["feature_id"]
        for detail in feature_details
        if detail["consumption_status"] == "CONSUMED"
        and detail["value_status"] in unavailable_states
    )
    schema = str(
        feature_schema_version
        or result.get("feature_schema_version")
        or (runtime_contract.schema_version if runtime_contract else None)
        or "UNDECLARED"
    ).strip()
    verification_status = "VERIFIED" if consumption_verified else "UNVERIFIED"
    role_contract_complete = all(
        detail["role"] != "UNDECLARED" for detail in feature_details
    )
    provenance_complete = all(
        detail["value_status"] not in unavailable_states
        and bool(detail.get("provenance_id") or detail.get("source"))
        for detail in feature_details
        if detail["role"] in CANONICAL_FEATURE_ROLES
    )
    critical_consumption_complete = all(
        detail["consumption_status"] == "CONSUMED"
        for detail in feature_details
        if detail["critical"]
    )
    receipt_complete = bool(
        schema != "UNDECLARED"
        and verification_status == "VERIFIED"
        and role_contract_complete
        and provenance_complete
        and critical_consumption_complete
        and not unexpected_consumed
        and not unexpected_rejected
        and not consumed_but_unavailable
    )

    prediction_id = str(result.get("prediction_id") or result.get("candidate_id") or "").strip()
    event_id = str(
        result.get("official_event_id")
        or getattr(req, "official_event_id", "")
        or ""
    ).strip()
    model_version = str(
        result.get("model_artifact_version")
        or result.get("model_version")
        or ""
    ).strip()
    generated_at = str(
        result.get("immutable_model_timestamp")
        or result.get("model_timestamp")
        or ""
    ).strip()

    receipt_core = {
        "prediction_id": prediction_id,
        "event_id": event_id,
        "sport": str(sport or "").strip().upper(),
        "controlling_specialist": str(controlling_specialist or "").strip(),
        "model_artifact_version": model_version or None,
        "feature_schema_version": schema,
        "features_expected": len(expected),
        "request_contract_features_expected": len(request_inputs),
        "model_features_expected": len(runtime_model_features),
        "features_available": available_count,
        "features_consumed": consumed_count,
        "features_missing": missing_count,
        "features_stale": stale_count,
        "features_rejected": rejected_count,
        "consumption_verification_status": verification_status,
        "role_contract_complete": role_contract_complete,
        "provenance_complete": provenance_complete,
        "critical_consumption_complete": critical_consumption_complete,
        "receipt_complete": receipt_complete,
        "unexpected_consumed_feature_ids": unexpected_consumed,
        "unexpected_rejected_feature_ids": unexpected_rejected,
        "consumed_but_unavailable_feature_ids": consumed_but_unavailable,
        "critical_features": {
            detail["feature_id"]: detail["consumption_status"]
            for detail in feature_details
            if detail["critical"]
        },
        "feature_details": feature_details,
        "model_timestamp": generated_at or None,
        "can_execute": False,
    }
    receipt_id = hashlib.sha256(
        json.dumps(receipt_core, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return {
        "feature_consumption_receipt_id": receipt_id,
        **receipt_core,
    }


__all__ = [
    "CAN_EXECUTE",
    "CANONICAL_FEATURE_ROLES",
    "CONSUMPTION_STATES",
    "CONSUMPTION_VERIFICATION_STATES",
    "FEATURE_ROLES",
    "FEATURE_VALUE_STATES",
    "FeatureContractEntry",
    "build_feature_consumption_receipt",
]
