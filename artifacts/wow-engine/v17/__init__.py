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
        from v17.sep15_runtime_contract_repairs import install_post_mlb_bridge_repairs

        installed = install_mlb_event_bridge_repair(
            market_api=market_api,
            team_event_module=team_runtime,
        )
        post_repair_installed = False
        if installed:
            post_repair_installed = install_post_mlb_bridge_repairs(
                market_api=market_api,
                team_runtime=team_runtime,
            )

        if installed:
            from v17.team_event_bridge_runtime import _install_health_overlay

            _install_health_overlay()

        preservation = sys.modules.get("v17.team_event_probability_preservation")
        if installed and preservation is not None:
            preservation._original_run_mlb_llp_governance = team_runtime._run_mlb_llp_governance

        handoff_rank_fix_installed = False
        if installed and preservation is not None:
            from v17.sep16_evidence_handoff_rank_fix import install_evidence_handoff_rank_fix

            handoff_rank_fix_installed = install_evidence_handoff_rank_fix(
                preservation=preservation,
            )

        publication_chain_repair_installed = False
        if installed and preservation is not None:
            from v17.sep17_team_event_publication_chain_repair import (
                install_team_event_publication_chain_repair,
            )

            publication_chain_repair_installed = install_team_event_publication_chain_repair(
                preservation=preservation,
                team_runtime=team_runtime,
            )

        if installed and not post_repair_installed:
            _MLB_BRIDGE_ACCEPTANCE_LOGGER.error(
                "V17_SEP15_MLB_POST_BRIDGE_REPAIR=FAIL can_execute=false"
            )
        if installed and preservation is not None and not handoff_rank_fix_installed:
            _MLB_BRIDGE_ACCEPTANCE_LOGGER.error(
                "V17_SEP16_EVIDENCE_HANDOFF_RANK_FIX=FAIL can_execute=false"
            )
        if installed and preservation is not None and not publication_chain_repair_installed:
            _MLB_BRIDGE_ACCEPTANCE_LOGGER.error(
                "V17_SEP17_PUBLICATION_CHAIN_REPAIR=FAIL can_execute=false"
            )

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
    from v17.daily_snapshot_oidc_bridge import install_daily_snapshot_oidc_bridge
    from v17.fallback_provider_routes import install_fallback_provider_health_routes
    from v17.full_board_overlay import install_cross_sport_full_board_overlay
    from v17.full_board_runtime import install_full_board_runtime_routes
    from v17.prop_response_semantics import install_prop_response_semantics
    from v17.projected_lineup_scenario_modeling import install_projected_lineup_semantics
    from v17.projected_lineup_probability_rehydration import install_projected_lineup_score_rehydration
    from v17.numerical_engine_production_bridge import install_production_bridges
    from v17.llp_rundown_market_bridge import install_llp_rundown_market_bridge
    from v17.llp_rundown_value_shadow import install_llp_rundown_value_shadow
    from v17.rundown_credential_diagnostic import log_rundown_credential_status
    from v17.rundown_market_startup_bootstrap import install_rundown_market_startup_bootstrap
    from v17.runtime_acceptance_probe import install_runtime_acceptance_probe
    from v17.sep15_runtime_contract_repairs import (
        install_market_prior_ingress_repair,
        install_rundown_v2_auth_repair,
    )
    from v17 import team_event_request_runtime as team_runtime

    rundown_auth_ok = install_rundown_v2_auth_repair()
    market_prior_ok = install_market_prior_ingress_repair(team_runtime)

    log_rundown_credential_status(logging.getLogger("uvicorn.error"))

    get_certified_numerical_registry()
    prop_ok = install_prop_response_semantics()
    lineup_ok = install_projected_lineup_semantics()
    rehydration_ok = install_projected_lineup_score_rehydration(team_runtime)
    rundown_llp_ok = install_llp_rundown_market_bridge(team_runtime)
    rundown_value_shadow_ok = install_llp_rundown_value_shadow(team_runtime)
    full_board_overlay_ok = install_cross_sport_full_board_overlay()

    market_api = sys.modules.get("api_prod_market")
    numerical_ok = False
    mlb_event_bridge_deferred = False
    runtime_acceptance_ok = False
    daily_snapshot_oidc_ok = False
    daily_async_ok = False
    full_board_runtime_ok = False
    fallback_provider_health_ok = False
    rundown_market_bootstrap_ok = False
    if market_api is not None:
        numerical_ok = install_production_bridges(market_api=market_api, team_event_module=team_runtime)
        mlb_event_bridge_deferred = _defer_mlb_event_bridge_install(
            market_api=market_api,
            team_runtime=team_runtime,
        )
        app = getattr(market_api, "app", None)
        if app is not None:
            runtime_acceptance_ok = install_runtime_acceptance_probe(
                app=app,
                market_api=market_api,
                team_runtime=team_runtime,
            )
            daily_snapshot_oidc_ok = install_daily_snapshot_oidc_bridge(
                app=app,
                market_api=market_api,
            )
            prod = getattr(market_api, "prod", None)
            auth_dependency = getattr(prod, "_require_action_api_key", None)
            db_client_fn = getattr(prod, "get_client", None)
            event_api = getattr(prod, "event_api", None)
            rundown_market_bootstrap_ok = install_rundown_market_startup_bootstrap(
                app,
                db_client_fn=db_client_fn,
            )
            if callable(auth_dependency) and callable(db_client_fn) and event_api is not None:
                from v17.daily_async_runtime import install_daily_async_routes

                daily_async_ok = install_daily_async_routes(
                    app,
                    auth_callable=auth_dependency,
                    db_client_fn=db_client_fn,
                    market_api=market_api,
                    event_api=event_api,
                )
            full_board_runtime_ok = install_full_board_runtime_routes(
                app,
                auth_dependency=auth_dependency,
            )
            fallback_provider_health_ok = install_fallback_provider_health_routes(
                app,
                auth_dependency=auth_dependency,
            )

    return bool(
        rundown_auth_ok or market_prior_ok
        or prop_ok or lineup_ok or rehydration_ok or rundown_llp_ok or rundown_value_shadow_ok or numerical_ok
        or full_board_overlay_ok
        or mlb_event_bridge_deferred or runtime_acceptance_ok or daily_snapshot_oidc_ok
        or daily_async_ok or rundown_market_bootstrap_ok
        or full_board_runtime_ok or fallback_provider_health_ok
        or getattr(market_api, "_v17_certified_numerical_bridge_installed", False)
        or getattr(market_api, "_v17_mlb_event_bridge_repair_installed", False)
        or getattr(team_runtime, "_v17_llp_rundown_market_bridge_installed", False)
        or getattr(team_runtime, "_v17_llp_rundown_value_shadow_installed", False)
        or getattr(team_runtime, "_v17_sep15_market_prior_ingress_repair_installed", False)
    )


compose_active_runtime()


__all__ = [
    "build_llp_postmortem_learning_report",
    "compose_active_runtime",
    "evaluate_game_winner_cash_single",
    "get_certified_numerical_registry",
    "optimize_slip_portfolio",
]
