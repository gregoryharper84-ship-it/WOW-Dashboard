"""Sep-21 V17 orchestration-integrity repairs shared by WOW and LLP.

These repairs change orchestration/accounting only. They do not change model
weights, probabilities, calibration thresholds, ranking, terminal authority, or
execution posture.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

CAN_EXECUTE = False

_DAILY_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "wow_v17_sep21_daily_context", default=None
)


def install_daily_current_acquisition_repair() -> bool:
    """Acquire now, then separately account for existing canonical slate rows.

    The old flow could score persisted canonical rows while reporting zero current
    acquisition. A tempting repair is to keep only current producer receipts, but
    that would incorrectly erase valid NFL/WNBA/user-board rows because today's
    autonomous Daily producer is MLB-specific. Instead, force the producer once,
    keep the full requested-date canonical manifest, and explicitly partition the
    accounting into current-run receipt-backed rows vs preexisting canonical rows.
    """
    from v17 import daily_snapshot_runtime as daily
    from v17.prop_sport_parity import prop_sport_parity_summary

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
                # Force the existing governed producer once even when same-slate
                # canonical rows already exist. The second read returns the full
                # cross-sport canonical slate; nothing is silently discarded.
                return []
        return original_manifest(*args, **kwargs)

    def binding_aware_classify(payload: Any) -> str:
        if isinstance(payload, dict):
            mismatch = payload.get("evidence_handoff_schema_mismatch")
            if payload.get("run_validity_status") == "RUN_INVALID_EVIDENCE_BINDING":
                return "INVALID"
            if isinstance(mismatch, dict) and mismatch.get("status") == "FAIL":
                return "INVALID"
        return original_classify(payload)

    def repaired_run(req: Any, *, db: Any, market_api: Any, event_api: Any) -> dict[str, Any]:
        lanes = set(getattr(req, "lanes", []) or [])
        force = "PROPS" in lanes and int(getattr(req, "max_props", 0) or 0) > 0
        if not force:
            response = original_run(req, db=db, market_api=market_api, event_api=event_api)
            if "PROPS" in lanes and isinstance(response, dict):
                response["prop_sport_parity"] = prop_sport_parity_summary()
                response["can_execute"] = False
            return response

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
            handoff = lane.get("handoff_reconciliation") or {}
            canonical_rows = int(handoff.get("canonical_rows") or lane.get("manifest_count") or 0)
            current_rows = int(handoff.get("canonical_rows_from_current_acquisition") or 0)
            preexisting_rows = max(canonical_rows - current_rows, 0)

            # Current producer counts and canonical slate counts are separate
            # populations. Never call preexisting NFL/WNBA/user-board rows
            # "duplicates" of this producer run.
            lane["current_run_discovered_raw"] = counts["lane_discovered_raw"]
            lane["current_run_snapshot_write_succeeded"] = counts["lane_snapshot_write_succeeded"]
            lane["current_run_snapshot_write_failed"] = counts["lane_snapshot_write_failed"]
            lane["current_run_persisted_candidates"] = counts["persisted_candidates"]
            lane["current_acquisition_receipt_count"] = len(
                daily._receipt_snapshot_ids(acquisition)
            )
            lane["current_acquisition_canonical_rows"] = current_rows
            lane["preexisting_canonical_slate_rows"] = preexisting_rows
            lane["canonical_rows_total"] = canonical_rows
            lane["acquisition_accounting_scope"] = (
                "CURRENT_RUN_PRODUCER_AND_PREEXISTING_CANONICAL_SLATE_SEPARATED"
            )
            lane["preexisting_canonical_rows_included"] = preexisting_rows > 0
            lane["historical_manifest_substitution"] = False

        response["current_acquisition_required"] = True
        response["historical_manifest_substitution"] = False
        response["prop_sport_parity"] = prop_sport_parity_summary()
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
    """Stop repeatedly charging an exhausted Rundown provider during one scan."""
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


def install_sep21_orchestration_integrity_repairs() -> dict[str, bool]:
    return {
        "daily_current_acquisition": install_daily_current_acquisition_repair(),
        "rundown_quota_circuit": install_rundown_quota_circuit_repair(),
    }


__all__ = [
    "install_daily_current_acquisition_repair",
    "install_rundown_quota_circuit_repair",
    "install_sep21_orchestration_integrity_repairs",
]
