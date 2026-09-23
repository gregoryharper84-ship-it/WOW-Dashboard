"""Fail-closed probability-quality parity contract for governed team/event lanes.

Equal treatment means the same evidence/governance standard, not the same model
mathematics.  This overlay does not compute or alter sporting probabilities.  It
adds a same-shaped quality contract for MLB, NFL, NCAAF/CFB, NBA, WNBA and NHL so
missing capability/evidence stays explicit instead of being mistaken for parity.
"""
from __future__ import annotations

from typing import Any, Mapping

CAN_EXECUTE = False
AUTOMATIC_PROMOTION = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
QUALITY_PARITY_VERSION = "V17_TEAM_EVENT_PROBABILITY_QUALITY_PARITY_V1"

TARGET_SPORTS = ("MLB", "NFL", "NCAAF", "NBA", "WNBA", "NHL")
ALIASES = {"CFB": "NCAAF", "COLLEGE_FOOTBALL": "NCAAF"}

REQUIRED_DIMENSIONS = (
    "exact_fitted_specialist",
    "quantitative_calibration_health",
    "cohort_reliability_separate",
    "event_specific_uncertainty",
    "early_pregame_grade",
    "final_pregame_published_grade",
    "dominance_or_margin_diagnostic",
    "chronological_challenger",
    "untouched_holdout",
    "typed_failure_preservation",
)

REQUIRED_CALIBRATION_METRICS = (
    "brier",
    "log_loss",
    "ece",
    "calibration_intercept",
    "calibration_slope",
    "max_calibration_bin_gap",
)


def normalize_quality_sport(sport: str) -> str:
    token = str(sport or "").strip().upper()
    return ALIASES.get(token, token)


def _truthy(evidence: Mapping[str, Any], key: str) -> bool:
    return evidence.get(key) is True


