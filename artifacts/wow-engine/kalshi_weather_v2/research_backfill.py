"""Bounded recovery of *existing* Miami hourly Weather Index settlement outcomes.

Never creates historical predictions or reconstructs unavailable pre-event forecasts.
The official Kalshi index and frozen contract remain the settlement authority.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from .persistence import KalshiWeatherPersistence, KalshiWeatherPersistenceError
from .runtime import KalshiWeatherRuntimeError, settle_hourly_prediction

STATION = "KALSHI_WEATHER_INDEX:MIAMI"
MAX_SCAN = 500
MAX_ATTEMPTS = 3
LOOKBACK_DAYS = 90
SETTLEMENT_GRACE = timedelta(minutes=10)
EXPECTED_WINDOW_PREFIX = "POINT_IN_TIME:"


class BackfillDataError(RuntimeError):
    """A transient or structurally invalid ledger read; do not report success."""


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("NAIVE_TIMESTAMP")
    return parsed.astimezone(timezone.utc)


def _existing_predictions(client: Any) -> list[Mapping[str, Any]]:
    try:
        response = (
            client.table("wow_kalshi_weather_predictions")
            .select("prediction_id,decision_time,model_payload,rule_snapshot_id")
            .order("decision_time", desc=True).limit(MAX_SCAN).execute()
        )
        if not isinstance(response.data, list):
            raise ValueError("INVALID_PREDICTION_LEDGER")
        return [row for row in response.data if isinstance(row, Mapping)]
    except Exception as exc:
        raise BackfillDataError("PREDICTION_LEDGER_UNAVAILABLE") from exc


def reconcile_miami_settlements(
    *,
    client: Any,
    limit: int = MAX_ATTEMPTS,
    now: datetime | None = None,
    settle: Callable[..., Mapping[str, Any]] = settle_hourly_prediction,
) -> dict[str, Any]:
    """Recover up to three missing, pre-existing, eligible settlement rows.

    A second call skips already-settled prediction IDs; database unique
    constraints remain the final concurrent-writer guard. Missing canonical
    observations remain missing, with no alternate-station substitution.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_ATTEMPTS:
        raise ValueError("BACKFILL_LIMIT_INVALID")
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("BACKFILL_CLOCK_MUST_BE_AWARE")
    clock = clock.astimezone(timezone.utc)
    cutoff = clock - SETTLEMENT_GRACE
    lookback = clock - timedelta(days=LOOKBACK_DAYS)
    persistence = KalshiWeatherPersistence(client)
    eligible: list[tuple[datetime, str]] = []
    rejected_bad_provenance = 0

    for row in _existing_predictions(client):
        payload = row.get("model_payload")
        contract = payload.get("contract") if isinstance(payload, Mapping) else None
        if not isinstance(contract, Mapping):
            continue
        if (str(contract.get("settlement_location_code") or "").upper() != STATION
                or contract.get("lane") != "HOURLY_TEMPERATURE"):
            continue
        target_text = str(contract.get("observation_window") or "")
        if not target_text.startswith(EXPECTED_WINDOW_PREFIX):
            rejected_bad_provenance += 1
            continue
        try:
            target = _utc(target_text[len(EXPECTED_WINDOW_PREFIX):])
            decision = _utc(str(row.get("decision_time") or ""))
        except (TypeError, ValueError):
            rejected_bad_provenance += 1
            continue
        # Prevent look-ahead and exclude out-of-scope/unfinished target hours.
        if not (decision < target <= cutoff and target >= lookback):
            if decision >= target:
                rejected_bad_provenance += 1
            continue
        prediction_id = str(row.get("prediction_id") or "")
        if not prediction_id or not row.get("rule_snapshot_id"):
            rejected_bad_provenance += 1
            continue
        eligible.append((target, prediction_id))

    # Rotate the bounded search through every candidate across five-minute
    # launch windows. Unlike restarting at the oldest unresolved record every
    # run, missing/non-final official points cannot permanently starve later
    # candidates. No mutable cursor table or new scheduler is required.
    eligible.sort(key=lambda item: (item[0], item[1]))
    rotation_offset = ((int(clock.timestamp()) // 300) * limit) % len(eligible) if eligible else 0
    eligible = eligible[rotation_offset:] + eligible[:rotation_offset]
    attempted = 0
    written: list[dict[str, str]] = []
    pending: list[dict[str, str]] = []
    skipped_existing = 0

    for _target, prediction_id in eligible:
        if attempted >= limit:
            break
        try:
            if persistence.outcome_exists_for_prediction(prediction_id):
                skipped_existing += 1
                continue
        except KalshiWeatherPersistenceError as exc:
            raise BackfillDataError("OUTCOME_LEDGER_UNAVAILABLE") from exc
        attempted += 1
        try:
            result = settle(
                client=client,
                prediction_id=prediction_id,
                index_city="miami",
                # settled_at is the ingestion/recording timestamp, NOT the
                # target observation time; source target time is stored
                # separately in the immutable outcome provenance.
                settled_at=clock.isoformat().replace("+00:00", "Z"),
            )
        except KalshiWeatherRuntimeError as exc:
            if exc.code == "OUTCOME_ALREADY_RECORDED":
                skipped_existing += 1
                continue
            if exc.code in ("SETTLEMENT_POINT_UNAVAILABLE", "SETTLEMENT_POINT_NOT_FINAL_QUALITY"):
                pending.append({"prediction_id": prediction_id, "reason": exc.code})
                continue
            raise BackfillDataError("CANONICAL_SETTLEMENT_REJECTED") from exc
        except Exception as exc:
            raise BackfillDataError("CANONICAL_SETTLEMENT_RETRY_REQUIRED") from exc

        receipt_target = result.get("target_time_utc")
        if (result.get("status") != "SETTLED"
                or not result.get("outcome_id")
                or not isinstance(receipt_target, str)
                or _utc(receipt_target) != _target):
            raise BackfillDataError("SETTLEMENT_RECEIPT_INVALID")
        written.append({
            "prediction_id": prediction_id,
            "outcome_id": str(result["outcome_id"]),
            "target_time_utc": str(result.get("target_time_utc") or ""),
        })

    return {
        "status": "SETTLEMENT_RECOVERY_RECORDED" if written else "NO_NEW_CANONICAL_SETTLEMENT",
        "lane": "HOURLY_TEMPERATURE",
        "settlement_location_code": STATION,
        "scanned_limit": MAX_SCAN,
        "eligible_preexisting_predictions": len(eligible),
        "rotation_offset": rotation_offset,
        "attempted": attempted,
        "new_outcomes": len(written),
        "already_recorded": skipped_existing,
        "unavailable_official_points": pending,
        "rejected_bad_provenance": rejected_bad_provenance,
        "outcome_receipts": written,
        "historical_forecasts_created": 0,
        "probability_publishable": False,
        "model_p_yes": None,
        "model_p_no": None,
        "can_execute": False,
        "status_gate": "NO_PLAY_DATA_INSUFFICIENT",
    }
