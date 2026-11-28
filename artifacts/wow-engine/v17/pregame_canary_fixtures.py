"""Shared receipt-to-canary-row adapter and live fixture acquisition.

The V17 live canary must exercise ``/score-pick-request`` with real, future-dated
pregame rows. The governed MLB, NFL and WNBA forward-evidence producers already
discover official events, resolve player identity, hydrate governed evidence and
derive a deterministic discovery-only half-point line, so this module reuses them
instead of reimplementing acquisition.

``receipt_to_canary_row`` maps a producer receipt onto the exact canary row
contract. It is a whitelist: producer bookkeeping fields are dropped because
``PickRequestRow`` forbids extras, and ``source_type`` is preserved verbatim so a
discovery row is never relabelled as market data.

``acquire_canary_rows`` runs each producer against a no-write sink and returns
only sports that yielded the required four future rows. An empty slate and an
unavailable source are reported as such; neither is replaced by a synthetic
event, player, stat or line. ``can_execute`` is false everywhere.
"""
from __future__ import annotations

import inspect
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from v17.prop_snapshot_sink import NO_WRITE_MODE, NoWriteSnapshotStore

ACQUISITION_ENV = "WOW_CANARY_ACQUIRE_FIXTURES"
SPORTS = ("NFL", "MLB", "WNBA")
ROWS_PER_SPORT = 4
DEFAULT_BUDGET = 8
DEFAULT_TIMEZONE = "America/Chicago"

VALID_SOURCE_TYPES = frozenset(
    {"SCREENSHOT", "PDF", "AUTONOMOUS_DISCOVERY", "PASTED_BOARD", "NORMALIZED"}
)

# Exactly the fields the canary forwards to /score-pick-request.
CANARY_ROW_FIELDS = (
    "row_key",
    "event_id",
    "event_start_time",
    "sport",
    "league",
    "player",
    "stat_type",
    "line",
    "direction",
    "opponent",
    "source_type",
    "platform",
)


