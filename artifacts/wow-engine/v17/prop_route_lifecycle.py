"""Exact-route V17 prop calibration/certification/promotion lifecycle.

This module is an audit/control-plane contract.  It does not train, calibrate,
certify, promote, activate, publish, or execute a prop model.  Its purpose is to
make the lifecycle state of every exact player/scalar prop route explicit so a
sport-level or hand-maintained capability declaration cannot be mistaken for
production numerical authority.

Production authority is route specific and requires the same immutable model
artifact to clear:

    fitted candidate -> forward calibration evidence -> certification review
    -> governed promotion -> runtime registration -> canonical Action canary

Phase-A PRECALIBRATION_SHRINKAGE remains useful evidence-building output, but it
is not the forward-calibration certification required by this lifecycle.
``can_execute`` is always false.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

import prop_discrete_engine
import prop_fitted_provider
from prop_auto_hydration_workload import WORKLOAD_STATS
import wnba_prop_auto_hydration

from v17.cross_sport_certification_inventory import CERTIFICATION_SPORTS
from v17.prop_capability_manifest import DECLARED_PROP_LANES, normalize_prop_sport

CAN_EXECUTE = False
FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"

PRODUCTION_REGISTERED = "PRODUCTION_REGISTERED"
GOVERNED_PROMOTION_REQUIRED = "GOVERNED_PROMOTION_REQUIRED"
CERTIFICATION_REVIEW_REQUIRED = "CERTIFICATION_REVIEW_REQUIRED"
CALIBRATION_EVIDENCE_REQUIRED = "CALIBRATION_EVIDENCE_REQUIRED"
CANDIDATE_ONLY = "CANDIDATE_ONLY"
MODEL_BUILD_REQUIRED = "MODEL_BUILD_REQUIRED"
HYDRATION_OR_RUNTIME_ADAPTER_REQUIRED = "HYDRATION_OR_RUNTIME_ADAPTER_REQUIRED"
NO_CURRENT_PROP_CATEGORY_DECLARED = "NO_CURRENT_PROP_CATEGORY_DECLARED"

CALIBRATION_CERTIFIED_PASS = "CALIBRATION_CERTIFIED_PASS"
CERTIFICATION_APPROVED = "APPROVED"
CERTIFIED_ARTIFACT_STATES = frozenset({"PROSPECTIVE_CERTIFIED", "CHAMPION"})
PHASE_A_CALIBRATION_STATES = frozenset(
    {
        "PRECALIBRATION",
        "PRECALIBRATION_SHRINKAGE",
        "PHASE_A",
        "PHASE_A_PRECALIBRATION",
    }
)

LIFECYCLE_STATUSES = frozenset(
    {
        PRODUCTION_REGISTERED,
        GOVERNED_PROMOTION_REQUIRED,
        CERTIFICATION_REVIEW_REQUIRED,
        CALIBRATION_EVIDENCE_REQUIRED,
        CANDIDATE_ONLY,
        MODEL_BUILD_REQUIRED,
        HYDRATION_OR_RUNTIME_ADAPTER_REQUIRED,
        NO_CURRENT_PROP_CATEGORY_DECLARED,
    }
)


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def exact_route_key(sport: str, stat_type: str, feature_schema_version: str = FEATURE_SCHEMA_VERSION) -> tuple[str, str, str]:
    return (normalize_prop_sport(sport), _norm(stat_type), str(feature_schema_version or "").strip())


def route_declared(sport: str, stat_type: str) -> bool:
    sport_key, stat_key, _schema = exact_route_key(sport, stat_type)
    return (sport_key, stat_key) in DECLARED_PROP_LANES


def hydration_route_registered(sport: str, stat_type: str) -> bool:
    """Return whether the canonical generic prop hydrator owns this exact route.

    This intentionally does not treat a sport-wide provider as authority for all
    of that sport's prop markets.  MLB 1IP, Fantasy Score, and future routes with
    separate ingress contracts remain false here until their production runtime
    explicitly registers an exact hydration path.
    """
    sport_key, stat_key, _schema = exact_route_key(sport, stat_type)
    if sport_key == "MLB":
        return stat_key == "PITCHER_STRIKEOUTS" or stat_key in WORKLOAD_STATS
    if sport_key == "WNBA":
        column = wnba_prop_auto_hydration.STAT_COLUMNS.get(stat_key)
        canonical = wnba_prop_auto_hydration.CANONICAL_STATS.get(str(column or ""))
        return canonical == stat_key
    return False


def runtime_registration_snapshot(
    *,
    sport: str,
    stat_type: str,
    model_family: str | None,
    calibrator_version: str | None,
) -> dict[str, Any]:
    """Read current in-process adapter/hydration registration without mutating it.

    The private registries are owned by the already-authoritative provider and
    calibration modules; this function reads them only for audit.  It cannot
    register an adapter and therefore cannot create capability by inspection.
    """
    model_key = _norm(model_family)
    calibrator_key = _norm(calibrator_version)
    model_registry = getattr(prop_fitted_provider, "_ADAPTERS", {})
    calibration_registry = getattr(prop_discrete_engine, "_CALIBRATION_ADAPTERS", {})
    return {
        "sport": normalize_prop_sport(sport),
        "stat_type": _norm(stat_type),
        "model_family": model_key or None,
        "calibrator_version": calibrator_key or None,
        "model_adapter_registered": bool(model_key and model_key in model_registry),
        "calibrator_adapter_registered": bool(calibrator_key and calibrator_key in calibration_registry),
        "hydration_route_registered": hydration_route_registered(sport, stat_type),
        "can_execute": False,
    }


@dataclass(frozen=True)
class PropRouteLifecycleEvidence:
    sport: str
    stat_type: str
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    declared_route: bool = False
    controlling_specialist_ready: bool = False
    model_build_exists: bool = False
    fitted_model_present: bool = False
    candidate_research_active: bool = False
    model_family: str | None = None
    model_artifact_version: str | None = None
    artifact_checksum: str | None = None
    calibrator_version: str | None = None
    calibration_evidence_status: str = "NOT_ASSESSED"
    deterministic_replay_ready: bool = False
    source_provenance_ready: bool = False
    certification_review_status: str = "NOT_ASSESSED"
    certification_id: str | None = None
    lifecycle_state: str | None = None
    promoted: bool = False
    active: bool = False
    model_adapter_registered: bool = False
    calibrator_adapter_registered: bool = False
    hydration_registered: bool = False
    action_canary_verified: bool = False
    can_execute: bool = CAN_EXECUTE


@dataclass(frozen=True)
class PropRouteLifecycleAssessment:
    sport: str
    stat_type: str
    feature_schema_version: str
    status: str
    blockers: tuple[str, ...]
    production_numerical_authority: bool
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def assess_prop_route(evidence: PropRouteLifecycleEvidence) -> PropRouteLifecycleAssessment:
    """Assess one exact route without upgrading missing evidence.

    Existing legacy/runtime publication is not mutated by this assessment.  The
    stricter ``PRODUCTION_REGISTERED`` label means the complete universal V17
    calibration/certification/promotion/registration chain has been evidenced
    for the same immutable artifact.
    """
    if evidence.can_execute:
        raise ValueError("V17 prop lifecycle evidence cannot grant execution authority")

    sport = normalize_prop_sport(evidence.sport)
    stat_type = _norm(evidence.stat_type)
    schema = str(evidence.feature_schema_version or "").strip()
    declared = bool(evidence.declared_route or route_declared(sport, stat_type))

    if not declared and not any(
        (
            evidence.controlling_specialist_ready,
            evidence.model_build_exists,
            evidence.fitted_model_present,
            evidence.candidate_research_active,
        )
    ):
        return PropRouteLifecycleAssessment(
            sport=sport,
            stat_type=stat_type,
            feature_schema_version=schema,
            status=NO_CURRENT_PROP_CATEGORY_DECLARED,
            blockers=("PROP_ROUTE_NOT_DECLARED",),
            production_numerical_authority=False,
        )

    base_blockers: list[str] = []
    if not declared:
        base_blockers.append("PROP_ROUTE_NOT_DECLARED")
    if not evidence.controlling_specialist_ready:
        base_blockers.append("CONTROLLING_SPECIALIST_UNAVAILABLE")
    if not evidence.source_provenance_ready:
        base_blockers.append("SOURCE_PROVENANCE_NOT_CERTIFIED")

    if not evidence.model_build_exists and not evidence.fitted_model_present:
        return PropRouteLifecycleAssessment(
            sport=sport,
            stat_type=stat_type,
            feature_schema_version=schema,
            status=MODEL_BUILD_REQUIRED,
            blockers=tuple(dict.fromkeys(base_blockers + ["FITTED_MODEL_BUILD_REQUIRED"])),
            production_numerical_authority=False,
        )

    if not evidence.fitted_model_present:
        return PropRouteLifecycleAssessment(
            sport=sport,
            stat_type=stat_type,
            feature_schema_version=schema,
            status=CANDIDATE_ONLY,
            blockers=tuple(dict.fromkeys(base_blockers + ["EXACT_FITTED_ARTIFACT_REQUIRED"])),
            production_numerical_authority=False,
        )

    immutable_identity_ready = all(
        _has_text(value)
        for value in (
            evidence.model_family,
            evidence.model_artifact_version,
            evidence.artifact_checksum,
            evidence.calibrator_version,
        )
    )
    if not immutable_identity_ready:
        return PropRouteLifecycleAssessment(
            sport=sport,
            stat_type=stat_type,
            feature_schema_version=schema,
            status=CANDIDATE_ONLY,
            blockers=tuple(dict.fromkeys(base_blockers + ["IMMUTABLE_ARTIFACT_IDENTITY_INCOMPLETE"])),
            production_numerical_authority=False,
        )

    calibration_status = _norm(evidence.calibration_evidence_status)
    if calibration_status != CALIBRATION_CERTIFIED_PASS:
        calibration_blockers = list(base_blockers)
        if calibration_status in PHASE_A_CALIBRATION_STATES:
            calibration_blockers.append("PHASE_A_PRECALIBRATION_NOT_FORWARD_CERTIFICATION")
        else:
            calibration_blockers.append("FORWARD_CALIBRATION_CERTIFICATION_REQUIRED")
        return PropRouteLifecycleAssessment(
            sport=sport,
            stat_type=stat_type,
            feature_schema_version=schema,
            status=CALIBRATION_EVIDENCE_REQUIRED,
            blockers=tuple(dict.fromkeys(calibration_blockers)),
            production_numerical_authority=False,
        )

    review_ready = (
        evidence.deterministic_replay_ready
        and evidence.source_provenance_ready
        and _norm(evidence.certification_review_status) == CERTIFICATION_APPROVED
        and _has_text(evidence.certification_id)
    )
    if not review_ready:
        blockers = list(base_blockers)
        if not evidence.deterministic_replay_ready:
            blockers.append("DETERMINISTIC_CERTIFICATION_REPLAY_REQUIRED")
        if _norm(evidence.certification_review_status) != CERTIFICATION_APPROVED:
            blockers.append("INDEPENDENT_CERTIFICATION_REVIEW_REQUIRED")
        if not _has_text(evidence.certification_id):
            blockers.append("CERTIFICATION_ID_REQUIRED")
        return PropRouteLifecycleAssessment(
            sport=sport,
            stat_type=stat_type,
            feature_schema_version=schema,
            status=CERTIFICATION_REVIEW_REQUIRED,
            blockers=tuple(dict.fromkeys(blockers)),
            production_numerical_authority=False,
        )

    promoted = (
        evidence.promoted
        and evidence.active
        and _norm(evidence.lifecycle_state) in CERTIFIED_ARTIFACT_STATES
    )
    if not promoted:
        blockers = list(base_blockers)
        if not evidence.promoted:
            blockers.append("EXACT_ARTIFACT_NOT_PROMOTED")
        if not evidence.active:
            blockers.append("EXACT_ARTIFACT_NOT_ACTIVE")
        if _norm(evidence.lifecycle_state) not in CERTIFIED_ARTIFACT_STATES:
            blockers.append("CERTIFIED_LIFECYCLE_STATE_REQUIRED")
        return PropRouteLifecycleAssessment(
            sport=sport,
            stat_type=stat_type,
            feature_schema_version=schema,
            status=GOVERNED_PROMOTION_REQUIRED,
            blockers=tuple(dict.fromkeys(blockers)),
            production_numerical_authority=False,
        )

    runtime_blockers: list[str] = list(base_blockers)
    if not evidence.model_adapter_registered:
        runtime_blockers.append("MODEL_FAMILY_RUNTIME_ADAPTER_REQUIRED")
    if not evidence.calibrator_adapter_registered:
        runtime_blockers.append("CALIBRATOR_RUNTIME_ADAPTER_REQUIRED")
    if not evidence.hydration_registered:
        runtime_blockers.append("EXACT_ROUTE_HYDRATION_REQUIRED")
    if not evidence.action_canary_verified:
        runtime_blockers.append("CANONICAL_ACTION_CANARY_REQUIRED")
    if runtime_blockers:
        return PropRouteLifecycleAssessment(
            sport=sport,
            stat_type=stat_type,
            feature_schema_version=schema,
            status=HYDRATION_OR_RUNTIME_ADAPTER_REQUIRED,
            blockers=tuple(dict.fromkeys(runtime_blockers)),
            production_numerical_authority=False,
        )

    return PropRouteLifecycleAssessment(
        sport=sport,
        stat_type=stat_type,
        feature_schema_version=schema,
        status=PRODUCTION_REGISTERED,
        blockers=(),
        production_numerical_authority=True,
    )


def build_prop_route_inventory(
    observations: Iterable[PropRouteLifecycleEvidence],
    *,
    sports: Iterable[str] = CERTIFICATION_SPORTS,
) -> list[dict[str, Any]]:
    """Assess all supplied exact routes and preserve explicit sport coverage.

    Every requested sport appears at least once.  A sport with no supplied or
    declared prop route is represented by a typed no-category row rather than
    silently disappearing from an all-sports audit.
    """
    rows = list(observations)
    requested_sports = tuple(dict.fromkeys(normalize_prop_sport(s) for s in sports))
    output: list[dict[str, Any]] = []
    seen_sports: set[str] = set()
    seen_routes: set[tuple[str, str, str]] = set()

    for evidence in rows:
        key = exact_route_key(evidence.sport, evidence.stat_type, evidence.feature_schema_version)
        if key in seen_routes:
            raise ValueError(f"duplicate exact prop lifecycle route: {key}")
        seen_routes.add(key)
        assessed = assess_prop_route(evidence)
        output.append(assessed.as_dict())
        seen_sports.add(assessed.sport)

    for sport in requested_sports:
        if sport in seen_sports:
            continue
        placeholder = PropRouteLifecycleEvidence(
            sport=sport,
            stat_type="__SPORT_PROP_CATEGORY_INVENTORY__",
            declared_route=False,
        )
        output.append(assess_prop_route(placeholder).as_dict())

    output.sort(key=lambda row: (row["sport"], row["stat_type"], row["feature_schema_version"]))
    return output


__all__ = [
    "CALIBRATION_CERTIFIED_PASS",
    "CALIBRATION_EVIDENCE_REQUIRED",
    "CAN_EXECUTE",
    "CANDIDATE_ONLY",
    "CERTIFICATION_APPROVED",
    "CERTIFICATION_REVIEW_REQUIRED",
    "GOVERNED_PROMOTION_REQUIRED",
    "HYDRATION_OR_RUNTIME_ADAPTER_REQUIRED",
    "LIFECYCLE_STATUSES",
    "MODEL_BUILD_REQUIRED",
    "NO_CURRENT_PROP_CATEGORY_DECLARED",
    "PRODUCTION_REGISTERED",
    "PropRouteLifecycleAssessment",
    "PropRouteLifecycleEvidence",
    "assess_prop_route",
    "build_prop_route_inventory",
    "exact_route_key",
    "hydration_route_registered",
    "route_declared",
    "runtime_registration_snapshot",
]
