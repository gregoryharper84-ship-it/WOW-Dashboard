from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .hourly_forecast_fusion import source_snapshot_id
from .persistence import KalshiWeatherPersistence
from .source_adapters import NwsAdapter
from .supplemental_source_adapters import (
    MetNorwayAdapter,
    NwsGridDataAdapter,
    OpenMeteoEnsembleAdapter,
)


def capture_hourly_supplemental_sources(
    *,
    client,
    rule_snapshot_id: str,
    forecast_latitude: float,
    forecast_longitude: float,
    target_time_utc: str,
    retrieved_at: str,
    http,
) -> Mapping[str, Any]:
    """Persist useful free/public corroboration without changing the model mean.

    These sources are deliberately evidence-only until empirical testing proves
    that a source adds out-of-sample skill. They may improve diagnostics and
    future calibration features, but they cannot silently alter the controlling
    forecast distribution or settlement identity.
    """
    persistence = KalshiWeatherPersistence(client)
    snapshot_ids: list[str] = []
    failures: list[str] = []

    target_date = _parse_utc(target_time_utc).date().isoformat()

    try:
        point = NwsAdapter(http.get_json).point_metadata(
            float(forecast_latitude),
            float(forecast_longitude),
            retrieved_at=retrieved_at,
        )
        grid_url = _forecast_grid_url(point.payload)
        grid = NwsGridDataAdapter(http.get_json).grid_forecast(grid_url, retrieved_at=retrieved_at)
        sid = source_snapshot_id(grid)
        persistence.persist_source_snapshot(
            rule_snapshot_id=rule_snapshot_id,
            source_snapshot_id=sid,
            snapshot=grid,
        )
        snapshot_ids.append(sid)
    except Exception as exc:
        failures.append(f"NWS_GRID:{type(exc).__name__}:{getattr(exc, 'code', '')}")

    try:
        ensemble = OpenMeteoEnsembleAdapter(http.get_json).hourly_temperature_ensemble(
            float(forecast_latitude),
            float(forecast_longitude),
            target_date,
            target_date,
            retrieved_at=retrieved_at,
            model="icon_seamless_eps",
        )
        sid = source_snapshot_id(ensemble)
        persistence.persist_source_snapshot(
            rule_snapshot_id=rule_snapshot_id,
            source_snapshot_id=sid,
            snapshot=ensemble,
        )
        snapshot_ids.append(sid)
    except Exception as exc:
        failures.append(f"OPEN_METEO_ENSEMBLE:{type(exc).__name__}:{getattr(exc, 'code', '')}")

    try:
        metno = MetNorwayAdapter(http.get_json).compact_forecast(
            float(forecast_latitude),
            float(forecast_longitude),
            retrieved_at=retrieved_at,
        )
        sid = source_snapshot_id(metno)
        persistence.persist_source_snapshot(
            rule_snapshot_id=rule_snapshot_id,
            source_snapshot_id=sid,
            snapshot=metno,
        )
        snapshot_ids.append(sid)
    except Exception as exc:
        failures.append(f"MET_NORWAY:{type(exc).__name__}:{getattr(exc, 'code', '')}")

    return {
        "status": "SUPPLEMENTAL_EVIDENCE_CAPTURED" if snapshot_ids else "SUPPLEMENTAL_EVIDENCE_UNAVAILABLE",
        "source_snapshot_ids": snapshot_ids,
        "failures": failures,
        "controlling_model_changed": False,
        "settlement_identity_changed": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def _forecast_grid_url(payload: Mapping[str, Any]) -> str:
    props = payload.get("properties") if isinstance(payload, Mapping) else None
    url = str(props.get("forecastGridData") or "") if isinstance(props, Mapping) else ""
    if not url:
        raise ValueError("NWS forecastGridData URL missing")
    return url


def _parse_utc(value: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return parsed.astimezone(timezone.utc)
