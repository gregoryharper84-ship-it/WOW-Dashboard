"""WOW V17 active-generation modules.

Importing the package in shared lower-layer tests must not mutate runtime routing.
Response semantics, immutable projected-score handoff, and numerical certification
are composed only in an explicitly active V17 runtime. None may choose a sporting
model, alter a fitted probability, or authorize execution.
"""
from __future__ import annotations

import os
import sys


def get_certified_numerical_registry():
    from v17.certified_numerical_engine import DEFAULT_NUMERICAL_REGISTRY
    return DEFAULT_NUMERICAL_REGISTRY


def compose_active_runtime() -> bool:
    if os.getenv("WOW_V17_ACTIVE", "0") != "1":
        return False
    from v17.prop_response_semantics import install_prop_response_semantics
    from v17.projected_lineup_scenario_modeling import install_projected_lineup_semantics
    from v17.projected_lineup_probability_rehydration import install_projected_lineup_score_rehydration
    from v17.numerical_engine_production_bridge import install_production_bridges
    from v17 import team_event_request_runtime as team_runtime

    get_certified_numerical_registry()
    prop_ok = install_prop_response_semantics()
    lineup_ok = install_projected_lineup_semantics()
    rehydration_ok = install_projected_lineup_score_rehydration(team_runtime)

    market_api = sys.modules.get("api_prod_market")
    numerical_ok = False
    if market_api is not None:
        numerical_ok = install_production_bridges(market_api=market_api, team_event_module=team_runtime)

    return bool(
        prop_ok or lineup_ok or rehydration_ok or numerical_ok
        or getattr(market_api, "_v17_certified_numerical_bridge_installed", False)
    )


compose_active_runtime()


__all__ = ["compose_active_runtime", "get_certified_numerical_registry"]
