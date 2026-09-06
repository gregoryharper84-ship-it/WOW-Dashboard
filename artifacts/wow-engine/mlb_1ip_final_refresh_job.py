"""Supabase-backed MLB 1IP final-refresh job.

Intended for a Render cron only after merge/review. It refreshes official MLB
evidence, reruns the same certified empirical 1IP specialist when the official
lineup is confirmed, and persists queue state. Provider/runtime failures remain
refresh failures and are never relabeled MODEL_UNAVAILABLE. can_execute remains
false.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from supabase import create_client

from mlb_1ip_empirical_specialist import score_mlb_1ip_empirical
from mlb_1ip_final_refresh import refresh_queue_row

CAN_EXECUTE = False
REFRESH_DELAY_SECONDS = 300
STAT_TYPE = "1ST_INNING_PITCHES_THROWN"
FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"


def _resolve_artifact(client: Any) -> dict[str, Any]:
    """Resolve the exact certified 1IP artifact through the governed RPC."""
    response = client.rpc(
        "wow_prop_certified_model_artifact",
        {
            "p_sport": "MLB",
            "p_stat_type": STAT_TYPE,
            "p_feature_schema_version": FEATURE_SCHEMA_VERSION,
        },
    ).execute()
    payload = response.data
    if not isinstance(payload, dict):
        raise RuntimeError("PROP_MODEL_REGISTRY_INVALID_RESPONSE")
    if payload.get("ok") is not True or payload.get("code") != "PROP_CERTIFIED_MODEL_ARTIFACT_READY":
        raise RuntimeError(str(payload.get("code") or "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"))
    return payload


def _market_evidence_present(row: dict[str, Any]) -> bool:
    """Recover market evidence captured at initial ingress without a schema change."""
    status = str(row.get("money_lane_status") or "").strip().upper()
    if status not in {"", "PAYOUT_UNRESOLVED"}:
        return True

    provisional = row.get("provisional_evidence")
    if not isinstance(provisional, dict):
        return False
    market = provisional.get("_market_evidence")
    if not isinstance(market, dict):
        return False
    nested_status = str(market.get("money_lane_status") or "").strip().upper()
    if nested_status not in {"", "PAYOUT_UNRESOLVED"}:
        return True
    return any(
        isinstance(side, dict) and bool(side)
        for side in (market.get("market_side_a"), market.get("market_side_b"))
    )


def _rerun(
    row: dict[str, Any],
    evidence: dict[str, Any],
    artifact_record: dict[str, Any],
) -> dict[str, Any]:
    """Pure rerun helper: caller supplies the already-resolved artifact."""
    return score_mlb_1ip_empirical(
        artifact_record=artifact_record,
        starter_status=evidence.get("starter_status", ""),
        official_lineup_status=evidence.get("official_lineup_status", ""),
        projected_top_four=evidence.get("projected_top_four"),
        line_value=float(row["line"]),
        side=str(row["direction"]),
        failure_path_prior=evidence.get("failure_path_prior"),
        market_evidence_present=_market_evidence_present(row),
    )


def run_once(*, client: Any | None = None, now: datetime | None = None, hydrator: Callable[..., dict[str, Any]] | None = None) -> dict[str, int]:
    if client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        client = create_client(url, key)
    ts = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    response = (
        client.table("wow_mlb_1ip_refresh_queue")
        .select("*")
        .eq("status", "WAITING_FOR_OFFICIAL_LINEUP")
        .gt("event_start_time", ts.isoformat())
        .order("event_start_time")
        .limit(50)
        .execute()
    )
    rows = list(response.data or [])
    counters = {"seen": len(rows), "waiting": 0, "rerun_completed": 0, "purged": 0, "expired": 0, "failed": 0}

    # Resolve once per batch so every row in the refresh pass is scored by the
    # same immutable certified artifact. A registry failure is a refresh-layer
    # failure, not evidence that an already-computed sporting probability was
    # invalid or that unrelated lanes are unavailable.
    artifact_record: dict[str, Any] | None = None
    artifact_error: str | None = None
    if rows:
        try:
            artifact_record = _resolve_artifact(client)
        except Exception as exc:
            artifact_error = f"REFRESH_ARTIFACT_RESOLUTION_ERROR:{type(exc).__name__}:{exc}"

    for row in rows:
        queue_id = row.get("queue_id")
        attempts = int(row.get("refresh_attempts") or 0) + 1
        base_update: dict[str, Any] = {
            "updated_at": ts.isoformat(),
            "last_refresh_at": ts.isoformat(),
            "refresh_attempts": attempts,
            "probability_publishable": False,
            "can_execute": False,
        }
        try:
            kwargs = {"now": ts}
            if hydrator is not None:
                kwargs["hydrator"] = hydrator
            result = refresh_queue_row(row, **kwargs)
            status = str(result.get("status") or "FAILED")

            if status == "WAITING_FOR_OFFICIAL_LINEUP":
                base_update.update({
                    "status": status,
                    "next_refresh_at": (ts + timedelta(seconds=REFRESH_DELAY_SECONDS)).isoformat(),
                    "last_error_code": None,
                })
                counters["waiting"] += 1
            elif status == "READY_TO_RERUN":
                if artifact_record is None:
                    base_update.update({
                        "status": "FAILED",
                        "next_refresh_at": None,
                        "last_error_code": artifact_error or "REFRESH_ARTIFACT_RESOLUTION_ERROR",
                    })
                    counters["failed"] += 1
                else:
                    evidence = result["refreshed_lineup_evidence"]
                    rerun = _rerun(row, evidence, artifact_record)
                    base_update.update({
                        "status": "RERUN_COMPLETED",
                        "next_refresh_at": None,
                        "refreshed_evidence": evidence,
                        "rerun_result": rerun,
                        "rerun_completed_at": ts.isoformat(),
                        "terminal_label": rerun.get("terminal_label"),
                        "probability_publishable": bool(rerun.get("probability_publishable", False)),
                        "last_error_code": None,
                    })
                    counters["rerun_completed"] += 1
            elif status == "SLATE_PURGE":
                base_update.update({
                    "status": status,
                    "next_refresh_at": None,
                    "terminal_label": "SLATE_PURGE",
                    "last_error_code": None,
                })
                counters["purged"] += 1
            elif status == "EXPIRED_PREGAME_WINDOW":
                base_update.update({
                    "status": status,
                    "next_refresh_at": None,
                    "last_error_code": None,
                })
                counters["expired"] += 1
            else:
                base_update.update({"status": "FAILED", "next_refresh_at": None, "last_error_code": status})
                counters["failed"] += 1
        except Exception as exc:
            base_update.update({
                "status": "FAILED",
                "next_refresh_at": None,
                "last_error_code": f"REFRESH_RUNTIME_ERROR:{type(exc).__name__}",
            })
            counters["failed"] += 1

        client.table("wow_mlb_1ip_refresh_queue").update(base_update).eq("queue_id", queue_id).execute()

    return counters


if __name__ == "__main__":
    print(run_once())
