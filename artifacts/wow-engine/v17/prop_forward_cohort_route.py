"""Authenticated V17 routes and scheduler installer for prop forward evidence."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import FastAPI

import v17.prop_forward_cohort_thesis_dedupe  # installs statistical-independence guard
import v17.fantasy_score_forward_cohort_thesis_dedupe as fantasy_thesis_dedupe
from v17.fantasy_score_forward_cohort_route import install_fantasy_score_forward_cohort_route
from v17.phase_a_row_publication import install_phase_a_row_publication
from v17.prop_action_canary_capture import install_prop_action_canary_capture
from v17.prop_certification_runtime import PropCertificationAuditRequest, run_prop_certification_audit
from v17.prop_exact_route_settlement import ExactRouteSettlementRequest, run_exact_route_settlement
from v17.prop_forward_cohort_market_adapter import ForwardCohortMarketAdapter
from v17.prop_forward_cohort_runtime import PropForwardCohortRequest, run_prop_forward_cohort
from v17.prop_forward_cohort_scheduler import run_prop_forward_cohort_loop
from v17.prop_lifecycle_autopilot_runtime import install_prop_lifecycle_autopilot
from v17.prop_production_registration import (
    PropProductionRegistrationAuditRequest,
    run_prop_production_registration_audit,
)
from v17.prop_universal_forward_evidence import (
    UniversalPropForwardEvidenceRequest,
    run_universal_prop_forward_evidence,
)
import v17.prop_universal_forward_schema_repair  # align with live Supabase snapshot schema


_LOGGER = logging.getLogger("wow.v17.prop_forward_cohort")
_STATE_KEY = "wow_prop_forward_cohort_scheduler_installed"


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _install_scheduler(app: FastAPI, *, db_client_fn: Any, market_api: Any) -> None:
    if os.getenv("WOW_PROP_FORWARD_COHORT_ENABLED", "0") != "1":
        return
    if getattr(app.state, _STATE_KEY, False):
        return
    setattr(app.state, _STATE_KEY, True)

    interval_seconds = _int_env(
        "WOW_PROP_FORWARD_COHORT_INTERVAL_SECONDS", 900, minimum=60, maximum=86400
    )
    max_snapshots = _int_env(
        "WOW_PROP_FORWARD_COHORT_MAX_SNAPSHOTS", 100, minimum=1, maximum=200
    )
    initial_delay_seconds = _int_env(
        "WOW_PROP_FORWARD_COHORT_INITIAL_DELAY_SECONDS", 5, minimum=0, maximum=300
    )

    @app.on_event("startup")
    async def schedule_prop_forward_cohort() -> None:
        task = asyncio.create_task(
            run_prop_forward_cohort_loop(
                db_client_fn=db_client_fn,
                market_api=market_api,
                logger=_LOGGER,
                interval_seconds=interval_seconds,
                max_snapshots=max_snapshots,
                initial_delay_seconds=initial_delay_seconds,
            )
        )
        tasks = getattr(app.state, "wow_prop_forward_cohort_tasks", None)
        if tasks is None:
            tasks = set()
            app.state.wow_prop_forward_cohort_tasks = tasks
        tasks.add(task)
        task.add_done_callback(tasks.discard)


def install_prop_forward_cohort_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
    market_api: Any,
) -> None:
    install_phase_a_row_publication(
        app,
        auth_dependency=auth_dependency,
        market_api=market_api,
    )

    install_fantasy_score_forward_cohort_route(
        app,
        auth_dependency=auth_dependency,
        db_client_fn=db_client_fn,
        market_api=market_api,
    )
    fantasy_thesis_dedupe.install()

    cohort_market_api = ForwardCohortMarketAdapter(market_api)
    _install_scheduler(app, db_client_fn=db_client_fn, market_api=cohort_market_api)

    # Universal lifecycle autopilot is distinct from the legacy MLB strikeout
    # cohort loop. It continuously runs every declared exact route through
    # forward capture, exact settlement, certification-readiness and production
    # registration audit, then persists route/artifact health. It cannot certify
    # or promote an artifact by itself.
    install_prop_lifecycle_autopilot(
        app,
        auth_dependency=auth_dependency,
        db_client_fn=db_client_fn,
        market_api=cohort_market_api,
    )

    # This wraps the already-composed canonical HTTP Action boundary. Internal
    # model calls do not pass through it, so a persisted canary is evidence of a
    # real canonical endpoint invocation. It remains inert until a reviewed exact
    # certification release exists.
    install_prop_action_canary_capture(app, db_client_fn=db_client_fn)

    if not any(
        getattr(route, "path", None) == "/v17/prop-forward-cohort-run"
        for route in app.router.routes
    ):
        @app.post(
            "/v17/prop-forward-cohort-run",
            dependencies=[auth_dependency],
            operation_id="runWowV17PropForwardCohort",
        )
        def prop_forward_cohort_run(req: PropForwardCohortRequest):
            return run_prop_forward_cohort(req, db=db_client_fn(), market_api=cohort_market_api)

    if not any(
        getattr(route, "path", None) == "/v17/prop-forward-evidence-run"
        for route in app.router.routes
    ):
        @app.post(
            "/v17/prop-forward-evidence-run",
            dependencies=[auth_dependency],
            operation_id="runWowV17UniversalPropForwardEvidence",
        )
        def prop_forward_evidence_run(req: UniversalPropForwardEvidenceRequest):
            return run_universal_prop_forward_evidence(
                req,
                db=db_client_fn(),
                market_api=cohort_market_api,
            )

    # Settlement is a separate authenticated stage. It can only write outcomes
    # for exact routes with an explicit official-stat adapter; unsupported and
    # separate-contract routes stay typed/blocked and never borrow a proxy stat.
    if not any(
        getattr(route, "path", None) == "/v17/prop-forward-settlement-run"
        for route in app.router.routes
    ):
        @app.post(
            "/v17/prop-forward-settlement-run",
            dependencies=[auth_dependency],
            operation_id="runWowV17ExactRoutePropSettlement",
        )
        def prop_forward_settlement_run(req: ExactRouteSettlementRequest):
            return run_exact_route_settlement(req, db=db_client_fn())

    # #492 control plane: descriptive forward metrics + exact reviewed-policy
    # certification. It never fits, certifies by declaration, or promotes.
    if not any(
        getattr(route, "path", None) == "/v17/prop-calibration-certification-audit"
        for route in app.router.routes
    ):
        @app.post(
            "/v17/prop-calibration-certification-audit",
            dependencies=[auth_dependency],
            operation_id="auditWowV17PropCalibrationCertification",
        )
        def prop_calibration_certification_audit(req: PropCertificationAuditRequest):
            return run_prop_certification_audit(req, db=db_client_fn())

    # #493 control plane: binds certification to registry/runtime/hydration and
    # an immutable real Action receipt. No synthetic receipt can satisfy it.
    if not any(
        getattr(route, "path", None) == "/v17/prop-production-registration-audit"
        for route in app.router.routes
    ):
        @app.post(
            "/v17/prop-production-registration-audit",
            dependencies=[auth_dependency],
            operation_id="auditWowV17PropProductionRegistration",
        )
        def prop_production_registration_audit(req: PropProductionRegistrationAuditRequest):
            return run_prop_production_registration_audit(req, db=db_client_fn())
