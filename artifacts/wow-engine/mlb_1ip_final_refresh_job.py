"""Supabase-backed MLB 1IP final-refresh job.

Intended for a Render cron only after merge/review. It refreshes official MLB
evidence, performs the same 1IP specialist rerun when the official lineup is
confirmed, and persists queue state. Provider/runtime failures remain refresh
failures and are never relabeled MODEL_UNAVAILABLE. can_execute remains false.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from supabase import create_client

from mlb_1ip_final_refresh import refresh_queue_row
from mlb_1ip_specialist import score_mlb_1ip

CAN_EXECUTE = False
REFRESH_DELAY_SECONDS = 300


def _rerun(row: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    money_lane_status = str(row.get("money_lane_status") or "PAYOUT_UNRESOLVED").upper()
    return score_mlb_1ip(
        starter_status=evidence.get("starter_status", ""),
        official_lineup_status=evidence.get("official_lineup_status", ""),
        projected_top_four=evidence.get("projected_top_four"),
        pitcher_bf_distribution=evidence.get("pitcher_bf_distribution") or {},
        baseline_pitches_per_batter=evidence.get("baseline_pitches_per_batter") or {},
        line_value=float(row["line"]),
        side=str(row["direction"]),
        failure_path_prior=evidence.get("failure_path_prior"),
        market_evidence_present=money_lane_status not in {"", "PAYOUT_UNRESOLVED"},
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
                evidence = result["refreshed_lineup_evidence"]
                rerun = _rerun(row, evidence)
                base_update.update({
                    "status": "RERUN_COMPLETED",
                    "next_refresh_at": None,
                    "refreshed_evidence": evidence,
                    "rerun_result": rerun,
                    "rerun_completed_at": ts.isoformat(),
                    "terminal_label": rerun.get("terminal_label"),
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
            # Infrastructure/acquisition failures are not probability-model
            # availability claims. Keep the diagnostic at the refresh layer.
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
