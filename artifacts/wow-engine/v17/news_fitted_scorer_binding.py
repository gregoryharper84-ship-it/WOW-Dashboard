"""Bind news-triggered rescores to registered controlling fitted specialists.

Production Scout previously called ``dispatch_news_rescores_for_handoff`` with
no ``score_fn``, so every requested rescore collapsed into the dispatcher's
internal ``_missing_fitted_scorer`` placeholder. This module is the operational
binding surface: routes resolve to registered adapters, unregistered routes
return typed ``MODEL_UNAVAILABLE``, and no adapter may invent probability.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, MutableMapping

CAN_EXECUTE = False
ScoreFn = Callable[[Mapping[str, Any]], Mapping[str, Any]]

# Route registry is intentionally empty until a certified adapter is registered
# for an exact sport/route. Tests may register temporary adapters.
_NEWS_FITTED_SCORER_REGISTRY: MutableMapping[tuple[str, str], ScoreFn] = {}


def _unavailable(*, blockers: list[str], code: str = "MODEL_UNAVAILABLE") -> dict[str, Any]:
    return {
        "ok": False,
        "code": code,
        "blockers": list(blockers),
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "model_version": None,
        "immutable_prediction_id": None,
        "calibration_receipt_id": None,
        "terminal_readback": None,
        "prediction_authority": False,
        "can_execute": False,
    }


def register_news_fitted_scorer(
    sport_key: str,
    route: str,
    score_fn: ScoreFn,
) -> None:
    """Register one exact sport/route adapter (tests and authorized wiring)."""
    sport = str(sport_key or "").strip()
    specialist_route = str(route or "").strip()
    if not sport or not specialist_route:
        raise ValueError("NEWS_FITTED_SCORER_REGISTRY_KEY_INVALID")
    if not callable(score_fn):
        raise ValueError("NEWS_FITTED_SCORER_ADAPTER_NOT_CALLABLE")
    _NEWS_FITTED_SCORER_REGISTRY[(sport, specialist_route)] = score_fn


def clear_news_fitted_scorer_registry() -> None:
    _NEWS_FITTED_SCORER_REGISTRY.clear()


def lookup_news_fitted_scorer(sport_key: str, route: str) -> ScoreFn | None:
    sport = str(sport_key or "").strip()
    specialist_route = str(route or "").strip()
    if not sport or not specialist_route:
        return None
    return _NEWS_FITTED_SCORER_REGISTRY.get((sport, specialist_route))


def production_news_fitted_scorer(request: Mapping[str, Any]) -> Mapping[str, Any]:
    """Resolve and invoke the registered controlling specialist for one request.

    Missing registration, blank route/sport, or adapter exceptions remain typed
    model failures. Successful adapters must still satisfy
    ``dispatch_news_rescore`` calibrated-pair and immutable receipt gates.
    """
    if not isinstance(request, Mapping):
        return _unavailable(blockers=["NEWS_FITTED_SCORER_REQUEST_INVALID"], code="MODEL_OUTPUT_INVALID")

    sport = str(request.get("sport_key") or "").strip()
    route = str(request.get("controlling_specialist_route") or "").strip()
    if not sport or not route:
        return _unavailable(blockers=["NEWS_FITTED_SCORER_ROUTE_INCOMPLETE"])

    adapter = lookup_news_fitted_scorer(sport, route)
    if adapter is None:
        return _unavailable(blockers=["NEWS_FITTED_SCORER_UNREGISTERED"])

    try:
        response = adapter(request)
    except Exception as exc:  # noqa: BLE001 - preserve typed scorer failure
        return {
            "ok": False,
            "code": "MODEL_SCORER_FAILED",
            "blockers": ["MODEL_SCORER_FAILED", type(exc).__name__],
            "calibrated_probability": None,
            "calibrated_lower_bound": None,
            "model_version": None,
            "immutable_prediction_id": None,
            "calibration_receipt_id": None,
            "terminal_readback": None,
            "prediction_authority": False,
            "can_execute": False,
        }

    if not isinstance(response, Mapping):
        return _unavailable(blockers=["MODEL_OUTPUT_INVALID"], code="MODEL_OUTPUT_INVALID")

    out = dict(response)
    out.setdefault("prediction_authority", False)
    out.setdefault("can_execute", False)
    if out.get("can_execute") is True or out.get("prediction_authority") is True:
        return _unavailable(
            blockers=["SCORER_CLAIMED_EXECUTION_OR_AUTHORITY"],
            code="MODEL_OUTPUT_INVALID",
        )
    return out


__all__ = [
    "CAN_EXECUTE",
    "clear_news_fitted_scorer_registry",
    "lookup_news_fitted_scorer",
    "production_news_fitted_scorer",
    "register_news_fitted_scorer",
]
