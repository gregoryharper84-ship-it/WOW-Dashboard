"""Bounded recurring TheRundown market-history collection for V17.

This module is market-evidence infrastructure only. It never supplies a sporting
probability, never mutates probability rank, and never enables execution.

The collector deliberately uses a narrow moneyline-only scope and a hard daily
call/datapoint budget. It resolves sportsbook/market IDs from the provider
catalog instead of permanently hardcoding them. OPEN/CLOSE rows are *captured
references*: the first and last locally observed pregame quote, not a claim that
the provider supplied an official opening or closing line.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from v17 import rundown_market_ingestor as ingestor
from v17 import rundown_market_ledger as ledger

CAN_EXECUTE = False
LOGGER = logging.getLogger("wow.v17.rundown_market_history")
BUDGET_PREFIX = "RUNDOWN:HISTORY_BUDGET:"
REFERENCE_VERSION = "CAPTURED_PREMATCH_REFERENCE_V1"
OPEN_SEMANTICS = "FIRST_CAPTURED_PREMATCH_REFERENCE_NOT_PROVIDER_OFFICIAL_OPEN"
CLOSE_SEMANTICS = "LAST_CAPTURED_PREMATCH_REFERENCE_NOT_PROVIDER_OFFICIAL_CLOSE"


def enabled() -> bool:
    return os.getenv("WOW_RUNDOWN_MARKET_HISTORY_ENABLED", "false").strip().lower() == "true"


def _int_env(name: str, default: int, *, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))


def interval_seconds() -> int:
    # Deliberately slow by default. This is a history collector, not a live-price
    # execution feed, and it must not compete with Scout for provider allowance.
    return _int_env("WOW_RUNDOWN_MARKET_HISTORY_INTERVAL_SECONDS", 7200, low=1800, high=21600)


def max_calls_per_day() -> int:
    return _int_env("WOW_RUNDOWN_MARKET_HISTORY_MAX_CALLS_PER_DAY", 12, low=1, high=48)


def max_datapoints_per_day() -> int:
    return _int_env("WOW_RUNDOWN_MARKET_HISTORY_MAX_DATAPOINTS_PER_DAY", 2500, low=1, high=100000)


def _csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def configured_sports() -> tuple[str, ...]:
    return _csv("WOW_RUNDOWN_MARKET_HISTORY_SPORT_KEYS", "baseball_mlb")


def configured_books() -> tuple[str, ...]:
    # Three-book default is enough to create real cross-book history while
    # staying materially cheaper than collecting every published affiliate.
    return _csv("WOW_RUNDOWN_MARKET_HISTORY_BOOKS", "Pinnacle,Draftkings,Fanduel")


def _timezone() -> ZoneInfo:
    name = os.getenv("WOW_RUNDOWN_MARKET_HISTORY_TIMEZONE", "America/Chicago").strip()
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - invalid config must fail to deterministic UTC
        return ZoneInfo("UTC")


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _response_rows(response: Any) -> list[dict[str, Any]]:
    rows = getattr(response, "data", None) or []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _catalog_rows(client: Any, catalog_type: str) -> list[dict[str, Any]]:
    response = (
        client.table(ledger.CATALOG_TABLE)
        .select("provider_id,canonical_key,display_name,active")
        .eq("provider", ledger.PROVIDER)
        .eq("catalog_type", catalog_type)
        .execute()
    )
    return _response_rows(response)


def resolve_collection_scope(client: Any) -> dict[str, Any]:
    """Resolve one moneyline market and configured books from persisted catalog."""
    market_rows = _catalog_rows(client, "MARKET")
    moneyline = [
        row
        for row in market_rows
        if str(row.get("canonical_key") or "").strip().lower() == "h2h"
        or str(row.get("display_name") or "").strip().lower() == "moneyline"
    ]
    if len(moneyline) != 1:
        return {
            "status": "BLOCKED",
            "reason_code": "RUNDOWN_HISTORY_MONEYLINE_MARKET_ID_UNRESOLVED",
            "match_count": len(moneyline),
            "prediction_authority": False,
            "can_execute": False,
        }

    affiliate_rows = _catalog_rows(client, "AFFILIATE")
    by_name = {
        str(row.get("display_name") or "").strip().casefold(): row
        for row in affiliate_rows
        if row.get("provider_id") is not None and row.get("active") is not False
    }
    requested = configured_books()
    resolved: list[str] = []
    missing: list[str] = []
    for name in requested:
        row = by_name.get(name.casefold())
        if row is None:
            missing.append(name)
        else:
            resolved.append(str(row["provider_id"]))
    if missing or len(resolved) < 2:
        return {
            "status": "BLOCKED",
            "reason_code": "RUNDOWN_HISTORY_MULTI_BOOK_SCOPE_UNRESOLVED",
            "requested_books": list(requested),
            "missing_books": missing,
            "resolved_book_count": len(resolved),
            "prediction_authority": False,
            "can_execute": False,
        }
    return {
        "status": "READY",
        "market_ids": (str(moneyline[0]["provider_id"]),),
        "affiliate_ids": tuple(resolved),
        "book_names": list(requested),
        "prediction_authority": False,
        "can_execute": False,
    }


def _reference_key(source_key: Any, kind: str) -> str:
    raw = f"{source_key}|{kind}|{REFERENCE_VERSION}".encode("utf-8")
    return sha256(raw).hexdigest()


def _group_key(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(row.get(field) or "")
        for field in (
            "provider",
            "provider_event_id",
            "market_id",
            "participant_id",
            "selection",
            "line_id",
            "affiliate_id",
        )
    )


def _quote_time(row: dict[str, Any]) -> datetime | None:
    return _parse_dt(row.get("price_updated_at")) or _parse_dt(row.get("fetched_at"))


def _clone_reference(
    source: dict[str, Any],
    *,
    kind: str,
    semantics: str,
    capture_count: int,
) -> dict[str, Any]:
    out = {
        key: value
        for key, value in source.items()
        if key not in {"observation_id", "created_at"}
    }
    out["observation_key"] = _reference_key(source.get("observation_key"), kind)
    out["snapshot_kind"] = kind
    raw = dict(source.get("raw_payload") or {})
    raw.update(
        {
            "reference_semantics": semantics,
            "reference_version": REFERENCE_VERSION,
            "reference_source_observation_key": source.get("observation_key"),
            "reference_capture_count": capture_count,
            "provider_official_open_close": False,
        }
    )
    out["raw_payload"] = raw
    out["prediction_authority"] = False
    out["can_execute"] = False
    return out


def derive_captured_reference_rows(
    rows: Iterable[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Derive deterministic captured OPEN/CLOSE markers from stored CURRENT rows."""
    current_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for value in rows:
        if not isinstance(value, dict):
            continue
        grouped.setdefault(_group_key(value), []).append(dict(value))

    derived: list[dict[str, Any]] = []
    for group_rows in grouped.values():
        kinds = {str(row.get("snapshot_kind") or "").upper() for row in group_rows}
        current_rows = [
            row
            for row in group_rows
            if str(row.get("snapshot_kind") or "").upper() == "CURRENT"
            and row.get("is_live") is not True
            and _quote_time(row) is not None
        ]
        if not current_rows:
            continue
        current_rows.sort(key=lambda row: _quote_time(row) or current_now)
        if "OPEN" not in kinds:
            derived.append(
                _clone_reference(
                    current_rows[0],
                    kind="OPEN",
                    semantics=OPEN_SEMANTICS,
                    capture_count=len(current_rows),
                )
            )

        event_start = _parse_dt(current_rows[0].get("event_start_utc"))
        if event_start is None or event_start > current_now or "CLOSE" in kinds:
            continue
        eligible = [row for row in current_rows if (_quote_time(row) or current_now) <= event_start]
        if not eligible:
            continue
        derived.append(
            _clone_reference(
                eligible[-1],
                kind="CLOSE",
                semantics=CLOSE_SEMANTICS,
                capture_count=len(eligible),
            )
        )
    return derived


