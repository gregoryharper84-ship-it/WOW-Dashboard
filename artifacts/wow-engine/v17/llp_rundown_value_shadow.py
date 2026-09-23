"""Post-score, no-extra-fetch market/value shadow adapter for LLP team events.

The incumbent TheRundown bridge remains the production source of market context.
This adapter runs strictly after that scorer wrapper returns and consumes only the
already-attached ``llp_rundown_market_evidence`` object. It performs no provider
request, does not mutate ``req.market_prior``, and cannot change probability,
calibration bounds, rank eligibility, blockers, terminal status, or execution.
"""
from __future__ import annotations

import os
from typing import Any

from v17.rundown_market_value import build_moneyline_market_features, build_value_lane

CAN_EXECUTE = False
SHADOW_FLAG = "WOW_LLP_RUNDOWN_VALUE_SHADOW_ENABLED"


def enabled() -> bool:
    return os.environ.get(SHADOW_FLAG, "false").strip().lower() == "true"


def _features_from_existing_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    if evidence.get("status") != "EXACT_LINE":
        return {
            "status": "MARKET_FEATURES_UNAVAILABLE",
            "reason_code": evidence.get("reason_code") or evidence.get("status"),
            "probability_mutated_by_evidence": False,
            "prediction_authority": False,
            "market_role_evidence_only": True,
            "can_execute": False,
        }

    # Forward-compatible rich context: once the collector-backed adapter exposes
    # quote snapshots, use the full BEST/dispersion/movement feature engine.
    current = evidence.get("current")
    if isinstance(current, dict):
        return build_moneyline_market_features(
            current=current,
            opening=evidence.get("opening") if isinstance(evidence.get("opening"), dict) else None,
            closing=evidence.get("closing") if isinstance(evidence.get("closing"), dict) else None,
        )

    # Current production bridge exposes cross-book no-vig consensus but not raw
    # executable quote rows. Preserve that separation: model-vs-consensus can be
    # measured in shadow, while price edge remains unavailable rather than being
    # invented from consensus.
    home = evidence.get("home_probability")
    away = evidence.get("away_probability")
    try:
        home = float(home)
        away = float(away)
    except (TypeError, ValueError):
        home = away = None
    return {
        "status": "AVAILABLE" if home is not None and away is not None else "MARKET_FEATURES_UNAVAILABLE",
        "current": {
            "status": "AVAILABLE" if home is not None and away is not None else "NO_CONSENSUS_PROBABILITY",
            "timestamp": evidence.get("timestamp"),
            "book_count": evidence.get("book_count"),
            "consensus_no_vig_probability": {
                "home": home,
                "away": away,
                "method": evidence.get("quality") or "EXISTING_RUNDOWN_BRIDGE_CONSENSUS",
            },
            "best_price": {"home": None, "away": None},
            "best_executable_breakeven_probability": {"home": None, "away": None},
            "prediction_authority": False,
            "market_role_evidence_only": True,
            "can_execute": False,
        },
        "opening": None,
        "closing": None,
        "movement_from_open_pp": {"home": None, "away": None},
        "movement_current_to_close_pp": {"home": None, "away": None},
        "probability_mutated_by_evidence": False,
        "prediction_authority": False,
        "market_role_evidence_only": True,
        "can_execute": False,
    }


def install_llp_rundown_value_shadow(team_event_module: Any) -> bool:
    """Install the post-score shadow only when explicitly enabled."""
    if not enabled():
        return False
    if getattr(team_event_module, "_v17_llp_rundown_value_shadow_installed", False):
        return True
    original = getattr(team_event_module, "score_team_event_request", None)
    if not callable(original):
        return False

    def score_with_value_shadow(req: Any, *, event_api: Any, canonical_hydration_required: bool = False):
        result = original(
            req,
            event_api=event_api,
            canonical_hydration_required=canonical_hydration_required,
        )
        if not isinstance(result, dict):
            return result
        out = dict(result)
        evidence = dict(out.get("llp_rundown_market_evidence") or {})
        features = _features_from_existing_evidence(evidence)
        value = build_value_lane(out, features)
        out["llp_market_features_shadow"] = features
        out["llp_market_value_shadow"] = value
        out["can_execute"] = False
        return out

    team_event_module.score_team_event_request = score_with_value_shadow
    team_event_module._v17_llp_rundown_value_shadow_original = original
    team_event_module._v17_llp_rundown_value_shadow_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "SHADOW_FLAG",
    "enabled",
    "install_llp_rundown_value_shadow",
]
