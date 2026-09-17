"""Narrow /score-pick-request compatibility for Fantasy Score research candidates.

The legacy batch ingress preflight requires the production-artifact ready code
before it will even acquire/freeze evidence. Fantasy Score candidates need the
opposite ordering: acquire/freeze evidence -> candidate score -> immutable
forward prediction -> calibration evidence -> independent certification.

This module permits only that evidence-building path for an explicitly active,
non-promoted research candidate. It never changes the artifact lifecycle and it
never makes the raw candidate probability publishable or rank eligible.
"""
from __future__ import annotations

from typing import Any

RESEARCH_ROUTES = {
    ("MLB", "PITCHER_FANTASY_SCORE"): "wow.mlb-pitcher-fantasy-score-expert",
}


def _key(sport: Any, stat_type: Any) -> tuple[str, str]:
    return (
        str(sport or "").strip().upper(),
        str(stat_type or "").strip().upper(),
    )


def research_candidate_preflight(
    market_api: Any,
    sport: Any,
    stat_type: Any,
    production_route: Any,
) -> dict[str, Any] | None:
    """Return a legacy-preflight compatibility receipt for evidence capture only."""
    sport_n, stat_n = _key(sport, stat_type)
    specialist = RESEARCH_ROUTES.get((sport_n, stat_n))
    if specialist is None:
        return None

    try:
        db = market_api.prod.get_client()
        result = (
            db.table("wow_prop_fitted_model_artifacts")
            .select(
                "artifact_id,model_family,model_artifact_version,specialist_version,"
                "certification_id,lifecycle_state,promoted,active,probability_publishable,"
                "can_execute,candidate_research_active"
            )
            .eq("sport", sport_n)
            .eq("stat_type", stat_n)
            .eq("candidate_research_active", True)
            .limit(2)
            .execute()
        )
    except Exception:
        return None

    rows = [dict(row) for row in (result.data or [])]
    if len(rows) != 1:
        return None
    artifact = rows[0]
    lifecycle = str(artifact.get("lifecycle_state") or "").strip().upper()
    if lifecycle not in {"CANDIDATE", "SHADOW"}:
        return None
    if artifact.get("candidate_research_active") is not True:
        return None
    if any(artifact.get(field) is True for field in ("promoted", "active", "probability_publishable", "can_execute")):
        return None
    if str(artifact.get("specialist_version") or "").split("@", 1)[0] != specialist:
        return None

    original = dict(production_route) if isinstance(production_route, dict) else {}
    # ``pick_request_runtime_core`` currently accepts only this legacy ready code.
    # The compatibility receipt is explicitly marked research-only and never
    # escapes as a certification claim; the real resolver used by /score-prop
    # still sees the unmodified CANDIDATE lifecycle and selects the research
    # scorer. This field is removed once core preflight gains a first-class
    # PROP_RESEARCH_CANDIDATE_ARTIFACT_READY state.
    return {
        "ok": True,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        "preflight_compatibility_mode": "FANTASY_SCORE_RESEARCH_EVIDENCE_ONLY",
        "actual_artifact_lifecycle": lifecycle,
        "actual_certification_status": "CANDIDATE_ONLY",
        "original_production_route_code": original.get("code"),
        "artifact_id": artifact.get("artifact_id"),
        "model_family": artifact.get("model_family"),
        "model_artifact_version": artifact.get("model_artifact_version"),
        "controlling_specialist": specialist,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def research_candidate_outcome(**kwargs: Any) -> dict[str, Any] | None:
    """Translate a valid research candidate score into a typed calibration hold."""
    scored = kwargs.get("scored")
    if not isinstance(scored, dict):
        return None
    candidate = scored.get("candidate_model_output")
    if not isinstance(candidate, dict):
        return None
    if candidate.get("probability_publishable") is not False or candidate.get("rank_eligible") is not False:
        return None

    blockers = list(candidate.get("blockers") or [])
    forward = scored.get("forward_evidence") if isinstance(scored.get("forward_evidence"), dict) else {}
    blockers.extend(list(forward.get("blockers") or []))
    blockers.append("CALIBRATION_BLOCKED_NO_PUBLISH")
    blockers = list(dict.fromkeys(str(value) for value in blockers if str(value).strip()))

    return {
        "row_key": kwargs.get("row_key"),
        "terminal_status": "HELD",
        "code": "CALIBRATION_BLOCKED_NO_PUBLISH",
        "terminal_label": "CALIBRATION_BLOCKED_NO_PUBLISH",
        "confidence_tier": "RESEARCH_ONLY",
        "rank_eligible": False,
        "model_supported": True,
        "model_evaluated": True,
        "pick_rejected": False,
        "verdict_class": "CALIBRATION_HOLD",
        "infrastructure_blocked": False,
        "terminal_cause": "FANTASY_SCORE_CANDIDATE_UNCALIBRATED",
        "concurrent_infrastructure_blockers": [],
        "blockers": blockers,
        "downstream_money_evaluation_allowed": False,
        "downstream_portfolio_evaluation_allowed": False,
        "source_snapshot_id": kwargs.get("snapshot_id"),
        "evidence_fingerprint": kwargs.get("fingerprint"),
        "acquisition": kwargs.get("acquisition") or {},
        "result": scored,
        "probability_publishable": False,
        "specialist_scoring_attempted": True,
        "scoring_attempted": True,
        "can_execute": False,
    }


__all__ = [
    "RESEARCH_ROUTES",
    "research_candidate_outcome",
    "research_candidate_preflight",
]
