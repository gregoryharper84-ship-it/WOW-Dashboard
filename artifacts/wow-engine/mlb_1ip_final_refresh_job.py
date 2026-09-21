"""Supabase-backed MLB 1IP final-refresh job.

Intended for a Render cron only after merge/review. It refreshes official MLB
evidence, reruns the same certified empirical 1IP specialist when the official
lineup is confirmed, and persists queue state. Provider/runtime failures remain
refresh failures and are never relabeled MODEL_UNAVAILABLE. can_execute remains
false.

Every queued row must terminate exactly once. Two reliability paths keep that
true without touching probability or calibration behaviour:

* rows whose ``event_start_time`` has already passed are still selected, so the
  state machine's ``EXPIRED_PREGAME_WINDOW`` disposition actually fires instead
  of leaving the row waiting forever for a lineup that can no longer be
  confirmed;
* runtime/provider failures get a bounded exponential-backoff retry while the
  pregame window is still open, and are dead-lettered with an explicit alert
  (typed error code preserved) once the retry budget is spent.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from supabase import create_client

from mlb_1ip_empirical_specialist import score_mlb_1ip_empirical
from mlb_1ip_final_refresh import refresh_queue_row

CAN_EXECUTE = False
REFRESH_DELAY_SECONDS = 300
MAX_REFRESH_ATTEMPTS = 8
RETRY_BACKOFF_BASE_SECONDS = 60
RETRY_BACKOFF_MAX_SECONDS = 1800
DEAD_LETTER_SAMPLE_LIMIT = 20
STAT_TYPE = "1ST_INNING_PITCHES_THROWN"
FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"

LOGGER = logging.getLogger(__name__)


def _pg_timestamp(ts: datetime) -> str:
    """PostgREST-safe UTC timestamp (avoids a literal '+' in filter values)."""
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _aware(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def _failure_code(exc: Exception) -> str:
    """Typed refresh-failure code; never collapses into MODEL_UNAVAILABLE."""
    code = getattr(exc, "code", None)
    suffix = f":{code}" if isinstance(code, str) and code.strip() else ""
    return f"REFRESH_RUNTIME_ERROR:{type(exc).__name__}{suffix}"


def _retry_backoff_seconds(attempts: int) -> int:
    exponent = max(0, min(int(attempts) - 1, 10))
    return min(RETRY_BACKOFF_MAX_SECONDS, RETRY_BACKOFF_BASE_SECONDS * (2 ** exponent))


def _pregame_window_open(row: dict[str, Any], ts: datetime) -> bool:
    try:
        return ts < _aware(row.get("event_start_time"))
    except Exception:
        return False


def _apply_failure(
    base_update: dict[str, Any],
    *,
    row: dict[str, Any],
    attempts: int,
    ts: datetime,
    code: str,
) -> bool:
    """Schedule a bounded retry, or dead-letter the row. Returns True on retry.

    A retry keeps the row in WAITING_FOR_OFFICIAL_LINEUP (the lineup is still
    unknown, which is exactly what that status means) with a backed-off
    next_refresh_at, so the pending-row query picks it up again. The typed
    failure code is preserved either way.
    """
    if attempts < MAX_REFRESH_ATTEMPTS and _pregame_window_open(row, ts):
        delay = _retry_backoff_seconds(attempts)
        base_update.update({
            "status": "WAITING_FOR_OFFICIAL_LINEUP",
            "next_refresh_at": (ts + timedelta(seconds=delay)).isoformat(),
            "last_error_code": code,
        })
        LOGGER.warning(
            "WOW_MLB_1IP_REFRESH_RETRY queue_id=%s event_id=%s player=%s attempts=%s "
            "retry_in_seconds=%s error_code=%s probability_publishable=false can_execute=false",
            row.get("queue_id"), row.get("event_id"), row.get("player"), attempts, delay, code,
        )
        return True

    base_update.update({
        "status": "FAILED",
        "next_refresh_at": None,
        "last_error_code": f"REFRESH_DEAD_LETTER:{code}",
    })
    LOGGER.error(
        "WOW_MLB_1IP_REFRESH_DEAD_LETTER queue_id=%s event_id=%s player=%s attempts=%s "
        "max_attempts=%s error_code=%s probability_publishable=false can_execute=false",
        row.get("queue_id"), row.get("event_id"), row.get("player"), attempts,
        MAX_REFRESH_ATTEMPTS, code,
    )
    return False


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


def _rerun(
    row: dict[str, Any],
    evidence: dict[str, Any],
    artifact_record: dict[str, Any],
) -> dict[str, Any]:
    """Pure rerun helper: caller supplies the already-resolved artifact."""
    money_lane_status = str(row.get("money_lane_status") or "PAYOUT_UNRESOLVED").upper()
    return score_mlb_1ip_empirical(
        artifact_record=artifact_record,
        starter_status=evidence.get("starter_status", ""),
        official_lineup_status=evidence.get("official_lineup_status", ""),
        projected_top_four=evidence.get("projected_top_four"),
        line_value=float(row["line"]),
        side=str(row["direction"]),
        failure_path_prior=evidence.get("failure_path_prior"),
        market_evidence_present=money_lane_status not in {"", "PAYOUT_UNRESOLVED"},
    )


def _dead_letter_backlog(client: Any) -> int:
    """Count rows already parked in FAILED so a dead letter is never silent."""
    response = (
        client.table("wow_mlb_1ip_refresh_queue")
        .select("queue_id,event_id,player,last_error_code", count="exact")
        .eq("status", "FAILED")
        .limit(DEAD_LETTER_SAMPLE_LIMIT)
        .execute()
    )
    sample = list(getattr(response, "data", None) or [])
    count = getattr(response, "count", None)
    total = int(count) if isinstance(count, int) else len(sample)
    if total:
        LOGGER.error(
            "WOW_MLB_1IP_REFRESH_DEAD_LETTER_BACKLOG count=%s sample=%s "
            "probability_publishable=false can_execute=false",
            total,
            [
                {
                    "queue_id": item.get("queue_id"),
                    "event_id": item.get("event_id"),
                    "player": item.get("player"),
                    "last_error_code": item.get("last_error_code"),
                }
                for item in sample
            ],
        )
    return total


def _reconcile_expired_dead_letters(client: Any, ts: datetime) -> int:
    """Terminate dead letters whose pregame window is irreversibly closed.

    These rows cannot be retried or scored safely after first pitch. Preserve
    the prior typed failure in the audit code while moving them to the same
    explicit terminal disposition used by the normal refresh state machine.
    """
    response = (
        client.table("wow_mlb_1ip_refresh_queue")
        .select("queue_id,event_id,player,event_start_time,last_error_code")
        .eq("status", "FAILED")
        .limit(50)
        .execute()
    )
    reconciled = 0
    for row in list(getattr(response, "data", None) or []):
        try:
            event_start = _aware(row.get("event_start_time"))
        except Exception:
            continue
        if ts < event_start:
            continue
        prior_code = str(row.get("last_error_code") or "UNKNOWN")
        update = {
            "status": "EXPIRED_PREGAME_WINDOW",
            "next_refresh_at": None,
            "terminal_label": "EXPIRED_PREGAME_WINDOW",
            "last_error_code": f"RECONCILED_EXPIRED_DEAD_LETTER:{prior_code}",
            "updated_at": ts.isoformat(),
            "probability_publishable": False,
            "can_execute": False,
        }
        client.table("wow_mlb_1ip_refresh_queue").update(update).eq(
            "queue_id", row.get("queue_id")
        ).execute()
        reconciled += 1
        LOGGER.warning(
            "WOW_MLB_1IP_REFRESH_DEAD_LETTER_RECONCILED queue_id=%s event_id=%s "
            "player=%s terminal_status=EXPIRED_PREGAME_WINDOW prior_error_code=%s "
            "probability_publishable=false can_execute=false",
            row.get("queue_id"), row.get("event_id"), row.get("player"), prior_code,
        )
    return reconciled


def run_once(*, client: Any | None = None, now: datetime | None = None, hydrator: Callable[..., dict[str, Any]] | None = None) -> dict[str, int]:
    if client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        client = create_client(url, key)
    ts = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = _pg_timestamp(ts)

    # Rows whose event has already started are deliberately included: the state
    # machine terminates them as EXPIRED_PREGAME_WINDOW. Excluding them left
    # past-event rows waiting for an official lineup forever.
    response = (
        client.table("wow_mlb_1ip_refresh_queue")
        .select("*")
        .eq("status", "WAITING_FOR_OFFICIAL_LINEUP")
        .or_(f"next_refresh_at.is.null,next_refresh_at.lte.{stamp}")
        .order("event_start_time")
        .limit(50)
        .execute()
    )
    rows = list(response.data or [])
    counters = {
        "seen": len(rows),
        "waiting": 0,
        "rerun_completed": 0,
        "purged": 0,
        "expired": 0,
        "expired_stale": 0,
        "failed": 0,
        "retry_scheduled": 0,
        "dead_lettered": 0,
        "dead_letter_reconciled_expired": 0,
    }

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
        window_open = _pregame_window_open(row, ts)
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
                    code = artifact_error or "REFRESH_ARTIFACT_RESOLUTION_ERROR"
                    if _apply_failure(base_update, row=row, attempts=attempts, ts=ts, code=code):
                        counters["retry_scheduled"] += 1
                    else:
                        counters["failed"] += 1
                        counters["dead_lettered"] += 1
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
                if not window_open:
                    counters["expired_stale"] += 1
                    LOGGER.warning(
                        "WOW_MLB_1IP_REFRESH_STALE_ABANDONED queue_id=%s event_id=%s player=%s "
                        "event_start_time=%s attempts=%s probability_publishable=false can_execute=false",
                        queue_id, row.get("event_id"), row.get("player"),
                        row.get("event_start_time"), attempts,
                    )
            else:
                if _apply_failure(base_update, row=row, attempts=attempts, ts=ts, code=status):
                    counters["retry_scheduled"] += 1
                else:
                    counters["failed"] += 1
                    counters["dead_lettered"] += 1
        except Exception as exc:
            if _apply_failure(base_update, row=row, attempts=attempts, ts=ts, code=_failure_code(exc)):
                counters["retry_scheduled"] += 1
            else:
                counters["failed"] += 1
                counters["dead_lettered"] += 1

        client.table("wow_mlb_1ip_refresh_queue").update(base_update).eq("queue_id", queue_id).execute()

    counters["dead_letter_reconciled_expired"] = _reconcile_expired_dead_letters(client, ts)
    counters["dead_letter_backlog"] = _dead_letter_backlog(client)
    return counters


if __name__ == "__main__":
    print(run_once())
