"""Targeted V17 runtime repairs for the 2026-09-15 team/event regression.

This module fixes three composition/ingress defects without changing fitted model
weights, calibration, ranking semantics, terminal authority, or execution safety:

1. TheRundown Product V2 authentication uses the provider-documented
   ``X-TheRundown-Key`` request header rather than the obsolete query-key shape.
2. Optional ``market_prior`` evidence is validated/sanitized before Pydantic scorer
   ingress so malformed market evidence cannot turn a sporting-model attempt into
   a scorer exception. Invalid market context is omitted from the fitted scorer;
   the separate market handoff retains its own typed DATA_UNOBTAINABLE semantics.
3. The direct MLB bridge may discover that official lineups are not yet available
   even though a ratified immutable ``SHADOW_SCORED_LINEUP_PENDING`` fitted score
   already exists. In that exact case only, recover the canonical held bridge
   receipt and rehydrate the immutable score before LLP governance. Final ranking
   remains held until the official-lineup refresh.

Boston/Texas-style canonical hydration failures are deliberately untouched.
``can_execute`` remains false throughout.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from math import isfinite
from typing import Any

from fastapi import HTTPException

CAN_EXECUTE = False
_PROJECTED_LINEUPS = {
    "NOT_YET_AVAILABLE",
    "PROJECTED",
    "PROJECTED_HIGH_CONFIDENCE",
    "PROJECTED_MEDIUM_CONFIDENCE",
}
_PROJECTED_BLOCKERS = {
    "LINEUP_NOT_CONFIRMED",
    "OFFICIAL_LINEUP_REFRESH_OFFICIAL_LINEUP_NOT_AVAILABLE",
    "POST_LINEUP_SCORE_SNAPSHOT_REQUIRED",
}


def install_rundown_v2_auth_repair() -> bool:
    """Align TheRundown with the current Product V2 authentication contract."""
    from v17 import market_evidence_sources as sources

    provider = sources.PROVIDERS.get("RUNDOWN")
    if provider is None:
        return False
    if provider.auth_style == "header" and provider.auth_name == "X-TheRundown-Key":
        return True

    endpoints = dict(provider.endpoints)
    # The current provider reference catalog is V2. Keep the remaining endpoint
    # paths unchanged because they are already V2 and are covered independently.
    endpoints["sports"] = "/api/v2/sports"
    sources.PROVIDERS["RUNDOWN"] = replace(
        provider,
        key_envs=("THERUNDOWN_API_KEY", "RUNDOWN_API_KEY", "WOW_RUNDOWN_API_KEY"),
        auth_style="header",
        auth_name="X-TheRundown-Key",
        endpoints=endpoints,
        notes=(
            "Product V2 market evidence. Authenticate with X-TheRundown-Key; "
            "market evidence never becomes sporting probability."
        ),
    )
    return True


def sanitize_model_market_prior(prior: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return only a scorer-valid EventMarketPrior, otherwise no model prior.

    Market context is optional for the MLB fitted scorer (current certified weight
    is zero). A malformed optional object must therefore not create a Pydantic
    ``ValidationError`` that changes sporting-model eligibility.
    """
    if not isinstance(prior, dict) or not prior:
        return None

    try:
        home = float(prior["home_probability"])
        away = float(prior["away_probability"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (isfinite(home) and isfinite(away) and 0.0 < home < 1.0 and 0.0 < away < 1.0):
        return None
    if abs((home + away) - 1.0) > 1e-6:
        return None

    timestamp = prior.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp.strip():
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None

    safe: dict[str, Any] = {
        "home_probability": home,
        "away_probability": away,
        "timestamp": timestamp,
    }
    for key in ("quality", "source"):
        value = prior.get(key)
        if isinstance(value, str) and value.strip():
            safe[key] = value.strip()
    return safe


def install_market_prior_ingress_repair(team_runtime: Any) -> bool:
    """Keep optional market evidence outside the scorer failure boundary."""
    if getattr(team_runtime, "_v17_sep15_market_prior_ingress_repair_installed", False):
        return True
    if not callable(getattr(team_runtime, "_model_market_prior", None)):
        return False
    team_runtime._model_market_prior = sanitize_model_market_prior
    team_runtime._v17_sep15_market_prior_ingress_repair_installed = True
    return True


def _is_lineup_pending_failure(exc: HTTPException) -> bool:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    return bool(
        detail.get("status") == "MODEL_INPUTS_INSUFFICIENT"
        and detail.get("blocker_code") == "MLB_TEAM_EVENT_LINEUP_NOT_YET_AVAILABLE"
    )


def _projected_receipt_valid(receipt: Any, req: Any) -> bool:
    if not isinstance(receipt, dict):
        return False
    blockers = {str(value) for value in (receipt.get("current_publication_blockers") or [])}
    requested_snapshot = str(getattr(req, "source_snapshot_id", "") or "")
    server_snapshot = str(receipt.get("server_snapshot_id") or "")
    return bool(
        receipt.get("code") == "REAL_FITTED_MODEL_PATH_PROVEN"
        and receipt.get("scoring_evidence_produced") is True
        and receipt.get("probability_fields_withheld") is True
        and receipt.get("probability_publishable") is False
        and receipt.get("governed_probability_capability") == "AVAILABLE"
        and receipt.get("ratification_status") == "RATIFIED"
        and receipt.get("calibration_health_status") == "PASS"
        and receipt.get("feature_hydration_status") == "PASS"
        and receipt.get("score_status") == "SHADOW_SCORED_LINEUP_PENDING"
        and str(receipt.get("lineup_status") or "").upper() in _PROJECTED_LINEUPS
        and bool(receipt.get("score_snapshot_id"))
        and bool(receipt.get("shadow_event_id"))
        and bool(server_snapshot)
        and (not requested_snapshot or requested_snapshot == server_snapshot)
        and blockers
        and blockers <= _PROJECTED_BLOCKERS
        and receipt.get("can_execute") is False
    )


def _load_projected_bridge_receipt(req: Any, *, event_api: Any) -> dict[str, Any] | None:
    """Recover the already-produced immutable pre-lineup fitted-score receipt."""
    get_client = getattr(event_api, "get_client", None)
    if not callable(get_client):
        return None
    try:
        result = get_client().rpc(
            "wow_mlb_score_event_bridge",
            {
                "p_official_event_id": str(getattr(req, "official_event_id", "") or ""),
                "p_event_start_time": str(getattr(req, "event_start_time_utc", "") or ""),
                "p_requested_slate_date": str(getattr(req, "requested_slate_date", "") or ""),
                "p_home_team": str(getattr(req, "home_team", "") or ""),
                "p_away_team": str(getattr(req, "away_team", "") or ""),
                "p_venue": str(getattr(req, "venue", "") or ""),
                "p_home_starting_pitcher": str(getattr(req, "home_starting_pitcher", "") or ""),
                "p_away_starting_pitcher": str(getattr(req, "away_starting_pitcher", "") or ""),
                "p_source_snapshot_id": str(getattr(req, "source_snapshot_id", "") or ""),
            },
        ).execute()
        receipt = result.data
    except Exception:
        return None
    if not _projected_receipt_valid(receipt, req):
        return None
    out = dict(receipt)
    out.update({
        "status": "MODEL_SCORED_HELD_PROJECTED_LINEUP",
        "event_identity_complete": True,
        "sport_model_selected": True,
        "sport_model_invoked": True,
        "sport_model_invocation_source": "IMMUTABLE_PROJECTED_SCORE_SNAPSHOT",
        "probability_package_valid": False,
        "dynamic_calibration_complete": False,
        "probability_audit_passed": False,
        "event_governor_complete": False,
        "last_completed_stage": "SPORT_MODEL_INVOKED",
        "model_provider": receipt.get("controlling_specialist"),
        "source_snapshot_id": receipt.get("server_snapshot_id"),
        "source_snapshot_timestamp": receipt.get("server_snapshot_timestamp"),
        "rank_eligible": False,
        "can_execute": False,
    })
    return out


def install_post_mlb_bridge_repairs(*, market_api: Any, team_runtime: Any) -> bool:
    """Compose projected-score preservation *after* the direct bridge is installed."""
    if getattr(market_api, "_v17_sep15_mlb_contract_repairs_installed", False):
        return True
    event_api = getattr(getattr(market_api, "prod", None), "event_api", None)
    if event_api is None:
        return False
    original_score_event = getattr(event_api, "score_event", None)
    original_governance = getattr(team_runtime, "_run_mlb_llp_governance", None)
    if not callable(original_score_event) or not callable(original_governance):
        return False

    from v17.projected_lineup_probability_rehydration import rehydrate_projected_probability

    def score_event_with_projected_lineup(req: Any) -> dict[str, Any]:
        try:
            return original_score_event(req)
        except HTTPException as exc:
            # Preserve every canonical/input/scorer/output failure except the one
            # exact state where an immutable projected-lineup score already exists.
            if not _is_lineup_pending_failure(exc):
                raise
            receipt = _load_projected_bridge_receipt(req, event_api=event_api)
            if receipt is None:
                raise
            return receipt

    def governance_with_projected_rehydration(
        req: Any,
        route: Any,
        model_result: dict[str, Any],
        envelope: Any | None = None,
        *,
        event_api: Any,
    ) -> dict[str, Any]:
        hydrated = rehydrate_projected_probability(model_result, req, event_api=event_api)
        if (
            isinstance(hydrated, dict)
            and hydrated.get("projected_lineup_score_rehydration", {}).get("status") == "PASS"
        ):
            hydrated = dict(hydrated)
            hydrated.update({
                "event_identity_complete": True,
                "sport_model_selected": True,
                "sport_model_invoked": True,
                "probability_package_valid": True,
                "dynamic_calibration_complete": True,
                "last_completed_stage": "PROBABILITY_PACKAGE_VALIDATED",
                "model_provider": hydrated.get("controlling_specialist"),
                "source_snapshot_id": hydrated.get("server_snapshot_id"),
                "source_snapshot_timestamp": hydrated.get("server_snapshot_timestamp"),
                # Final rank/publication is intentionally still gated by the
                # official-lineup refresh and V17_TERMINAL_REDUCER.
                "rank_eligible": False,
                "can_execute": False,
            })
        return original_governance(
            req,
            route,
            hydrated,
            envelope=envelope,
            event_api=event_api,
        )

    event_api.score_event = score_event_with_projected_lineup
    team_runtime._run_mlb_llp_governance = governance_with_projected_rehydration
    team_runtime._v17_sep15_projected_lineup_composition_repair_installed = True
    market_api._v17_sep15_mlb_contract_repairs_installed = True
    return True


__all__ = [
    "install_market_prior_ingress_repair",
    "install_post_mlb_bridge_repairs",
    "install_rundown_v2_auth_repair",
    "sanitize_model_market_prior",
]
