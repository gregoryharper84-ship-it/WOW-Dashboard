"""Sep-21 V17 orchestration-integrity repairs.

This module fixes orchestration/reporting defects only. It does not change model
weights, calibrated probabilities, qualification thresholds, rank ordering, or
terminal authority.

Repairs:
1. Daily PROPS always performs a current acquisition before scoring and restricts
   the current run to receipt-backed rows from that acquisition. Historical
   canonical rows remain in storage but cannot masquerade as current acquisition.
2. Evidence handoff contradictions classify as RUN_INVALID_EVIDENCE_BINDING for
   Daily terminal reduction rather than an ordinary no-pick/hold.
3. TheRundown discovery caches successful sport reads and opens a per-scan quota
   circuit after a quota/rate-limit response so one exhausted provider is not hit
   repeatedly for every sport family.
4. /governance reports route/capability publication readiness from live governed
   state instead of the stale static global false flag. This is diagnostic only;
   per-row rank eligibility remains owned by V17_TERMINAL_REDUCER.
5. Render-to-Render Scout acquisition may use a separate internal odds-proxy
   credential without rotating the existing Custom GPT/Action credential.

can_execute remains false throughout.
"""
from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Any

CAN_EXECUTE = False
RUN_INVALID_EVIDENCE_BINDING = "RUN_INVALID_EVIDENCE_BINDING"

_DAILY_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "wow_v17_sep21_daily_context", default=None
)


def install_internal_proxy_client_auth() -> bool:
    """Expose only the dedicated internal key to existing read-only Scout client code."""
    internal = os.environ.get("WOW_ODDS_PROXY_INTERNAL_KEY")
    if not internal:
        return False
    # nightly_multiscout.proxy_get reads WOW_ODDS_PROXY_ACTION_KEY dynamically.
    # Populate it only when the existing client credential is absent, so no
    # configured Action key is replaced or logged.
    if not os.environ.get("WOW_ODDS_PROXY_ACTION_KEY"):
        os.environ["WOW_ODDS_PROXY_ACTION_KEY"] = internal
    return True


