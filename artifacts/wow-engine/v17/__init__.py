"""WOW V17 active-generation modules.

Importing the package in shared lower-layer tests must not mutate runtime routing.
Response semantics, immutable projected-score handoff, numerical certification,
postmortem learning, and portfolio construction remain subordinate to V17 terminal
governance.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

_RUNTIME_TASKS: set[asyncio.Task] = set()
_MLB_BRIDGE_ACCEPTANCE_LOGGER = logging.getLogger("wow.v17.mlb.event_bridge.acceptance")


def get_certified_numerical_registry():
    from v17.certified_numerical_engine import DEFAULT_NUMERICAL_REGISTRY
    return DEFAULT_NUMERICAL_REGISTRY


def evaluate_game_winner_cash_single(*args, **kwargs):
    """Lazy public entrypoint for the downstream PrizePicks Game Winner cash gate."""
    from v17.game_winner_cash_single_gate import evaluate_game_winner_cash_single as _evaluate
    return _evaluate(*args, **kwargs)


def optimize_slip_portfolio(*args, **kwargs):
    """Lazy public entrypoint for Stage 17-19 card/session exposure governance."""
    from v17.slip_portfolio_optimizer import optimize_portfolio as _optimize
    return _optimize(*args, **kwargs)


def build_llp_postmortem_learning_report(*args, **kwargs):
    """Lazy analytical entrypoint for immutable LLP postmortem learning.

    This helper never mutates production model parameters and never authorizes
    execution. Any recalibration signal it returns is review-only.
    """
    from v17.llp_postmortem_recalibration import build_llp_learning_report as _build
    return _build(*args, **kwargs)


def _defer_mlb_event_bridge_install(*, market_api, team_runtime) -> bool:
    """Install the MLB bridge after module import, before the app becomes ready.

    Production starts with WOW_V17_ACTIVE=1. Installing the bridge while the v17
    package itself is still importing can mutate partially initialized modules and
    stall Uvicorn before application startup. Deferring the same governed repair
    to FastAPI startup keeps import side effects bounded while still installing
    the bridge before Render marks the service healthy.
    """
    app = getattr(market_api, "app", None)
    if app is None:
        return False
    if getattr(app.state, "v17_mlb_event_bridge_deferred", False):
        return True

    @app.on_event("startup")
    async def install_mlb_event_bridge_after_imports():
        from v17.mlb_event_bridge_repair import install_mlb_event_bridge_repair

        installed = install_mlb_event_bridge_repair(
            market_api=market_api,
            team_event_module=team_runtime,
        )

        # Keep the projected-lineup compatibility adapter startup-only as well.
        # Importing the direct bridge during v17 package initialization previously
        # stalled Uvicorn before port binding; do not reintroduce that import-time
        # mutation. The compatibility path is installed only after the direct
        # bridge has safely captured its original held-receipt scorer.
        projected_lineup_compat_installed = False
        if installed:
            from v17.projected_lineup_direct_bridge_compat import (
                install_projected_lineup_direct_bridge_compat,
            )
            projected_lineup_compat_installed = install_projected_lineup_direct_bridge_compat(
                market_api=market_api,
            )

        # team_event_probability_preservation is imported after v17.__init__ and
        # therefore captures the unpatched governance callable. Once the bridge is
        # safely installed at startup, point that wrapper at the patched callable
        # so evidence handoff still traverses the stage-audit taxonomy.
        preservation = sys.modules.get("v17.team_event_probability_preservation")
        if installed and preservation is not None:
            preservation._original_run_mlb_llp_governance = team_runtime._run_mlb_llp_governance

        if installed and not projected_lineup_compat_installed:
            _MLB_BRIDGE_ACCEPTANCE_LOGGER.error(
                "MLB_PROJECTED_LINEUP_DIRECT_BRIDGE_COMPAT=DOWN can_execute=false"
            )

        # A dedicated production flag runs one authenticated, non-secret smoke
        # test against a real confirmed pregame MLB event after startup completes.
        # It calls the public V17 HTTP boundary with the server-owned Action key,
        # logs no probability values, and can never execute a wager.
        if installed and os.getenv("WOW_V17_MLB_BRIDGE_SELF_ACCEPTANCE", "0") == "1":
            async def _run_after_startup():
                await asyncio.sleep(5.0)
                from v17_mlb_bridge_self_acceptance import run_mlb_event_bridge_self_acceptance
                await run_mlb_event_bridge_self_acceptance(
                    _MLB_BRIDGE_ACCEPTANCE_LOGGER,
                    event_api=getattr(getattr(market_api, "prod", None), "event_api", None),
                )

            task = asyncio.create_task(_run_after_startup())
            _RUNTIME_TASKS.add(task)
            task.add_done_callback(_RUNTIME_TASKS.discard)

    app.state.v17_mlb_event_bridge_deferred = True
    return True


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
    mlb_event_bridge_deferred = False
    if market_api is not None:
        numerical_ok = install_production_bridges(market_api=market_api, team_event_module=team_runtime)
        mlb_event_bridge_deferred = _defer_mlb_event_bridge_install(
            market_api=market_api,
            team_runtime=team_runtime,
        )

    return bool(
        prop_ok or lineup_ok or rehydration_ok or numerical_ok or mlb_event_bridge_deferred
        or getattr(market_api, "_v17_certified_numerical_bridge_installed", False)
        or getattr(market_api, "_v17_mlb_event_bridge_repair_installed", False)
    )


compose_active_runtime()


__all__ = [
    "build_llp_postmortem_learning_report",
    "compose_active_runtime",
    "evaluate_game_winner_cash_single",
    "get_certified_numerical_registry",
    "optimize_slip_portfolio",
]
