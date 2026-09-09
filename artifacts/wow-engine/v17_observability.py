"""Optional V17 production observability with strict safety defaults.

Telemetry is disabled when SENTRY_DSN is absent. Enabling it never changes
scoring, model selection, terminal labels, or execution authority.

The accepted production entrypoint calls this initializer once before routes are
served.  V17 bridge registration/health is therefore bootstrapped here as an
independent fail-closed runtime control; telemetry remains optional.
"""
from __future__ import annotations

import os
from typing import Any


def initialize_observability() -> dict[str, Any]:
    # Runtime bridge registration is not telemetry.  It is intentionally done
    # before the optional Sentry branch so /health and /score-team-event expose
    # the same authoritative production registry even when Sentry is disabled.
    from v17.team_event_bridge_runtime import install_team_event_bridge_runtime

    bridge_runtime = install_team_event_bridge_runtime()

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
            "team_event_bridge_runtime": bridge_runtime,
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
        "team_event_bridge_runtime": bridge_runtime,
        "can_execute": False,
    }