def install_daily_current_acquisition_repair() -> bool:
    from v17 import daily_snapshot_runtime as daily

    if getattr(daily, "_v17_sep21_current_acquisition_repair_installed", False):
        return True

    original_run = daily.run_daily_snapshot
    original_manifest = daily._prop_manifest_rows
    original_acquire = daily.acquire_daily_prop_snapshots
    original_classify = daily.classify_stage_status

    def tracked_acquire(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original_acquire(*args, **kwargs)
        context = _DAILY_CONTEXT.get()
        if context is not None:
            context["acquisition"] = result
        return result

    def current_manifest(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        context = _DAILY_CONTEXT.get()
        if context is not None and context.get("force_current_acquisition"):
            calls = int(context.get("manifest_calls") or 0)
            context["manifest_calls"] = calls + 1
            if calls == 0:
                # Force the existing governed producer path to run once. This is
                # not an empty-lane claim; it is current-run acquisition policy.
                return []

        rows = original_manifest(*args, **kwargs)
        context = _DAILY_CONTEXT.get()
        if context is None or not context.get("force_current_acquisition"):
            return rows

        acquisition = context.get("acquisition")
        if not isinstance(acquisition, dict):
            return []
        current_ids = daily._receipt_snapshot_ids(acquisition)
        if not current_ids:
            # A current acquisition with no successful write receipts cannot be
            # backfilled silently from yesterday's persisted manifest.
            return []
        return [
            row for row in rows
            if str(row.get("source_snapshot_id") or "") in current_ids
        ]

    def binding_aware_classify(payload: Any) -> str:
        if isinstance(payload, dict):
            mismatch = payload.get("evidence_handoff_schema_mismatch")
            if payload.get("run_validity_status") == RUN_INVALID_EVIDENCE_BINDING:
                return "INVALID"
            if isinstance(mismatch, dict) and mismatch.get("status") == "FAIL":
                return "INVALID"
        return original_classify(payload)

    def repaired_run(req: Any, *, db: Any, market_api: Any, event_api: Any) -> dict[str, Any]:
        lanes = set(getattr(req, "lanes", []) or [])
        force = "PROPS" in lanes and int(getattr(req, "max_props", 0) or 0) > 0
        if not force:
            return original_run(req, db=db, market_api=market_api, event_api=event_api)

        requested_mode = str(getattr(req, "response_mode", "COMPACT") or "COMPACT").upper()
        full_req = req.model_copy(update={"response_mode": "FULL"})
        context = {
            "force_current_acquisition": True,
            "manifest_calls": 0,
            "acquisition": None,
        }
        token = _DAILY_CONTEXT.set(context)
        try:
            response = original_run(full_req, db=db, market_api=market_api, event_api=event_api)
        finally:
            _DAILY_CONTEXT.reset(token)

        acquisition = response.get("prop_acquisition")
        lane = (response.get("lane_reconciliation") or {}).get("PROPS")
        if isinstance(acquisition, dict) and isinstance(lane, dict):
            counts = daily._acquisition_counts(acquisition)
            lane["discovered_count"] = counts["lane_discovered_raw"]
            lane["duplicate_source_instance_count"] = int(
                acquisition.get("duplicate_source_instances") or 0
            )
            lane["acquisition_accounting_scope"] = "CURRENT_RUN_ONLY"
            lane["historical_manifest_substitution"] = False
            lane["current_acquisition_receipt_count"] = len(
                daily._receipt_snapshot_ids(acquisition)
            )

        response["current_acquisition_required"] = True
        response["historical_manifest_substitution"] = False
        response["can_execute"] = False
        if requested_mode == "FULL":
            response["response_mode"] = "FULL"
            return response
        detail = response.get("row_detail_persistence") or {}
        return daily.compact_response(
            response,
            detail_available=bool(detail.get("detail_available")),
        )

    daily.acquire_daily_prop_snapshots = tracked_acquire
    daily._prop_manifest_rows = current_manifest
    daily.classify_stage_status = binding_aware_classify
    daily.run_daily_snapshot = repaired_run
    daily._v17_sep21_current_acquisition_repair_installed = True
    return True


def install_rundown_quota_circuit_repair() -> bool:
    from v17 import cross_sport_discovery_feed as feed
    from v17 import cross_sport_winner_discovery as discovery

    if getattr(feed, "_v17_sep21_rundown_quota_circuit_installed", False):
        return True
    original_factory = feed.rundown_board_feed

    def repaired_factory(*args: Any, **kwargs: Any):
        base_fetch = original_factory(*args, **kwargs)
        cache: dict[str, list[Any]] = {}
        quota_blocker: list[str] = []

        def fetch(family: str, target: Any = None):
            if quota_blocker:
                raise discovery.DiscoveryFeedError(quota_blocker[0])
            key = str(getattr(target, "sport_id", None) or family)
            if key in cache:
                return list(cache[key])
            try:
                rows = list(base_fetch(family, target) or ())
            except discovery.DiscoveryFeedError as exc:
                code = str(exc.code or "RUNDOWN_DISCOVERY_FAILED")
                upper = code.upper()
                if "QUOTA" in upper or "RATE_LIMIT" in upper or "429" in upper:
                    quota_blocker.append(code)
                raise
            cache[key] = rows
            return list(rows)

        return fetch

    feed.rundown_board_feed = repaired_factory
    feed._v17_sep21_rundown_quota_circuit_installed = True
    return True


def install_governance_publication_diagnostics_repair(*, market_api: Any) -> bool:
    """Replace only the stale diagnostics route; never promote an event row."""
    if getattr(market_api, "_v17_sep21_governance_diagnostics_installed", False):
        return True
    app = getattr(market_api, "app", None)
    prod = getattr(market_api, "prod", None)
    if app is None or prod is None or not callable(getattr(prod, "governance", None)):
        return False

    existing = [
        route for route in list(app.router.routes)
        if getattr(route, "path", None) == "/governance"
        and "GET" in (getattr(route, "methods", set()) or set())
    ]
    for route in existing:
        app.router.routes.remove(route)

    @app.get("/governance", operation_id="getWowV17Governance")
    def governed_publication_diagnostics():
        payload = dict(prod.governance())
        global_state = prod.base_api._query_deployment_gate_state() or {}
        global_publishable = bool(
            global_state.get("governed_probability_capability") == "AVAILABLE"
            and global_state.get("probability_publishable") is True
        )
        payload["probability_publishable"] = global_publishable
        payload["probability_publishable_scope"] = "CAPABILITY_DIAGNOSTIC_NOT_ROW_APPROVAL"

        lanes = payload.get("lane_capabilities")
        if isinstance(lanes, dict):
            event_lane = lanes.get(prod.MLB_EVENT_CAPABILITY_KEY)
            if isinstance(event_lane, dict):
                evidence = event_lane.get("evidence") or {}
                event_lane["probability_publishable"] = bool(
                    global_publishable
                    and event_lane.get("status") == "AVAILABLE"
                    and evidence.get("model_probability_publishable") is True
                )
                event_lane["publication_flag_source"] = (
                    "LIVE_GOVERNED_DEPLOYMENT_PLUS_ROUTE_MODEL_CAPABILITY"
                )
            prop_lane = lanes.get(prod.PROP_CAPABILITY_KEY)
            if isinstance(prop_lane, dict):
                evidence = prop_lane.get("evidence") or {}
                # Prop capability remains scope-specific. Do not promote the
                # whole lane merely because some fitted routes can score.
                prop_lane["probability_publishable"] = bool(
                    prop_lane.get("status") == "AVAILABLE"
                    and evidence.get("probability_publishable") is True
                )
                prop_lane["publication_flag_source"] = "PROP_ROUTE_LEDGER"

        payload["can_execute"] = False
        return payload

    market_api._v17_sep21_governance_diagnostics_installed = True
    return True


def install_sep21_orchestration_integrity_repairs(*, market_api: Any | None = None) -> dict[str, bool]:
    result = {
        "internal_proxy_client_auth": install_internal_proxy_client_auth(),
        "daily_current_acquisition": install_daily_current_acquisition_repair(),
        "rundown_quota_circuit": install_rundown_quota_circuit_repair(),
        "governance_publication_diagnostics": False,
    }
    if market_api is not None:
        result["governance_publication_diagnostics"] = (
            install_governance_publication_diagnostics_repair(market_api=market_api)
        )
    return result


__all__ = [
    "RUN_INVALID_EVIDENCE_BINDING",
    "install_daily_current_acquisition_repair",
    "install_governance_publication_diagnostics_repair",
    "install_internal_proxy_client_auth",
    "install_rundown_quota_circuit_repair",
    "install_sep21_orchestration_integrity_repairs",
]
