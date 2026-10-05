"""V17 JS + governed-model convergence gate.

The JS Style layer is a research/candidate-generation intelligence. This module
uses the exact controlling specialist's already-governed probability package to
decide whether a JS-like thesis is *confirmed*, held, or contradicted.

It never blends JS heuristics into sporting probability and never changes the
specialist's calibrated probability or calibrated lower bound.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
PROBABILITY_AUTHORITY = "CONTROLLING_SPECIALIST_ONLY"
VERSION = "WOW_JS_MODEL_CONVERGENCE_V17_V1"

JS_MODEL_STRONG_CONFIRMATION = "JS_MODEL_STRONG_CONFIRMATION"
JS_MODEL_CONFIRMED = "JS_MODEL_CONFIRMED"
JS_ONLY_MODEL_HOLD = "JS_ONLY_MODEL_HOLD"
JS_MODEL_CONTRADICTION = "JS_MODEL_CONTRADICTION"
MODEL_CONFIRMED_NON_JS = "MODEL_CONFIRMED_NON_JS"
MODEL_PACKAGE_INCOMPLETE = "MODEL_PACKAGE_INCOMPLETE"

ADVANCE = "ADVANCE_TO_NORMAL_V17_CONSTRUCTION"
HOLD = "HOLD"
DO_NOT_ELEVATE = "DO_NOT_ELEVATE"


@dataclass(frozen=True)
class JSModelConvergenceDecision:
    convergence_status: str
    action: str
    js_candidate: bool
    js_research_priority: float | None
    model_publishable: bool
    model_rank_eligible: bool
    calibrated_probability: float | None
    calibrated_lower_bound: float | None
    controlling_specialist: str | None
    evidence_fresh: bool | None
    stress_passed: bool | None
    current_board_verified: bool | None
    material_contradiction: bool
    blockers: tuple[str, ...]
    reasons: tuple[str, ...]
    probability_mutated: bool = False
    probability_authority: str = PROBABILITY_AUTHORITY
    terminal_authority: str = TERMINAL_AUTHORITY
    can_execute: bool = CAN_EXECUTE
    version: str = VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "convergence_status": self.convergence_status,
            "action": self.action,
            "js_candidate": self.js_candidate,
            "js_research_priority": self.js_research_priority,
            "model_publishable": self.model_publishable,
            "model_rank_eligible": self.model_rank_eligible,
            "calibrated_probability": self.calibrated_probability,
            "calibrated_lower_bound": self.calibrated_lower_bound,
            "controlling_specialist": self.controlling_specialist,
            "evidence_fresh": self.evidence_fresh,
            "stress_passed": self.stress_passed,
            "current_board_verified": self.current_board_verified,
            "material_contradiction": self.material_contradiction,
            "blockers": list(self.blockers),
            "reasons": list(self.reasons),
            "probability_mutated": False,
            "probability_authority": PROBABILITY_AUTHORITY,
            "terminal_authority": TERMINAL_AUTHORITY,
            "can_execute": False,
            "version": VERSION,
        }


def _bool(payload: Mapping[str, Any], *keys: str) -> bool:
    return any(payload.get(key) is True for key in keys)


def _tri_bool(payload: Mapping[str, Any], *keys: str) -> bool | None:
    seen = False
    for key in keys:
        if key in payload:
            seen = True
            if payload.get(key) is True:
                return True
            if payload.get(key) is False:
                return False
    return None if not seen else False


def _float(payload: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if isfinite(number):
            return number
    return None


def _qualification(model: Mapping[str, Any]) -> Mapping[str, Any]:
    value = model.get("probability_qualification")
    return value if isinstance(value, Mapping) else {}


def _model_package(model: Mapping[str, Any]) -> dict[str, Any]:
    qualification = _qualification(model)
    publishable = model.get("probability_publishable") is True
    rank_eligible = (
        model.get("rank_eligible") is True
        or model.get("probability_rank_eligible") is True
        or qualification.get("rank_eligible") is True
        or qualification.get("probability_rank_eligible") is True
    )
    calibrated_probability = _float(
        model,
        "calibrated_probability",
        "governed_calibrated_probability",
    )
    calibrated_lower_bound = _float(
        model,
        "calibrated_lower_bound",
        "governed_lower_bound",
    )
    valid_numbers = (
        calibrated_probability is not None
        and calibrated_lower_bound is not None
        and 0.0 < calibrated_lower_bound <= calibrated_probability < 1.0
    )
    return {
        "publishable": publishable,
        "rank_eligible": rank_eligible,
        "calibrated_probability": calibrated_probability,
        "calibrated_lower_bound": calibrated_lower_bound,
        "valid_numbers": valid_numbers,
        "confidence_tier": str(
            model.get("confidence_tier")
            or qualification.get("confidence_tier")
            or ""
        ).strip().upper(),
        "model_qualified": (
            model.get("model_qualified") is True
            or qualification.get("model_qualified") is True
            or str(model.get("model_qualification_status") or qualification.get("model_qualification_status") or "").upper()
            in {"QUALIFIED", "MODEL_QUALIFIED"}
        ),
        "controlling_specialist": str(
            model.get("controlling_specialist")
            or model.get("specialist_owner")
            or qualification.get("controlling_specialist")
            or ""
        ).strip() or None,
    }


def _js_fields(js: Mapping[str, Any]) -> tuple[bool, float | None]:
    nested = js.get("js_style")
    if isinstance(nested, Mapping):
        js = nested
    priority = _float(js, "js_research_priority")
    return js.get("js_candidate") is True, priority


def evaluate_js_model_convergence(
    *,
    js_annotation: Mapping[str, Any],
    model_package: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    require_current_board: bool = False,
) -> JSModelConvergenceDecision:
    """Confirm or reject a JS thesis using the governed specialist package.

    Official V17 ranking remains lower-bound-first under the controlling route.
    This output is a downstream classification, not a new probability.
    """
    context = context or {}
    js_candidate, js_priority = _js_fields(js_annotation)
    model = _model_package(model_package)

    evidence_fresh = _tri_bool(
        context,
        "evidence_fresh",
        "role_opportunity_evidence_fresh",
        "research_fresh",
    )
    stress_passed = _tri_bool(
        context,
        "stress_passed",
        "failure_path_stress_passed",
        "scenario_stress_passed",
    )
    current_board_verified = _tri_bool(
        context,
        "current_board_verified",
        "exact_line_direction_verified",
    )
    material_contradiction = _bool(
        context,
        "material_contradiction",
        "failure_path_conflict",
        "role_opportunity_conflict",
        "directional_contradiction",
    )

    blockers: list[str] = []
    reasons: list[str] = []

    if not model["publishable"]:
        blockers.append("MODEL_PROBABILITY_NOT_PUBLISHABLE")
    if not model["rank_eligible"]:
        blockers.append("MODEL_NOT_RANK_ELIGIBLE")
    if not model["valid_numbers"]:
        blockers.append("MODEL_PACKAGE_INVALID_OR_INCOMPLETE")
    if evidence_fresh is False:
        blockers.append("JS_CONTEXT_STALE")
    if stress_passed is False:
        blockers.append("JS_FAILURE_PATH_STRESS_NOT_PASS")
    if require_current_board and current_board_verified is not True:
        blockers.append("CURRENT_EXACT_LINE_DIRECTION_NOT_VERIFIED")
    if material_contradiction:
        blockers.append("MATERIAL_JS_MODEL_CONTRADICTION")

    model_confirmed = (
        model["publishable"]
        and model["rank_eligible"]
        and model["valid_numbers"]
        and evidence_fresh is not False
        and stress_passed is not False
        and (not require_current_board or current_board_verified is True)
    )

    if material_contradiction and js_candidate:
        status = JS_MODEL_CONTRADICTION
        action = DO_NOT_ELEVATE
        reasons.append("JS_THESIS_HAS_MATERIAL_COUNTEREVIDENCE")
    elif not model_confirmed:
        status = JS_ONLY_MODEL_HOLD if js_candidate else MODEL_PACKAGE_INCOMPLETE
        action = HOLD
        reasons.append(
            "JS_SIGNAL_CANNOT_OVERRIDE_GOVERNED_MODEL"
            if js_candidate
            else "GOVERNED_MODEL_PACKAGE_NOT_READY"
        )
    elif not js_candidate:
        status = MODEL_CONFIRMED_NON_JS
        action = ADVANCE
        reasons.append("MODEL_CAN_QUALIFY_NON_JS_CANDIDATE")
    else:
        strong = (
            model["confidence_tier"] == "HIGH"
            and stress_passed is True
            and evidence_fresh is True
            and (not require_current_board or current_board_verified is True)
        )
        status = JS_MODEL_STRONG_CONFIRMATION if strong else JS_MODEL_CONFIRMED
        action = ADVANCE
        reasons.append(
            "JS_STRUCTURE_AND_GOVERNED_MODEL_STRONGLY_CONVERGE"
            if strong
            else "JS_STRUCTURE_CONFIRMED_BY_GOVERNED_MODEL"
        )

    reasons.append("OFFICIAL_RANKING_REMAINS_GOVERNED_LOWER_BOUND_FIRST")
    return JSModelConvergenceDecision(
        convergence_status=status,
        action=action,
        js_candidate=js_candidate,
        js_research_priority=js_priority,
        model_publishable=model["publishable"],
        model_rank_eligible=model["rank_eligible"],
        calibrated_probability=model["calibrated_probability"],
        calibrated_lower_bound=model["calibrated_lower_bound"],
        controlling_specialist=model["controlling_specialist"],
        evidence_fresh=evidence_fresh,
        stress_passed=stress_passed,
        current_board_verified=current_board_verified,
        material_contradiction=material_contradiction,
        blockers=tuple(dict.fromkeys(blockers)),
        reasons=tuple(dict.fromkeys(reasons)),
    )
