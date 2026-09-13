"""Narrow compatibility path for projected-lineup MLB team/event scoring.

The V17 direct prospective specialist requires a confirmed strict-pregame lineup.
That is correct for the prospective re-score, but a separate V17 contract already
preserves an immutable *previously scored* projected-lineup probability while the
final lineup is pending.  This adapter joins those two contracts without relaxing
either one:

* confirmed lineups continue through the V17 direct prospective specialist;
* a direct MODEL_INPUTS_INSUFFICIENT caused only by missing lineup confirmation
  may fall back to the pre-existing validated held bridge receipt;
* only an exact SHADOW_SCORED_LINEUP_PENDING receipt is accepted, with numeric
  probability fields still withheld; and
* downstream projected-lineup rehydration may then recover the immutable score
  snapshot under its existing strict checks, while rank/final publication stays
  held for lineup confirmation.

No probability is recomputed, interpolated, or manufactured here. can_execute is
always false.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException

CAN_EXECUTE = False

_PROJECTED_LINEUP_FIELDS = {"home_lineup_status", "away_lineup_status"}
_PROJECTED_LINEUP_STATES = {
    "NOT_YET_AVAILABLE",
    "PROJECTED",
    "PROJECTED_HIGH_CONFIDENCE",
    "PROJECTED_MEDIUM_CONFIDENCE",
}
_ALLOWED_PROJECTED_BLOCKERS = {
    "LINEUP_NOT_CONFIRMED",
    "OFFICIAL_LINEUP_REFRESH_OFFICIAL_LINEUP_NOT_AVAILABLE",
    "POST_LINEUP_SCORE_SNAPSHOT_REQUIRED",
}
_NUMERIC_PROBABILITY_FIELDS = {
    "raw_home_probability",
    "raw_away_probability",
    "independent_home_probability",
    "independent_away_probability",
    "calibrated_home_probability",
    "calibrated_away_probability",
    "calibrated_home_lower_bound",
    "calibrated_home_upper_bound",
    "calibrated_away_lower_bound",
    "calibrated_away_upper_bound",
    "projected_runs_home",
    "projected_runs_away",
    "tie_after_9_probability",
}


def _detail(exc: HTTPException) -> dict[str, Any]:
    return dict(exc.detail) if isinstance(exc.detail, dict) else {}


def _is_lineup_only_direct_hold(exc: HTTPException) -> bool:
    detail = _detail(exc)
    missing = {str(value) for value in (detail.get("missing_fields") or [])}
    return bool(
        exc.status_code == 422
        and detail.get("code") == "MODEL_INPUTS_INSUFFICIENT"
        and detail.get("blocker_code") == "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE"
        and missing == _PROJECTED_LINEUP_FIELDS
        and detail.get("sport_model_selected") is True
        and detail.get("sport_model_invoked") is False
        and detail.get("can_execute") is False
    )


def _valid_projected_held_receipt(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    blockers = {str(value) for value in (payload.get("current_publication_blockers") or [])}
    numeric_leak = bool(_NUMERIC_PROBABILITY_FIELDS.intersection(payload))
    return bool(
        payload.get("code") == "REAL_FITTED_MODEL_PATH_PROVEN"
        and payload.get("scoring_evidence_produced") is True
        and payload.get("probability_fields_withheld") is True
        and payload.get("probability_publishable") is False
        and payload.get("can_execute") is False
        and payload.get("score_status") == "SHADOW_SCORED_LINEUP_PENDING"
        and str(payload.get("lineup_status") or "").upper() in _PROJECTED_LINEUP_STATES
        and payload.get("feature_hydration_status") == "PASS"
        and payload.get("calibration_health_status") == "PASS"
        and payload.get("governed_probability_capability") == "AVAILABLE"
        and payload.get("ratification_status") == "RATIFIED"
        and bool(payload.get("score_snapshot_id"))
        and bool(payload.get("shadow_event_id"))
        and bool(payload.get("server_snapshot_id"))
        and blockers
        and blockers <= _ALLOWED_PROJECTED_BLOCKERS
        and not numeric_leak
    )


def install_projected_lineup_direct_bridge_compat(*, market_api: Any) -> bool:
    """Wrap the already-installed V17 direct event scorer, once, at startup."""
    prod = getattr(market_api, "prod", None)
    event_api = getattr(prod, "event_api", None)
    if event_api is None:
        return False
    if getattr(event_api, "_v17_projected_lineup_direct_bridge_compat_installed", False):
        return True

    direct_score: Callable[[Any], dict[str, Any]] | None = getattr(event_api, "score_event", None)
    legacy_score: Callable[[Any], dict[str, Any]] | None = getattr(event_api, "_v17_original_score_event", None)
    if not callable(direct_score) or not callable(legacy_score):
        return False

    def score_event_with_projected_lineup_compat(req: Any) -> dict[str, Any]:
        try:
            return direct_score(req)
        except HTTPException as direct_exc:
            if not _is_lineup_only_direct_hold(direct_exc):
                raise

            # The legacy bridge is used only to retrieve its validated, withheld
            # receipt for an already-scored projected-lineup event. It is not a
            # substitute fitted model and its numeric probability fields are never
            # accepted here.
            try:
                receipt = legacy_score(req)
            except HTTPException:
                raise direct_exc

            if not isinstance(receipt, dict):
                raise direct_exc

            # If final refresh happened to confirm the lineup during the receipt
            # call, do not accept a legacy numeric publication. Re-enter the direct
            # prospective specialist once using the newly confirmed evidence.
            if receipt.get("code") == "GOVERNED_PROBABILITY_PUBLISHED":
                return direct_score(req)

            if not _valid_projected_held_receipt(receipt):
                raise direct_exc

            out = dict(receipt)
            out["projected_lineup_direct_bridge_compat"] = {
                "status": "PASS_HELD_RECEIPT_ONLY",
                "source": "VALIDATED_LEGACY_HELD_BRIDGE_RECEIPT",
                "probabilities_recomputed": False,
                "probabilities_exposed": False,
                "direct_prospective_model_invoked": False,
                "final_refresh_required": True,
                "can_execute": False,
            }
            out["can_execute"] = False
            return out

    event_api.score_event = score_event_with_projected_lineup_compat
    event_api._v17_projected_lineup_direct_bridge_compat_installed = True
    return True


__all__ = ["install_projected_lineup_direct_bridge_compat"]
