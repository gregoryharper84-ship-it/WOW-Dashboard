"""Immutable weather evidence snapshots with provenance and tamper-evident digests."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

_MARKET_KEYS = {"market_price", "yes_price", "no_price", "edge", "payout", "fee_adjusted_break_even"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(row: dict[str, Any]) -> str:
    payload = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _assert_no_market_data(row: dict[str, Any]) -> None:
    leaked = sorted(k for k in _MARKET_KEYS if k in row)
    if leaked:
        raise ValueError(f"MARKET_DATA_LEAKAGE_IN_WEATHER_SNAPSHOT:{','.join(leaked)}")


def freeze_forecast_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate/freeze one forecast model run without overwriting another model family."""
    row = deepcopy(snapshot)
    _assert_no_market_data(row)
    required = ("station_id", "source_family", "model_name")
    missing = [k for k in required if not row.get(k)]
    if missing:
        raise ValueError(f"FORECAST_SNAPSHOT_REQUIRED_FIELDS_MISSING:{','.join(missing)}")
    if row.get("forecast_high_f") is None and not (row.get("hourly") or row.get("hourly_temperature")):
        raise ValueError("FORECAST_SNAPSHOT_NO_TEMPERATURE_SIGNAL")
    row.setdefault("retrieved_at", _iso_now())
    row.setdefault("source_quality", 1.0)
    row["snapshot_type"] = "WEATHER_FORECAST"
    unsigned = deepcopy(row)
    unsigned.pop("snapshot_digest", None)
    row["snapshot_digest"] = _digest(unsigned)
    return row


def freeze_observation_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Freeze official station observation state, including max-so-far when available."""
    row = deepcopy(snapshot)
    _assert_no_market_data(row)
    if not row.get("station_id"):
        raise ValueError("OBSERVATION_STATION_MISSING")
    if not (row.get("observed_at") or row.get("timestamp")):
        raise ValueError("OBSERVATION_TIMESTAMP_MISSING")
    if row.get("temperature_f") is None and row.get("current_temperature") is None:
        raise ValueError("OBSERVATION_TEMPERATURE_MISSING")
    row.setdefault("retrieved_at", _iso_now())
    row["snapshot_type"] = "OFFICIAL_WEATHER_OBSERVATION"
    unsigned = deepcopy(row)
    unsigned.pop("snapshot_digest", None)
    row["snapshot_digest"] = _digest(unsigned)
    return row


def verify_snapshot_digest(snapshot: dict[str, Any]) -> bool:
    expected = snapshot.get("snapshot_digest")
    if not expected:
        return False
    unsigned = deepcopy(snapshot)
    unsigned.pop("snapshot_digest", None)
    return str(expected) == _digest(unsigned)
