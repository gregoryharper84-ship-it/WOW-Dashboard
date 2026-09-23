"""Post-score TheRundown market/value bridge for LLP team-event scoring.

Unlike the legacy pre-score bridge, this wrapper calls the governed sporting
specialist first and resolves market evidence only after the probability package
has returned. Therefore sportsbook evidence cannot become an input to sporting
probability through this bridge.
"""
from __future__ import annotations

from threading import RLock
from typing import Any, Callable

from v17.rundown_market_value import build_moneyline_market_features, build_value_lane

CAN_EXECUTE = False
BRIDGE_SOURCE = "RUNDOWN_MARKET_EVIDENCE"
_MARKET_RELATIVE_INTENTS = {"FAVORITE", "UNDERDOG", "UPSET"}
_LOCK = RLock()


def _favorite_from_prior(req: Any, prior: dict[str, Any] | None) -> str | None:
    prior = dict(prior or {})
    try:
        home = float(prior["home_probability"])
        away = float(prior["away_probability"])
    except (KeyError, TypeError, ValueError):
        return None
    if home == away:
        return None
    return str(getattr(req, "home_team", "")) if home > away else str(getattr(req, "away_team", ""))


def _runtime_resolver() -> Callable[[Any], dict[str, Any]]:
    from v17.llp_rundown_market_bridge import resolve_rundown_market_context

    return resolve_rundown_market_context


def _features_from_context(context: dict[str, Any]) -> dict[str, Any]:
    if context.get("status") != "EXACT_LINE":
        return {
            "status": "MARKET_FEATURES_UNAVAILABLE",
            "reason_code": context.get("reason_code") or context.get("status"),
            "probability_mutated_by_evidence": False,
            "prediction_authority": False,
            "market_role_evidence_only": True,
            "can_execute": False,
        }
    return build_moneyline_market_features(
        current=context.get("current"),
        opening=context.get("opening") if isinstance(context.get("opening"), dict) else None,
        closing=context.get("closing") if isinstance(context.get("closing"), dict) else None,
    )


def install_llp_rundown_value_bridge(
    team_event_module: Any,
    *,
    resolver: Callable[[Any], dict[str, Any]] | None = None,
) -> bool:
    """Install a post-score market/value wrapper without mutating ``market_prior``."""
    if getattr(team_event_module, "_v17_llp_rundown_value_bridge_installed", False):
        return True
    original = getattr(team_event_module, "score_team_event_request", None)
    if not callable(original):
        return False
    resolve = resolver or _runtime_resolver()

    def score_with_postscore_market(req: Any, *, event_api: Any, canonical_hydration_required: bool = False):
        with _LOCK:
            caller_prior = dict(getattr(req, "market_prior", None) or {})
            caller_favorite = _favorite_from_prior(req, caller_prior)

            # Probability ownership ends here before TheRundown is consulted.
            result = original(
                req,
                event_api=event_api,
                canonical_hydration_required=canonical_hydration_required,
            )
            if not isinstance(result, dict):
                return result

            out = dict(result)
            try:
                context = resolve(req)
            except Exception as exc:  # evidence failure must never masquerade as model failure
                context = {
                    "status": "MARKET_DATA_UNOBTAINABLE",
                    "provider": BRIDGE_SOURCE,
                    "reason_code": f"RUNDOWN_POSTSCORE_RESOLVER_{type(exc).__name__.upper()}",
                    "prediction_authority": False,
                    "market_role_evidence_only": True,
                    "can_execute": False,
                }

            features = _features_from_context(context)
            value_lane = (
                build_value_lane(out, features)
                if features.get("status") != "MARKET_FEATURES_UNAVAILABLE"
                else {
                    "status": "MARKET_DATA_UNAVAILABLE",
                    "reason_code": features.get("reason_code"),
                    "probability_rank_mutated": False,
                    "prediction_authority": False,
                    "can_execute": False,
                }
            )

            rundown_favorite = context.get("favorite") if context.get("status") == "EXACT_LINE" else None
            conflict = bool(caller_favorite and rundown_favorite and caller_favorite != rundown_favorite)
            out["llp_rundown_market_evidence"] = {
                **context,
                "caller_market_source": caller_prior.get("source"),
                "caller_favorite": caller_favorite,
                "rundown_favorite": rundown_favorite,
                "favorite_status_conflict": conflict,
                "probability_mutated_by_bridge": False,
                "market_prior_mutated_by_bridge": False,
                "can_execute": False,
            }
            out["llp_market_features"] = features
            out["llp_market_value"] = value_lane

            intent = str(getattr(req, "decision_intent", "WINNER") or "WINNER").upper()
            if conflict and intent in _MARKET_RELATIVE_INTENTS:
                out["rank_eligible"] = False
                out["blockers"] = sorted(set([*(out.get("blockers") or []), "FAVORITE_STATUS_CONFLICT"]))
                out["market_role_status"] = "SOURCE_CONFLICT"

            out["can_execute"] = False
            return out

    team_event_module.score_team_event_request = score_with_postscore_market
    team_event_module._v17_llp_rundown_value_bridge_original = original
    team_event_module._v17_llp_rundown_value_bridge_installed = True
    return True


__all__ = ["BRIDGE_SOURCE", "CAN_EXECUTE", "install_llp_rundown_value_bridge"]