def _slate_window(slate_date: str, tz: ZoneInfo) -> tuple[str, str]:
    local_start = datetime.fromisoformat(slate_date).replace(tzinfo=tz)
    return _iso(local_start.astimezone(timezone.utc)), _iso((local_start + timedelta(days=1)).astimezone(timezone.utc))


def materialize_captured_references(
    client: Any,
    *,
    sport_key: str,
    slate_date: str,
    now: datetime | None = None,
) -> int:
    """Materialize first/last captured pregame references without provider calls."""
    start_utc, end_utc = _slate_window(slate_date, _timezone())
    response = (
        client.table(ledger.TABLE)
        .select("*")
        .eq("provider", ledger.PROVIDER)
        .eq("sport_key", sport_key)
        .gte("event_start_utc", start_utc)
        .lt("event_start_utc", end_utc)
        .limit(10000)
        .execute()
    )
    rows = _response_rows(response)
    references = derive_captured_reference_rows(rows, now=now)
    return ledger.persist_observations(client, references)


def _budget_key(local_date: str) -> str:
    return f"{BUDGET_PREFIX}{local_date}"


def _read_budget(client: Any, local_date: str) -> dict[str, Any]:
    response = (
        client.table(ledger.SYNC_TABLE)
        .select("feed_key,metadata,last_error_code,last_success_at,last_failure_at")
        .eq("feed_key", _budget_key(local_date))
        .limit(1)
        .execute()
    )
    rows = _response_rows(response)
    if not rows:
        return {"calls": 0, "datapoints": 0}
    metadata = dict(rows[0].get("metadata") or {})
    return {
        "calls": int(metadata.get("calls") or 0),
        "datapoints": int(metadata.get("datapoints") or 0),
        "suspended_until": metadata.get("suspended_until"),
        "last_error_code": rows[0].get("last_error_code"),
    }


