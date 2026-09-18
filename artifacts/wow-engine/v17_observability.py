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
    from v17.team_event_bridge_runtime import install_team_event_bridge_runtime

    install_team_event_bridge_runtime()

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
        pass

    try:
        import api_prod_market_acceptance as _accepted_base
        from v17.claude_runtime import install_claude_runtime_routes

        install_claude_runtime_routes(
            _accepted_base.app,
            auth_dependency=Depends(_accepted_base.market_api.prod._require_action_api_key),
        )
    except Exception:
        pass

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
