"""Exact-once continuation for governed V17 FULL moneyline slates.

The cross-sport resilience layer intentionally bounds model invocations. That
limit is a *batch* safety control, not permission to publish a partial FULL
slate. This overlay keeps compact/canary behavior unchanged while exhausting a
FULL MONEYLINE discovery inventory through sequential bounded batches.

No sporting probability, calibration, ranking threshold, terminal authority,
or execution posture is changed. ``can_execute`` remains false.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Callable

CAN_EXECUTE = False
CONTRACT_VERSION = "V17_FULL_SLATE_EXACT_ONCE_V1"

_FULL_SLATE_CONTINUATION_ACTIVE: ContextVar[bool] = ContextVar(
    "wow_v17_full_slate_continuation_active",
    default=False,
)


def _is_full_moneyline_request(req: Any) -> bool:
    response_mode = str(getattr(req, "response_mode", "") or "").upper()
    lanes = {str(value).upper() for value in (getattr(req, "lanes", None) or ())}
    return response_mode == "FULL" and "MONEYLINE" in lanes


def _route_full_slate_in_batches(
    original_route: Callable[..., list[Any]],
    inventory: Any,
    *args: Any,
    batch_size: int,
    **kwargs: Any,
) -> list[Any]:
    """Route one immutable discovery inventory in sequential bounded batches.

    Discovery is performed once upstream. We only partition the already-frozen
    inventory, so provider identity and acquisition evidence are not re-fetched
    between continuation batches. Each discovered event is presented to the
    canonical router exactly once in this call.
    """
    from v17 import cross_sport_resilience_overlay as resilience

    score_row = kwargs.get("score_row")
    if not callable(score_row) or not _FULL_SLATE_CONTINUATION_ACTIVE.get():
        return original_route(inventory, *args, **kwargs)

    ordered = resilience._round_robin_events(
        list(getattr(inventory, "events", []) or [])
    )
    if not ordered:
        return original_route(inventory, *args, **kwargs)

    bounded_batch = max(1, min(int(batch_size), resilience.model_invocation_limit()))
    original_events = getattr(inventory, "events", None)
    combined: list[Any] = []

    try:
        for offset in range(0, len(ordered), bounded_batch):
            batch = ordered[offset : offset + bounded_batch]
            inventory.events = batch

            # The resilience wrapper otherwise inherits Daily's *remaining total*
            # request budget. For FULL-slate continuation that would turn a
            # 12-row safety bound into a terminal slate cap (and can become zero
            # after the canonical MLB lane). Override it only for this batch,
            # using the exact batch cardinality so the receipt remains truthful.
            token = resilience._REQUEST_MODEL_INVOCATION_LIMIT.set(len(batch))
            try:
                batch_rows = original_route(inventory, *args, **kwargs)
            finally:
                resilience._REQUEST_MODEL_INVOCATION_LIMIT.reset(token)

            combined.extend(list(batch_rows or ()))
    finally:
        inventory.events = original_events

    return combined


def install_full_slate_exact_once() -> dict[str, Any]:
    """Install FULL-moneyline continuation outside the resilience wrapper."""
    from v17 import cross_sport_resilience_overlay as resilience
    from v17 import cross_sport_winner_discovery as discovery
    from v17 import daily_snapshot_runtime as daily

    if getattr(discovery, "_v17_full_slate_exact_once_installed", False):
        return {
            "status": "ALREADY_INSTALLED",
            "contract_version": CONTRACT_VERSION,
            "can_execute": False,
        }

    original_route = discovery.route_discovered_slate
    original_daily_cross = getattr(daily, "_cross_sport_moneyline_rows", None)

    def exact_once_route(inventory: Any, *args: Any, **kwargs: Any):
        return _route_full_slate_in_batches(
            original_route,
            inventory,
            *args,
            batch_size=resilience.model_invocation_limit(),
            **kwargs,
        )

    def exact_once_daily_cross(req: Any, *args: Any, **kwargs: Any):
        if not callable(original_daily_cross):
            return None
        token = _FULL_SLATE_CONTINUATION_ACTIVE.set(_is_full_moneyline_request(req))
        try:
            return original_daily_cross(req, *args, **kwargs)
        finally:
            _FULL_SLATE_CONTINUATION_ACTIVE.reset(token)

    discovery.route_discovered_slate = exact_once_route
    if callable(original_daily_cross):
        daily._cross_sport_moneyline_rows = exact_once_daily_cross

    discovery._v17_full_slate_exact_once_original_route = original_route
    discovery._v17_full_slate_exact_once_original_daily_cross = original_daily_cross
    discovery._v17_full_slate_exact_once_installed = True

    return {
        "status": "INSTALLED",
        "contract_version": CONTRACT_VERSION,
        "batch_model_invocation_limit": resilience.model_invocation_limit(),
        "full_moneyline_only": True,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CONTRACT_VERSION",
    "_is_full_moneyline_request",
    "_route_full_slate_in_batches",
    "install_full_slate_exact_once",
]
