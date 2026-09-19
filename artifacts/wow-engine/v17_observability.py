"""Optional V17 production observability with strict safety defaults.

Telemetry is disabled when SENTRY_DSN is absent. Enabling it never changes
scoring, model selection, terminal labels, or execution authority.

The accepted production entrypoint calls this initializer once before routes are
served. V17 bridge registration/health is bootstrapped before the optional
telemetry branch, while the public observability return contract stays unchanged.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import Depends

from v17.action_invocation_telemetry import install_action_invocation_middleware
from v17.interactive_latency_telemetry import install_interactive_latency_middleware
from v17.interactive_pick_hydration import schedule_interactive_pick_hydration_install


def initialize_observability() -> dict[str, Any]:
    # Runtime bridge registration is not telemetry. It is intentionally done
    # before the optional Sentry branch so /health and /score-team-event expose
    # the same authoritative production registry even when Sentry is disabled.
    from v17.team_event_bridge_runtime import install_team_event_bridge_runtime
    from v17.universal_team_event_governance import install_universal_team_event_governance

    install_team_event_bridge_runtime()
    install_universal_team_event_governance()

    # Install non-secret total-wall-time telemetry, certification-independent
    # Action invocation receipts, and schedule the bounded external pre-hydration
    # wrapper on the accepted production FastAPI app. Invocation telemetry is
    # fail-open and never participates in certification or scoring authority.
    # The hydration wrapper installs at startup, after api_ncaaf_acceptance has
    # composed all routes, and delegates validation/persistence/scoring/
    # reconciliation back to the captured canonical endpoint.
    try:
        import api_prod_market_acceptance as _accepted_base

        install_interactive_latency_middleware(_accepted_base.app)
        install_action_invocation_middleware(
            _accepted_base.app,
            db_client_fn=_accepted_base.market_api.prod.get_client,
        )
        schedule_interactive_pick_hydration_install(
            _accepted_base.app,
            market_api=_accepted_base.market_api,
        )
    except Exception:
        # Observability/latency optimization must never make the governed API
        # unavailable; canonical route behavior remains intact on any failure.
        pass

    # Mount the Render-hosted Claude support runtime on the same accepted app.
    # This is intentionally advisory-only: it has no fitted-model authority,
    # probability-publication authority, terminal authority, or wager execution.
    # Installation failure is isolated so Anthropic availability can never make
    # the governed sports API unavailable.
    try:
        import api_prod_market_acceptance as _accepted_base
        from v17.claude_runtime import install_claude_runtime_routes

        install_claude_runtime_routes(
            _accepted_base.app,
            auth_dependency=Depends(_accepted_base.market_api.prod._require_action_api_key),
        )
    except Exception:
        pass

    # The research evaluation is off by default and independent of telemetry.
    # It is scheduled here because this initializer runs once in the accepted
    # production entrypoint before serving starts. Any research-runner defect is
    # isolated from API startup and can never alter probability publication.
    try:
        from v17.mlb_game_winner_shadow_one_shot import schedule_if_enabled

        schedule_if_enabled()
    except Exception:
        pass

    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return {
            "status": "DISABLED_NOT_CONFIGURED",
            "provider": "SENTRY",
            "can_execute": False,
        }

    import sentry_sdk

    traces_sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05"))
    traces_sample_rate = min(max(traces_sample_rate, 0.0), 1.0)
    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("WOW_ENVIRONMENT", "production"),
        release=os.getenv("RENDER_GIT_COMMIT") or os.getenv("WOW_RELEASE_SHA"),
        traces_sample_rate=traces_sample_rate,
        send_default_pii=False,
        max_request_body_size="never",
    )
    sentry_sdk.set_tag("wow.generation", "V17")
    sentry_sdk.set_tag("wow.can_execute", "false")
    return {
        "status": "ENABLED",
        "provider": "SENTRY",
        "traces_sample_rate": traces_sample_rate,
        "can_execute": False,
    }