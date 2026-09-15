"""V17 cross-sport certification state contract.

This module does not certify models. It gives WOW/LLP one vocabulary for
reporting the state of every required sport/surface without confusing a skill,
route, candidate, or stale corpus with governed numerical authority.

Exactly one controlling specialist plus an exact fitted artifact and governed
calibration package are required for ``CERTIFIED_ROUTE``. ``can_execute`` is
always false.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Literal, Mapping

CAN_EXECUTE = False

CERTIFICATION_SPORTS = (
    "MLB",
    "NFL",
    "NCAAF",
    "NBA",
    "WNBA",
    "NCAAB",
    "NHL",
    "SOCCER",
    "TENNIS",
    "GOLF",
    "MMA",
    "BOXING",
)
SURFACES = ("PROP", "TEAM_EVENT")

CertificationStatus = Literal[
    "CERTIFIED_ROUTE",
    "READY_FOR_LIFECYCLE_REVIEW",
    "CANDIDATE_ONLY",
    "DATA_REFRESH_REQUIRED",
    "MODEL_BUILD_REQUIRED",
    "MODEL_UNAVAILABLE",
]


@dataclass(frozen=True)
class CertificationEvidence:
    sport: str
    surface: Literal["PROP", "TEAM_EVENT"]
    controlling_specialist_ready: bool = False
    fitted_model_present: bool = False
    exact_certified_artifact_ready: bool = False
    calibrator_ready: bool = False
    candidate_ready: bool = False
    deterministic_replay_ready: bool = False
    data_current: bool = False
    model_build_exists: bool = False
    source_provenance_ready: bool = False
    notes: tuple[str, ...] = ()
    can_execute: bool = CAN_EXECUTE


@dataclass(frozen=True)
class CertificationAssessment:
    sport: str
    surface: str
    status: CertificationStatus
    blockers: tuple[str, ...]
    numerical_authority: bool
    can_execute: bool = CAN_EXECUTE


def _normalize_sport(value: str) -> str:
    sport = str(value).strip().upper()
    if sport not in CERTIFICATION_SPORTS:
        raise ValueError(f"unsupported certification inventory sport: {sport}")
    return sport


def assess(evidence: CertificationEvidence) -> CertificationAssessment:
    sport = _normalize_sport(evidence.sport)
    surface = str(evidence.surface).strip().upper()
    if surface not in SURFACES:
        raise ValueError(f"unsupported certification surface: {surface}")
    if evidence.can_execute:
        raise ValueError("V17 certification evidence cannot grant execution authority")

    blockers: list[str] = []
    if not evidence.controlling_specialist_ready:
        blockers.append("CONTROLLING_SPECIALIST_UNAVAILABLE")
    if not evidence.data_current:
        blockers.append("CURRENT_DATA_NOT_CERTIFIED")
    if not evidence.source_provenance_ready:
        blockers.append("SOURCE_PROVENANCE_NOT_CERTIFIED")

    certified = (
        evidence.controlling_specialist_ready
        and evidence.fitted_model_present
        and evidence.exact_certified_artifact_ready
        and evidence.calibrator_ready
        and evidence.data_current
        and evidence.source_provenance_ready
    )
    if certified:
        return CertificationAssessment(
            sport=sport,
            surface=surface,
            status="CERTIFIED_ROUTE",
            blockers=(),
            numerical_authority=True,
        )

    lifecycle_ready = (
        evidence.controlling_specialist_ready
        and evidence.fitted_model_present
        and evidence.candidate_ready
        and evidence.deterministic_replay_ready
        and evidence.calibrator_ready
        and evidence.data_current
        and evidence.source_provenance_ready
    )
    if lifecycle_ready:
        return CertificationAssessment(
            sport=sport,
            surface=surface,
            status="READY_FOR_LIFECYCLE_REVIEW",
            blockers=("EXACT_CERTIFIED_ARTIFACT_NOT_PROMOTED",),
            numerical_authority=False,
        )

    if evidence.candidate_ready:
        if not evidence.deterministic_replay_ready:
            blockers.append("DETERMINISTIC_REPLAY_NOT_READY")
        if not evidence.calibrator_ready:
            blockers.append("CALIBRATOR_NOT_READY")
        return CertificationAssessment(
            sport=sport,
            surface=surface,
            status="CANDIDATE_ONLY",
            blockers=tuple(sorted(set(blockers))),
            numerical_authority=False,
        )

    if evidence.model_build_exists and not evidence.data_current:
        return CertificationAssessment(
            sport=sport,
            surface=surface,
            status="DATA_REFRESH_REQUIRED",
            blockers=tuple(sorted(set(blockers + ["CURRENT_TRAINING_CORPUS_REQUIRED"]))),
            numerical_authority=False,
        )

    if evidence.model_build_exists or evidence.controlling_specialist_ready:
        if not evidence.fitted_model_present:
            blockers.append("FITTED_MODEL_ARTIFACT_REQUIRED")
        if not evidence.calibrator_ready:
            blockers.append("CALIBRATOR_NOT_READY")
        return CertificationAssessment(
            sport=sport,
            surface=surface,
            status="MODEL_BUILD_REQUIRED",
            blockers=tuple(sorted(set(blockers))),
            numerical_authority=False,
        )

    return CertificationAssessment(
        sport=sport,
        surface=surface,
        status="MODEL_UNAVAILABLE",
        blockers=tuple(sorted(set(blockers + ["GOVERNED_MODEL_CAPABILITY_UNAVAILABLE"]))),
        numerical_authority=False,
    )


def build_inventory(
    observations: Mapping[tuple[str, str], CertificationEvidence] | None = None,
) -> list[dict]:
    """Return the complete sport × surface inventory, never silently omitting a sport."""
    observations = observations or {}
    output: list[dict] = []
    for sport in CERTIFICATION_SPORTS:
        for surface in SURFACES:
            evidence = observations.get((sport, surface))
            if evidence is None:
                evidence = CertificationEvidence(sport=sport, surface=surface)  # type: ignore[arg-type]
            output.append(asdict(assess(evidence)))
    return output


__all__ = [
    "CAN_EXECUTE",
    "CERTIFICATION_SPORTS",
    "SURFACES",
    "CertificationAssessment",
    "CertificationEvidence",
    "assess",
    "build_inventory",
]
