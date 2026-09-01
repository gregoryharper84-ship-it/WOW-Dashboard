"""Supabase-backed MLB 1IP final-refresh job.

Intended for a Render cron after merge/review. Reads only queued provisional
rows, refreshes official MLB evidence, and updates queue state. It never places
bets and never sets probability_publishable true.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from supabase import create_client

from mlb_1ip_final_refresh import process_pending

CAN_EXECUTE = False


def run_once() -> dict[str, int]:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    client = create_client(url, key)
    now = datetime.now(timezone.utc)

    response = (
        client.table("wow_mlb_1ip_refresh_queue")
        .select("*")
        .eq("status", "WAITING_FOR_OFFICIAL_LINEUP")
        .gt("event_start_time", now.isoformat())
        .order("event_start_time")
        .limit(50)
        .execute()
    )
    rows = list(response.data or [])
    results = process_pending(rows, now=now)

    counters = {"seen": len(rows), "waiting": 0, "ready": 0, "purged": 0, "expired": 0, "failed": 0}
    for result in results:
        queue_id = result.get("queue_id")
        status = result.get("status") or "FAILED"
        update = {
            "status": status,
            "updated_at": now.isoformat(),
            "last_refresh_at": now.isoformat(),
            "refresh_attempts": 1,
            "terminal_label": result.get("terminal_label"),
            "probability_publishable": False,
            "can_execute": False,
        }
        if result.get("refreshed_lineup_evidence"):
            update["refreshed_evidence"] = result["refreshed_lineup_evidence"]
        if status == "WAITING_FOR_OFFICIAL_LINEUP":
            counters["waiting"] += 1
        elif status == "READY_TO_RERUN":
            counters["ready"] += 1
        elif status == "SLATE_PURGE":
            counters["purged"] += 1
        elif status == "EXPIRED_PREGAME_WINDOW":
            counters["expired"] += 1
        else:
            counters["failed"] += 1
        client.table("wow_mlb_1ip_refresh_queue").update(update).eq("queue_id", queue_id).execute()

    return counters


if __name__ == "__main__":
    print(run_once())
