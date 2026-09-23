"""Cross-sport /score-pick-request compatibility for research candidates.

Fitted candidates need evidence acquisition before exact-line forward calibration
can mature. This bridge permits only that evidence-building path for explicitly
activated, non-promoted CANDIDATE/SHADOW artifacts. Genuine artifact absence is
refined through the V17 capability manifest; transport, registry, RPC, scorer,
and malformed-response failures are never rewritten.

The module also composes the WNBA composite and MLB player-doubles candidate
control-plane routes. These research routes can never certify, promote, publish,
rank, price, or execute.
"""
from __future__ import annotations

from typing import Any

import pick_request_runtime_core as _runtime_core
from v17.mlb_player_doubles_candidate_bridge import candidate_preflight as _mlb_doubles_candidate_preflight
from v17.nba_scalar_candidate_bridge import candidate_preflight as _nba_scalar_candidate_preflight
from v17.wnba_composite_candidate_bridge import candidate_preflight as _wnba_composite_candidate_preflight
from v17.prop_capability_manifest import prop_capability as _prop_capability, runtime_prop_stat_aliases

RESEARCH_ROUTES = {
    ("NFL", "FANTASY_SCORE"): "wow.nfl-dfs-fantasy-score-expert",
    ("NBA", "FANTASY_SCORE"): "wow.nba-dfs-fantasy-score-expert",
    ("WNBA", "FANTASY_SCORE"): "wow.wnba-dfs-fantasy-score-expert",
    ("MLB", "HITTER_FANTASY_SCORE"): "wow.mlb-hitter-fantasy-score-expert",
    ("MLB", "PITCHER_FANTASY_SCORE"): "wow.mlb-pitcher-fantasy-score-expert",
}

_ARTIFACT_ABSENCE_CODES = frozenset({
    "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
    "PROP_MODEL_ARTIFACT_NOT_FOUND",
})

_runtime_core.PROP_STAT_ALIASES.update(runtime_prop_stat_aliases())


def _key(sport: Any, stat_type: Any) -> tuple[str, str]:
    return (str(sport or "").strip().upper(), str(stat_type or "").strip().upper())


def _manifest_typed_absence(sport: Any, stat_type: Any, production_route: Any) -> dict[str, Any] | None:
    if not isinstance(production_route, dict):
        return None
    code = str(production_route.get("code") or "").strip().upper()
    if code not in _ARTIFACT_ABSENCE_CODES:
        return None
    capability = _prop_capability(str(sport or ""), str(stat_type or ""))
    typed = dict(production_route)
    typed.update({
        "ok": False,
        "code": capability.blocker or code,
        "capability_lane_status": capability.lane_status,
        "capability_route_active": capability.route_active,
        "controlling_specialist": capability.controlling_specialist,
        "declared_skill_status": capability.declared_skill_status,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    })
    typed["capability_manifest"] = capability.as_dict()
    return typed


def research_candidate_preflight(market_api: Any, sport: Any, stat_type: Any, production_route: Any) -> dict[str, Any] | None:
    """Return research-evidence compatibility or an exact lifecycle blocker.

    A research candidate is consulted only after production has failed to expose
    an exact publishable route. Registry/RPC/transport/scorer failures for already
    selected production routes still pass through unchanged. The MLB doubles
    challenger is intentionally allowed to refine the current generic
    ``MODEL_UNAVAILABLE`` preflight because that route previously had no declared
    production specialist at all; its return remains research-only/nonpublishable.
    """
    if not isinstance(production_route, dict):
        return None

    mlb_doubles = _mlb_doubles_candidate_preflight(market_api, sport, stat_type, production_route)
    if mlb_doubles is not None:
        return mlb_doubles

    route_code = str(production_route.get("code") or "").strip().upper()
    if route_code not in _ARTIFACT_ABSENCE_CODES:
        return None

    sport_n, stat_n = _key(sport, stat_type)
    specialist = RESEARCH_ROUTES.get((sport_n, stat_n))
    if specialist is None:
        if not hasattr(market_api, "prod"):
            return _manifest_typed_absence(sport, stat_type, production_route)
        for candidate in (_wnba_composite_candidate_preflight, _nba_scalar_candidate_preflight):
            result = candidate(market_api, sport, stat_type, production_route)
            if result is not None:
                return result
        return _manifest_typed_absence(sport, stat_type, production_route)

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
        return _manifest_typed_absence(sport, stat_type, production_route)

    rows = [dict(row) for row in (result.data or [])]
    if len(rows) != 1:
        return _manifest_typed_absence(sport, stat_type, production_route)
    artifact = rows[0]
    lifecycle = str(artifact.get("lifecycle_state") or "").strip().upper()
    if lifecycle not in {"CANDIDATE", "SHADOW"}:
        return _manifest_typed_absence(sport, stat_type, production_route)
    if artifact.get("candidate_research_active") is not True:
        return _manifest_typed_absence(sport, stat_type, production_route)
    if any(artifact.get(field) is True for field in ("promoted", "active", "probability_publishable", "can_execute")):
        return _manifest_typed_absence(sport, stat_type, production_route)
    if str(artifact.get("specialist_version") or "").split("@", 1)[0] != specialist:
        return _manifest_typed_absence(sport, stat_type, production_route)

    original = dict(production_route)
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

    family = str(candidate.get("candidate_family") or "").strip().upper()
    terminal_cause = {
        "NBA_SCALAR": "NBA_SCALAR_CANDIDATE_UNCALIBRATED",
        "WNBA_COMPOSITE": "WNBA_COMPOSITE_CANDIDATE_UNCALIBRATED",
        "MLB_PLAYER_DOUBLES": "MLB_PLAYER_DOUBLES_FORWARD_CALIBRATION_REQUIRED",
    }.get(family, "FANTASY_SCORE_CANDIDATE_UNCALIBRATED")
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
        "terminal_cause": terminal_cause,
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


def _install_wnba_composite_registration_overlay() -> None:
    import v17.wnba_prop_candidate_registry as base_registry
    from v17.wnba_composite_candidate_registry import install_wnba_composite_candidate_registration_route

    original = base_registry.install_wnba_prop_candidate_registration_route
    if getattr(original, "_v17_composite_overlay", False):
        return

    def combined(app: Any, *, auth_dependency: Any, db_client_fn: Any) -> None:
        original(app, auth_dependency=auth_dependency, db_client_fn=db_client_fn)
        install_wnba_composite_candidate_registration_route(
            app,
            auth_dependency=auth_dependency,
            db_client_fn=db_client_fn,
        )

    combined._v17_composite_overlay = True  # type: ignore[attr-defined]
    base_registry.install_wnba_prop_candidate_registration_route = combined


_install_wnba_composite_registration_overlay()


__all__ = ["RESEARCH_ROUTES", "research_candidate_outcome", "research_candidate_preflight"]