def _write_budget(
    client: Any,
    *,
    local_date: str,
    calls: int,
    datapoints: int,
    status: str,
    reason_code: str | None = None,
    suspended_until: str | None = None,
) -> None:
    now_iso = _iso(datetime.now(timezone.utc))
    payload: dict[str, Any] = {
        "feed_key": _budget_key(local_date),
        "acquisition_mode": "SNAPSHOT",
        "last_rows_written": 0,
        "last_error_code": reason_code,
        "metadata": {
            "collector_status": status,
            "calls": calls,
            "datapoints": datapoints,
            "max_calls_per_day": max_calls_per_day(),
            "max_datapoints_per_day": max_datapoints_per_day(),
            "interval_seconds": interval_seconds(),
            "configured_books": list(configured_books()),
            "configured_sports": list(configured_sports()),
            "suspended_until": suspended_until,
            "prediction_authority": False,
        },
    }
    if reason_code:
        payload["last_failure_at"] = now_iso
    else:
        payload["last_success_at"] = now_iso
    ledger.persist_sync_state(client, payload)


def collect_history_once(
    client: Any,
    *,
    now: datetime | None = None,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Collect one bounded history cycle and materialize captured references."""
    current_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    tz = _timezone()
    local_date = current_now.astimezone(tz).date().isoformat()
    budget = _read_budget(client, local_date)
    suspended_until = _parse_dt(budget.get("suspended_until"))
    if suspended_until is not None and current_now < suspended_until:
        return {
            "status": "SUSPENDED",
            "reason_code": budget.get("last_error_code") or "RUNDOWN_HISTORY_SUSPENDED",
            "suspended_until": _iso(suspended_until),
            "provider_calls": 0,
            "prediction_authority": False,
            "can_execute": False,
        }
    if budget["calls"] >= max_calls_per_day() or budget["datapoints"] >= max_datapoints_per_day():
        return {
            "status": "BUDGET_EXHAUSTED",
            "reason_code": "RUNDOWN_HISTORY_DAILY_BUDGET_REACHED",
            "calls": budget["calls"],
            "datapoints": budget["datapoints"],
            "provider_calls": 0,
            "prediction_authority": False,
            "can_execute": False,
        }

    scope = resolve_collection_scope(client)
    if scope.get("status") != "READY":
        return scope

    calls = int(budget["calls"])
    datapoints = int(budget["datapoints"])
    total_rows = 0
    total_references = 0
    results: list[dict[str, Any]] = []
    reason_code: str | None = None
    next_suspend: str | None = None

    for sport_key in configured_sports():
        if calls >= max_calls_per_day() or datapoints >= max_datapoints_per_day():
            break
        result = ingestor.collect_snapshot(
            client,
            sport_key=sport_key,
            slate_date=local_date,
            market_ids=scope["market_ids"],
            affiliate_ids=scope["affiliate_ids"],
            opener=opener,
            allow_delta=False,
            include_all_periods=False,
        )
        calls += 1
        datapoints += int(result.get("datapoints") or 0)
        total_rows += int(result.get("rows_written") or 0)
        result = dict(result)
        result["sport_key"] = sport_key
        results.append(result)

        if result.get("status") == "COMPLETE":
            total_references += materialize_captured_references(
                client,
                sport_key=sport_key,
                slate_date=local_date,
                now=current_now,
            )
            continue

        reason_code = str(result.get("reason_code") or result.get("status") or "RUNDOWN_HISTORY_COLLECTION_FAILED")
        # Any 429 is conservatively suspended for 24 hours. The history lane is
        # non-critical and must never turn an ambiguous provider refusal into a
        # retry storm or consume allowance needed by discovery/Scout.
        if "429" in reason_code or "QUOTA" in reason_code:
            next_suspend = _iso(current_now + timedelta(hours=24))
        break

    status = "COMPLETE" if results and all(row.get("status") == "COMPLETE" for row in results) else "PARTIAL"
    if not results:
        status = "BUDGET_EXHAUSTED"
        reason_code = "RUNDOWN_HISTORY_DAILY_BUDGET_REACHED"
    _write_budget(
        client,
        local_date=local_date,
        calls=calls,
        datapoints=datapoints,
        status=status,
        reason_code=reason_code,
        suspended_until=next_suspend,
    )
    return {
        "status": status,
        "reason_code": reason_code,
        "slate_date": local_date,
        "provider_calls": len(results),
        "calls_today": calls,
        "datapoints_today": datapoints,
        "rows_written": total_rows,
        "reference_rows_written": total_references,
        "book_names": scope["book_names"],
        "sports": list(configured_sports()),
        "suspended_until": next_suspend,
        "results": results,
        "prediction_authority": False,
        "can_execute": False,
    }


async def run_history_loop(
    db_client_fn: Callable[[], Any],
    *,
    logger: logging.Logger | None = None,
) -> None:
    """Run recurring collection with bounded cadence; never affect app liveness."""
    log = logger or LOGGER
    initial_delay = _int_env("WOW_RUNDOWN_MARKET_HISTORY_INITIAL_DELAY_SECONDS", 30, low=5, high=600)
    await asyncio.sleep(initial_delay)
    while enabled():
        try:
            result = await asyncio.to_thread(lambda: collect_history_once(db_client_fn()))
            log.info(
                "RUNDOWN_MARKET_HISTORY=%s reason=%s calls=%s datapoints=%s rows=%s references=%s books=%s can_execute=false",
                result.get("status"),
                result.get("reason_code"),
                result.get("provider_calls"),
                result.get("datapoints_today"),
                result.get("rows_written"),
                result.get("reference_rows_written"),
                len(result.get("book_names") or []),
            )
        except Exception as exc:  # noqa: BLE001 - evidence loop must not affect service liveness
            log.exception(
                "RUNDOWN_MARKET_HISTORY=FAIL error_type=%s can_execute=false",
                type(exc).__name__,
            )
        await asyncio.sleep(interval_seconds())


__all__ = [
    "CAN_EXECUTE",
    "CLOSE_SEMANTICS",
    "OPEN_SEMANTICS",
    "collect_history_once",
    "configured_books",
    "configured_sports",
    "derive_captured_reference_rows",
    "enabled",
    "interval_seconds",
    "materialize_captured_references",
    "max_calls_per_day",
    "max_datapoints_per_day",
    "resolve_collection_scope",
    "run_history_loop",
]