def acquisition_enabled() -> bool:
    """Live acquisition is explicit; unset keeps static-shape testing."""
    return str(os.environ.get(ACQUISITION_ENV, "0")).strip().lower() in {"1", "true", "yes", "on"}


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _line(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def receipt_to_canary_row(receipt: Mapping[str, Any]) -> dict[str, Any] | None:
    """Map one receipt to a canary row, or ``None`` if it is incomplete.

    A missing line, unknown direction or unparseable start time is a real
    acquisition gap and is dropped rather than repaired.
    """
    if not isinstance(receipt, Mapping):
        return None
    sport = str(receipt.get("sport") or "").strip().upper()
    event_id = _text(receipt.get("event_id"))
    player = _text(receipt.get("player"))
    stat_type = "_".join(_text(receipt.get("stat_type")).upper().replace("-", " ").split())
    direction = str(receipt.get("direction") or receipt.get("side") or "").strip().upper()
    source_type = str(receipt.get("source_type") or "").strip().upper()
    line = _line(receipt.get("line"))
    if (
        sport not in SPORTS
        or not event_id
        or not player
        or not stat_type
        or direction not in {"MORE", "LESS"}
        or source_type not in VALID_SOURCE_TYPES
        or _aware(receipt.get("event_start_time")) is None
        or line is None
    ):
        return None
    return {
        "row_key": f"acquired:{sport.lower()}:{event_id}:{player}:{stat_type}:{line:g}",
        "event_id": event_id,
        "event_start_time": str(receipt["event_start_time"]),
        "sport": sport,
        "league": sport,
        "player": player,
        "stat_type": stat_type,
        "line": line,
        "direction": direction,
        "opponent": _text(receipt.get("opponent")) or None,
        "source_type": source_type,
        "platform": _text(receipt.get("platform")) or None,
    }


def future_rows(rows: Iterable[Mapping[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    """Keep unique rows whose event start is strictly in the future."""
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        start = _aware(row.get("event_start_time"))
        key = str(row.get("row_key") or "")
        if start is None or start <= now or not key or key in seen:
            continue
        seen.add(key)
        kept.append(dict(row))
    return kept


def fixtures_from_receipts(
    receipts_by_sport: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Pure eligibility contract: a sport qualifies only with four future rows."""
    configs: dict[str, list[dict[str, Any]]] = {}
    detail: dict[str, Any] = {}
    for sport in SPORTS:
        receipts = list(receipts_by_sport.get(sport) or [])
        mapped = [row for row in (receipt_to_canary_row(item) for item in receipts) if row]
        future = future_rows(mapped, now=now)
        detail[sport] = {"mapped_rows": len(mapped), "future_rows": len(future)}
        if len(future) >= ROWS_PER_SPORT:
            configs[sport] = future[:ROWS_PER_SPORT]
    return {"configs": configs, "sports_eligible": tuple(configs), "sports": detail}


def _candidate_dates(now: datetime, requested_timezone: str) -> list[str]:
    """Probe the local slate plus two days, so a weekend slate is reachable."""
    try:
        zone = ZoneInfo(requested_timezone)
    except Exception:
        zone = timezone.utc
    local = now.astimezone(zone).date()
    return [(local + timedelta(days=offset)).isoformat() for offset in range(3)]


def _accepted_kwargs(target: Any, values: Mapping[str, Any]) -> dict[str, Any]:
    """Pass only fields/parameters the producer model or function declares."""
    fields = getattr(target, "model_fields", None)
    if isinstance(fields, Mapping):
        return {key: value for key, value in values.items() if key in fields}
    parameters = inspect.signature(target).parameters
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return dict(values)
    return {key: value for key, value in values.items() if key in parameters}


def _mlb_batch(*, db: Any, requested_date: str, requested_timezone: str, budget: int,
               now: datetime, http_get: Callable[..., Any]) -> dict[str, Any]:
    from v17 import mlb_prop_evidence_control_plane as producer

    values = {
        "requested_date": requested_date,
        "requested_timezone": requested_timezone,
        "candidate_offset": 0,
        "max_candidates": budget,
    }
    request = producer.MLBForwardEvidenceRequest(**_accepted_kwargs(producer.MLBForwardEvidenceRequest, values))
    return producer.acquire_mlb_forward_evidence_batch(
        request,
        **_accepted_kwargs(producer.acquire_mlb_forward_evidence_batch, {"db": db, "now": now, "http_get": http_get}),
    )


def _nfl_batch(*, db: Any, requested_date: str, requested_timezone: str, budget: int,
               now: datetime, http_get: Callable[..., Any]) -> dict[str, Any]:
    from v17 import nfl_prop_evidence_control_plane as producer

    values = {"requested_date": requested_date, "candidate_offset": 0, "max_candidates": budget}
    request = producer.NFLForwardEvidenceRequest(**_accepted_kwargs(producer.NFLForwardEvidenceRequest, values))
    return producer.acquire_nfl_forward_evidence_batch(
        request,
        **_accepted_kwargs(
            producer.acquire_nfl_forward_evidence_batch,
            {"db": db, "now": now, "http_get": http_get, "priority_targets": (), "_discovery_cache": {}},
        ),
    )


def _wnba_batch(*, db: Any, requested_date: str, requested_timezone: str, budget: int,
                now: datetime, http_get: Callable[..., Any]) -> dict[str, Any]:
    from v17 import wnba_prop_evidence_acquisition as producer

    return producer.acquire_wnba_prop_snapshots(
        **_accepted_kwargs(
            producer.acquire_wnba_prop_snapshots,
            {
                "db": db,
                "requested_date": requested_date,
                "requested_timezone": requested_timezone,
                "max_candidates": budget,
                "now": now,
                "http_get": http_get,
            },
        )
    )


_PRODUCERS: dict[str, Callable[..., dict[str, Any]]] = {
    "MLB": _mlb_batch,
    "NFL": _nfl_batch,
    "WNBA": _wnba_batch,
}


def acquire_canary_rows(
    *,
    now: datetime | None = None,
    http_get: Callable[..., Any] | None = None,
    budget: int = DEFAULT_BUDGET,
    requested_timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Acquire future pregame rows for every sport without governed writes.

    Each sport probes the current local slate date and then the next one, so a
    late-night run can still find a genuinely future event.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    budget = max(ROWS_PER_SPORT, int(budget))
    if http_get is None:
        import httpx

        http_get = httpx.get

    sink = NoWriteSnapshotStore()
    configs: dict[str, list[dict[str, Any]]] = {}
    sports: dict[str, Any] = {}
    blockers: dict[str, str] = {}

    for sport in SPORTS:
        receipts: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []
        for requested_date in _candidate_dates(now, requested_timezone):
            try:
                batch = _PRODUCERS[sport](
                    db=sink,
                    requested_date=requested_date,
                    requested_timezone=requested_timezone,
                    budget=budget,
                    now=now,
                    http_get=http_get,
                )
            except ImportError as exc:
                blockers[sport] = f"CANARY_ACQUISITION_DEPENDENCIES_UNAVAILABLE:{type(exc).__name__}"
                break
            except Exception as exc:  # producer contract/transport failure, never a model result
                blockers[sport] = f"CANARY_ACQUISITION_FAILED:{type(exc).__name__}"
                break
            batch_receipts = list(batch.get("receipts") or [])
            receipts.extend(batch_receipts)
            attempts.append({
                "requested_date": requested_date,
                "source_status": batch.get("status"),
                "receipt_rows": len(batch_receipts),
            })
            if str(batch.get("status") or "") == "DATA_UNOBTAINABLE":
                # An upstream failure is not an empty slate: keep the typed reason
                # so the report cannot read as "no games scheduled".
                reason = (batch.get("blockers") or ["SOURCE_DATA_UNOBTAINABLE"])[0]
                blockers[sport] = f"CANARY_ACQUISITION_FAILED:{reason}"
                continue
            eligible = fixtures_from_receipts({sport: receipts}, now=now)["configs"].get(sport)
            if eligible:
                configs[sport] = eligible
                blockers.pop(sport, None)
                break
        sports[sport] = {"attempts": attempts, "selected_rows": len(configs.get(sport) or [])}

    status = "ACQUIRED" if configs else ("ACQUISITION_FAILED" if blockers else "NO_FUTURE_EVENT")
    return {
        "status": status,
        "fixture_source": "ACQUIRED",
        "snapshot_write_mode": NO_WRITE_MODE,
        "snapshot_writes_suppressed": sink.write_count,
        "sports_eligible": list(configs),
        "sports": sports,
        "blockers": blockers,
        "configs": configs,
        "can_execute": False,
    }


__all__ = [
    "ACQUISITION_ENV",
    "CANARY_ROW_FIELDS",
    "DEFAULT_BUDGET",
    "DEFAULT_TIMEZONE",
    "ROWS_PER_SPORT",
    "SPORTS",
    "acquire_canary_rows",
    "acquisition_enabled",
    "fixtures_from_receipts",
    "future_rows",
    "receipt_to_canary_row",
]
