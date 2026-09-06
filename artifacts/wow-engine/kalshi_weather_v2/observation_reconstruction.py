from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class ObservationPoint:
    timestamp: str
    temperature_f: float
    source_id: str


@dataclass(frozen=True)
class ReconstructedExtreme:
    metric: str
    value_f: float | None
    observation_time: str | None
    points_used: int
    source_ids: tuple[str, ...]
    complete: bool
    blockers: tuple[str, ...] = ()


def c_to_f(value_c: float) -> float:
    return float(value_c) * 9.0 / 5.0 + 32.0


def reconstruct_temperature_series(features: Iterable[Mapping[str, Any]]) -> tuple[ObservationPoint, ...]:
    """Extract valid temperature observations from NWS-style GeoJSON features.

    No interpolation, padding, or daily-extreme field is used. Null/malformed
    rows are ignored; duplicates are de-duplicated by timestamp/value/source.
    """
    points: list[ObservationPoint] = []
    seen: set[tuple[str, float, str]] = set()
    for feature in features:
        props = feature.get("properties") if isinstance(feature, Mapping) else None
        if not isinstance(props, Mapping):
            continue
        timestamp = props.get("timestamp")
        temp = props.get("temperature")
        if not timestamp or not isinstance(temp, Mapping):
            continue
        value = temp.get("value")
        unit = str(temp.get("unitCode") or "")
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if unit.endswith("degC"):
            value_f = c_to_f(numeric)
        elif unit.endswith("degF"):
            value_f = numeric
        else:
            continue
        source_id = str(feature.get("id") or props.get("@id") or timestamp)
        key = (str(timestamp), round(value_f, 6), source_id)
        if key in seen:
            continue
        seen.add(key)
        points.append(ObservationPoint(str(timestamp), value_f, source_id))
    return tuple(sorted(points, key=lambda p: p.timestamp))


def reconstruct_extreme(points: Iterable[ObservationPoint], metric: str) -> ReconstructedExtreme:
    points_tuple = tuple(points)
    metric_norm = metric.strip().upper()
    if metric_norm not in {"MAX", "MIN"}:
        return ReconstructedExtreme(
            metric=metric_norm,
            value_f=None,
            observation_time=None,
            points_used=len(points_tuple),
            source_ids=tuple(p.source_id for p in points_tuple),
            complete=False,
            blockers=("OBSERVATION_EXTREME_METRIC_UNSUPPORTED",),
        )
    if not points_tuple:
        return ReconstructedExtreme(
            metric=metric_norm,
            value_f=None,
            observation_time=None,
            points_used=0,
            source_ids=(),
            complete=False,
            blockers=("OFFICIAL_OBSERVATION_SERIES_EMPTY",),
        )

    selector = max if metric_norm == "MAX" else min
    chosen = selector(points_tuple, key=lambda p: p.temperature_f)
    return ReconstructedExtreme(
        metric=metric_norm,
        value_f=chosen.temperature_f,
        observation_time=chosen.timestamp,
        points_used=len(points_tuple),
        source_ids=tuple(p.source_id for p in points_tuple),
        complete=True,
    )
