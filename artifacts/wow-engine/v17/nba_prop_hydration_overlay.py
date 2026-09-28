"""Install research-only NBA prop hydration into the canonical sport router.

The extension is intentionally narrow. It registers only NBA scalar/composite
candidate routes that already have fitted research artifacts. The public caller
seam remains ``prop_auto_hydration_router.auto_hydrate_prop_evidence``; this
module only extends that router at startup and never grants model/publication
or execution authority.
"""
from __future__ import annotations

import sys
from typing import Any, Mapping

import prop_auto_hydration_router as router
from prop_auto_hydration import PropAutoHydrationError
from v17.nba_prop_auto_hydration import (
    NBAPropHydrationError,
    PROVIDER_ID,
    canonical_stat,
    hydrate_nba_prop_evidence,
)

CAN_EXECUTE = False
_ORIGINAL_PROVIDER_FOR_SPORT = router.provider_for_sport
_ORIGINAL_AUTO_HYDRATE = router.auto_hydrate_prop_evidence
_ORIGINAL_PROVIDER_EVENT_IDS = router._provider_event_ids
_INSTALLED = False


def provider_for_sport(sport: str, stat_type: str | None = None) -> str:
    if str(sport or "").strip().upper() == "NBA":
        try:
            canonical_stat(str(stat_type or ""))
        except NBAPropHydrationError:
            return router.UNREGISTERED_PROVIDER
        return PROVIDER_ID
    return _ORIGINAL_PROVIDER_FOR_SPORT(sport, stat_type)


def _provider_event_ids(sport: str, role_status: Mapping[str, Any]) -> dict[str, str]:
    if str(sport or "").strip().upper() == "NBA":
        value = role_status.get("official_game_id")
        return {"NBA_OFFICIAL": str(value)} if str(value or "").strip() else {}
    return _ORIGINAL_PROVIDER_EVENT_IDS(sport, role_status)


def auto_hydrate_prop_evidence(*, sport: str, player: str, stat_type: str, event_start_time: str, **kwargs: Any) -> dict[str, Any]:
    normalized_sport = str(sport or "").strip().upper()
    if normalized_sport != "NBA":
        return _ORIGINAL_AUTO_HYDRATE(
            sport=sport,
            player=player,
            stat_type=stat_type,
            event_start_time=event_start_time,
            **kwargs,
        )

    canonical_event_id, opponent = router._context_identity(
        sport="NBA",
        player=player,
        event_start_time=event_start_time,
        canonical_event_id=kwargs.pop("canonical_event_id", None),
        opponent=kwargs.get("opponent"),
    )
    kwargs["opponent"] = opponent
    try:
        result = hydrate_nba_prop_evidence(
            player=player,
            stat_type=stat_type,
            event_start_time=event_start_time,
            **kwargs,
        )
    except NBAPropHydrationError as exc:
        raise PropAutoHydrationError(exc.code, str(exc), detail=exc.detail) from exc
    result = dict(result)
    result.pop("hydration_provider", None)
    return router._bind_event_identity(
        result,
        sport="NBA",
        canonical_event_id=canonical_event_id,
        requested_opponent=opponent,
    )


# This callable becomes the canonical router export when installed. Preserve that
# public seam in introspection as well as behavior; downstream code must never be
# taught to import this overlay directly.
auto_hydrate_prop_evidence.__module__ = router.__name__
provider_for_sport.__module__ = router.__name__


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    router.provider_for_sport = provider_for_sport
    router._provider_event_ids = _provider_event_ids
    router.auto_hydrate_prop_evidence = auto_hydrate_prop_evidence

    # Modules that imported the canonical router seam before this startup overlay
    # was installed retain a Python function reference. Repoint those references
    # to the router export, never to an overlay-specific public API.
    for module_name in ("pick_request_runtime", "v17.interactive_pick_hydration"):
        module = sys.modules.get(module_name)
        if module is not None:
            setattr(module, "auto_hydrate_prop_evidence", router.auto_hydrate_prop_evidence)
            if module_name == "pick_request_runtime":
                setattr(module, "provider_for_sport", router.provider_for_sport)
    _INSTALLED = True


install()


__all__ = ["CAN_EXECUTE", "PROVIDER_ID", "install"]
