"""Calibration/publication lane separation for WOW v16 Clean Core.

A calibration-health/publication lock is not evidence that the controlling
specialist is unavailable. This module classifies blocker scope and resolves the
strict research terminal ceiling without manufacturing calibrated claims.

WOW-PATCH-2026-08-30-CALIBRATION-PUBLICATION-LANE-SEPARATION
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

# Only blockers whose owning gate explicitly certifies calibration/publication
# scope belong here. Do not add transport/preflight/deployment failures: when
# canonical governance evidence itself is unavailable, scope is not proven and
# the caller must fail closed rather than enter the raw-research bypass.
PUBLICATION_SCOPED_BLOCKERS = frozenset({
    "FORWARD_SHADOW_NOT_COMPLETED",
    "CALIBRATION_HEALTH_BLOCKED",
    "CALIBRATION_UNAVAILABLE",
    "GOVERNED_PROBABILITY_PUBLICATION_UNAVAILABLE",
})

# These failures mean the backend could not establish the canonical governance
# state. They are GLOBAL evidence failures, not evidence that only calibration
# or publication is broken.
GLOBAL_SCOPED_BLOCKERS = frozenset({
    "GOVERNED_PROBABILITY_PREFLIGHT_UNAVAILABLE",
    "GOVERNED_PROBABILITY_PREFLIGHT_INVALID_RESPONSE",
    "GOVERNED_DEPLOYMENT_NOT_READY",
    "GOVERNED_PROBABILITY_UNAVAILABLE",
})

MODEL_SCOPED_BLOCKERS = frozenset({
    "MODEL_UNAVAILABLE",
    "SPECIALIST_ROUTING_UNAVAILABLE",
    "SPECIALIST_MODEL_UNAVAILABLE",
    "GENERIC_PROP_FITTED_PROVIDER_UNAVAILABLE",
    "FITTED_MODEL_ARTIFACT_UNAVAILABLE",
    "MANDATORY_MODEL_INPUTS_INCOMPLETE",
})

_SCOPE_ORDER = ("GLOBAL", "CONFIDENCE", "CALIBRATION", "PUBLICATION", "MARKET", "MONEY", "SLIP")
_TERMINAL_RANK = {
    "FINAL_APPROVED": 90,
    "MONEY_QUALIFIED": 80,
    "MARKET_VERIFIED_HOLD": 70,
    "MODEL_QUALIFIED_HOLD": 60,
    "RESEARCH_INTEREST": 50,
    "MODEL_UNAVAILABLE": 20,
    "REJECT_DATA_QUALITY": 15,
    "NO_PLAY": 10,
}


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def blocker_scopes(blockers: Iterable[str]) -> tuple[str, ...]:
    """Return the union of failed contract scopes for classified blockers.

    Canonical preflight/transport failures are GLOBAL because the backend could
    not prove a narrower scope. Other unknown blockers remain scope-unknown and
    must be classified by their owning gate instead of being guessed here.
    """
    scopes: set[str] = set()
    for raw in blockers:
        blocker = _norm(raw)
        if blocker in GLOBAL_SCOPED_BLOCKERS:
            scopes.add("GLOBAL")
        elif blocker in PUBLICATION_SCOPED_BLOCKERS or blocker.startswith("FORWARD_SHADOW_"):
            scopes.update(("CALIBRATION", "PUBLICATION"))
        elif blocker in MODEL_SCOPED_BLOCKERS:
            scopes.add("CONFIDENCE")
        elif blocker.startswith("MARKET_") or blocker.startswith("EXACT_LINE_"):
            scopes.add("MARKET")
        elif blocker.startswith("MONEY_") or blocker.startswith("PAYOUT_") or blocker.startswith("FEE_"):
            scopes.add("MONEY")
        elif blocker.startswith("SLIP_") or blocker.startswith("PORTFOLIO_"):
            scopes.add("SLIP")
    return tuple(scope for scope in _SCOPE_ORDER if scope in scopes)


def is_calibration_publication_only(blockers: Iterable[str]) -> bool:
    blockers_tuple = tuple(_norm(x) for x in blockers if _norm(x))
    if not blockers_tuple:
        return False
    if any(blocker in GLOBAL_SCOPED_BLOCKERS for blocker in blockers_tuple):
        return False
    if not all(
        blocker in PUBLICATION_SCOPED_BLOCKERS or blocker.startswith("FORWARD_SHADOW_")
        for blocker in blockers_tuple
    ):
        return False
    scopes = blocker_scopes(blockers_tuple)
    return bool(scopes) and set(scopes).issubset({"CALIBRATION", "PUBLICATION"})


def strictest_ceiling(existing: Optional[str], new_ceiling: str) -> str:
    """Return the stricter (lower) native WOW ceiling when both are known."""
    if not existing:
        return new_ceiling
    a = _TERMINAL_RANK.get(existing, 0)
    b = _TERMINAL_RANK.get(new_ceiling, 0)
    return existing if a <= b else new_ceiling


@dataclass(frozen=True)
class LaneSeparationDecision:
    specialist_model_capability: str
    specialist_model_name: Optional[str]
    specialist_model_status: str
    calibration_health_status: str
    calibration_status: str
    governed_probability_capability: str
    governed_publishable: bool
    manual_lane_used: bool
    manual_confidence_cap: Optional[float]
    failed_contract_scope: tuple[str, ...]
    probability_claim_status: str
    terminal_ceiling: str
    blockers: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "specialist_model_capability": self.specialist_model_capability,
            "specialist_model_name": self.specialist_model_name,
            "specialist_model_status": self.specialist_model_status,
            "calibration_health_status": self.calibration_health_status,
            "calibration_status": self.calibration_status,
            "governed_probability_capability": self.governed_probability_capability,
            "governed_publishable": self.governed_publishable,
            "manual_lane_used": self.manual_lane_used,
            "manual_confidence_cap": self.manual_confidence_cap,
            "failed_contract_scope": list(self.failed_contract_scope),
            "probability_claim_status": self.probability_claim_status,
            "terminal_ceiling": self.terminal_ceiling,
            "blockers": list(self.blockers),
        }


def resolve_lane_separation(
    *,
    specialist_available: bool,
    specialist_name: Optional[str],
    specialist_output_complete: bool,
    calibration_health_status: Optional[str],
    governed_probability_capability: Optional[str],
    blockers: Sequence[str] = (),
    manual_lane_permitted: bool = False,
    manual_lane_used: bool = False,
    manual_confidence_cap: Optional[float] = None,
    existing_ceiling: Optional[str] = None,
) -> LaneSeparationDecision:
    """Resolve capability/claim/ceiling state without inventing probability.

    This function never creates calibrated_probability, lower_bound, upper_bound,
    fitted parameters, or market-derived model probability. It only classifies
    which lanes remain valid and the maximum native terminal ceiling.
    """
    normalized_blockers = tuple(dict.fromkeys(_norm(x) for x in blockers if _norm(x)))
    capability = _norm(governed_probability_capability) or "UNAVAILABLE"
    calibration_health = _norm(calibration_health_status) or "UNKNOWN"
    scopes = blocker_scopes(normalized_blockers)

    if not specialist_available:
        model_blockers = normalized_blockers or ("MODEL_UNAVAILABLE",)
        return LaneSeparationDecision(
            specialist_model_capability="UNAVAILABLE",
            specialist_model_name=specialist_name,
            specialist_model_status="UNAVAILABLE",
            calibration_health_status=calibration_health,
            calibration_status="NOT_RUN",
            governed_probability_capability=capability,
            governed_publishable=False,
            manual_lane_used=False,
            manual_confidence_cap=None,
            failed_contract_scope=blocker_scopes(model_blockers) or ("CONFIDENCE",),
            probability_claim_status="MODEL_UNAVAILABLE",
            terminal_ceiling=strictest_ceiling(existing_ceiling, "MODEL_UNAVAILABLE"),
            blockers=model_blockers,
        )

    publication_available = capability == "AVAILABLE" and calibration_health in {"PASS", "AVAILABLE", "HEALTHY"}
    if publication_available and specialist_output_complete:
        return LaneSeparationDecision(
            specialist_model_capability="AVAILABLE",
            specialist_model_name=specialist_name,
            specialist_model_status="COMPLETED",
            calibration_health_status=calibration_health,
            calibration_status="AVAILABLE",
            governed_probability_capability=capability,
            governed_publishable=True,
            manual_lane_used=False,
            manual_confidence_cap=None,
            failed_contract_scope=scopes,
            probability_claim_status="GOVERNED_CALIBRATED_PUBLISHABLE",
            terminal_ceiling=existing_ceiling or "FINAL_APPROVED",
            blockers=normalized_blockers,
        )

    research_complete = specialist_output_complete or (manual_lane_permitted and manual_lane_used)
    ceiling = "MODEL_QUALIFIED_HOLD" if research_complete else "RESEARCH_INTEREST"
    claim = "SPECIALIST_RAW_RESEARCH_ONLY" if specialist_output_complete else (
        "MANUAL_ESTIMATE_RESEARCH_ONLY" if manual_lane_permitted and manual_lane_used else "CALIBRATION_BLOCKED_NO_PUBLISH"
    )
    scoped = tuple(dict.fromkeys((*scopes, "CALIBRATION", "PUBLICATION")))
    scoped = tuple(scope for scope in _SCOPE_ORDER if scope in scoped)
    return LaneSeparationDecision(
        specialist_model_capability="AVAILABLE",
        specialist_model_name=specialist_name,
        specialist_model_status="COMPLETED" if specialist_output_complete else "AVAILABLE_NO_OUTPUT",
        calibration_health_status=calibration_health,
        calibration_status="UNKNOWN_OR_BLOCKED",
        governed_probability_capability=capability,
        governed_publishable=False,
        manual_lane_used=bool(manual_lane_permitted and manual_lane_used),
        manual_confidence_cap=manual_confidence_cap if manual_lane_permitted and manual_lane_used else None,
        failed_contract_scope=scoped,
        probability_claim_status=claim,
        terminal_ceiling=strictest_ceiling(existing_ceiling, ceiling),
        blockers=normalized_blockers,
    )