def assess_probability_quality_evidence(
    sport: str,
    evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return parity state without manufacturing missing model capability.

    `evidence` is metadata/health evidence only.  It is never allowed to supply a
    probability and cannot promote a sporting model.
    """
    normalized = normalize_quality_sport(sport)
    if normalized not in TARGET_SPORTS:
        return {
            "quality_parity_version": QUALITY_PARITY_VERSION,
            "sport": normalized,
            "in_scope": False,
            "status": "NOT_IN_SCOPE",
            "required_dimensions": list(REQUIRED_DIMENSIONS),
            "blockers": [],
            "automatic_promotion": False,
            "probability_publishable": False,
            "global_terminal_authority": TERMINAL_AUTHORITY,
            "can_execute": False,
        }

    row = dict(evidence or {})
    blockers: list[str] = []

    # Model capability comes first.  No calibration or generic probability can
    # compensate for a missing exact fitted specialist.
    if not _truthy(row, "exact_fitted_specialist"):
        blockers.append("QUALITY_PARITY_EXACT_FITTED_SPECIALIST_UNPROVEN")

    metrics = row.get("calibration_metrics")
    if not isinstance(metrics, Mapping):
        blockers.append("QUALITY_PARITY_CALIBRATION_METRICS_MISSING")
    else:
        for metric in REQUIRED_CALIBRATION_METRICS:
            value = metrics.get(metric)
            if value is None:
                blockers.append(f"QUALITY_PARITY_{metric.upper()}_MISSING")

    if str(row.get("calibration_health_status") or "").upper() != "PASS":
        blockers.append("QUALITY_PARITY_CALIBRATION_HEALTH_NOT_PASS")
    if not _truthy(row, "calibration_health_quantitative"):
        blockers.append("QUALITY_PARITY_CALIBRATION_PASS_NOT_QUANTITATIVE")
    if _truthy(row, "test_used_for_selection"):
        blockers.append("QUALITY_PARITY_TEST_SELECTION_LEAKAGE")

    if not _truthy(row, "cohort_reliability_separate"):
        blockers.append("QUALITY_PARITY_COHORT_RELIABILITY_NOT_SEPARATED")
    if not _truthy(row, "event_specific_uncertainty"):
        blockers.append("QUALITY_PARITY_EVENT_UNCERTAINTY_UNPROVEN")
    if not _truthy(row, "early_pregame_grade"):
        blockers.append("QUALITY_PARITY_EARLY_GRADE_LEDGER_MISSING")
    if not _truthy(row, "final_pregame_published_grade"):
        blockers.append("QUALITY_PARITY_FINAL_PREGAME_GRADE_LEDGER_MISSING")
    if not _truthy(row, "dominance_or_margin_diagnostic"):
        blockers.append("QUALITY_PARITY_DOMINANCE_DIAGNOSTIC_MISSING")
    if not _truthy(row, "chronological_challenger"):
        blockers.append("QUALITY_PARITY_CHRONOLOGICAL_CHALLENGER_MISSING")
    if not _truthy(row, "untouched_holdout"):
        blockers.append("QUALITY_PARITY_UNTOUCHED_HOLDOUT_MISSING")
    if not _truthy(row, "typed_failure_preservation"):
        blockers.append("QUALITY_PARITY_TYPED_FAILURE_PRESERVATION_UNPROVEN")
    if row.get("can_execute") not in (None, False):
        blockers.append("QUALITY_PARITY_CAN_EXECUTE_MUST_BE_FALSE")

    blockers = list(dict.fromkeys(blockers))
    if not _truthy(row, "exact_fitted_specialist"):
        status = "MODEL_CAPABILITY_UNPROVEN"
    elif blockers:
        status = "QUALITY_EVIDENCE_INCOMPLETE"
    else:
        status = "QUALITY_PARITY_EVIDENCE_COMPLETE"

    return {
        "quality_parity_version": QUALITY_PARITY_VERSION,
        "sport": normalized,
        "in_scope": True,
        "status": status,
        "required_dimensions": list(REQUIRED_DIMENSIONS),
        "required_calibration_metrics": list(REQUIRED_CALIBRATION_METRICS),
        "blockers": blockers,
        "automatic_promotion": False,
        "probability_publishable": False,
        "market_probability_substitution_allowed": False,
        "generic_reasoning_substitution_allowed": False,
        "global_terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


def readiness_quality_evidence(sport: str, health: Mapping[str, Any]) -> dict[str, Any]:
    """Translate existing route/readiness health into conservative parity evidence.

    This adapter intentionally does not infer calibration quality from a route,
    registration, or artifact PASS label.  Missing quantitative/ledger evidence
    remains blocked until the sport-specific lifecycle proves it.
    """
    normalized = normalize_quality_sport(sport)
    model_proven = bool(
        health.get("model_artifact_dependency_satisfied")
        and health.get("request_scoring_path_ready")
    )
    quantitative = bool(health.get("probability_quality_quantitative_pass"))
    metrics = health.get("probability_quality_metrics")
    return {
        "exact_fitted_specialist": model_proven,
        "calibration_metrics": metrics if isinstance(metrics, Mapping) else None,
        "calibration_health_status": health.get("probability_quality_calibration_status"),
        "calibration_health_quantitative": quantitative,
        "test_used_for_selection": bool(health.get("probability_quality_test_used_for_selection")),
        "cohort_reliability_separate": bool(health.get("cohort_reliability_separate")),
        "event_specific_uncertainty": bool(health.get("event_specific_uncertainty_certified")),
        "early_pregame_grade": bool(health.get("early_pregame_grade_ledger_present")),
        "final_pregame_published_grade": bool(health.get("final_pregame_grade_ledger_present")),
        "dominance_or_margin_diagnostic": bool(health.get("dominance_diagnostic_present")),
        "chronological_challenger": bool(health.get("chronological_challenger_evidence_present")),
        "untouched_holdout": bool(health.get("untouched_holdout_evidence_present")),
        "typed_failure_preservation": bool(health.get("typed_failure_preservation_verified")),
        "can_execute": False,
    }


def install_probability_quality_parity_overlay() -> dict[str, Any]:
    """Add quality-parity metadata to team-event health; never alter scoring."""
    import v17.team_event_bridge_runtime as bridges

    if getattr(bridges, "_v17_probability_quality_parity_installed", False):
        return {"status": "ALREADY_INSTALLED", "can_execute": False}

    original_health = bridges.team_event_bridge_health

    def quality_health() -> dict[str, dict[str, Any]]:
        base = original_health()
        out: dict[str, dict[str, Any]] = {}
        for sport, value in base.items():
            row = dict(value)
            normalized = normalize_quality_sport(sport)
            if normalized in TARGET_SPORTS:
                evidence = readiness_quality_evidence(normalized, row)
                assessment = assess_probability_quality_evidence(normalized, evidence)
                row.update(
                    {
                        "probability_quality_parity_status": assessment["status"],
                        "probability_quality_parity_blockers": assessment["blockers"],
                        "probability_quality_required_dimensions": assessment["required_dimensions"],
                        "probability_quality_required_metrics": assessment["required_calibration_metrics"],
                        "probability_quality_parity_version": QUALITY_PARITY_VERSION,
                    }
                )
            out[sport] = row
        return out

    bridges.team_event_bridge_health = quality_health
    bridges._v17_probability_quality_parity_original_health = original_health
    bridges._v17_probability_quality_parity_installed = True
    return {
        "status": "INSTALLED",
        "quality_parity_version": QUALITY_PARITY_VERSION,
        "target_sports": list(TARGET_SPORTS),
        "automatic_promotion": False,
        "global_terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


__all__ = [
    "AUTOMATIC_PROMOTION",
    "CAN_EXECUTE",
    "QUALITY_PARITY_VERSION",
    "REQUIRED_CALIBRATION_METRICS",
    "REQUIRED_DIMENSIONS",
    "TARGET_SPORTS",
    "assess_probability_quality_evidence",
    "install_probability_quality_parity_overlay",
    "normalize_quality_sport",
    "readiness_quality_evidence",
]
