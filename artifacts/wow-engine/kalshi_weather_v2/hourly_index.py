from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from .kalshi_market_data import KALSHI_BASE_URL


ALLOWED_INDEX_STATUSES = {"normal", "degraded", "incomplete"}


class WeatherIndexError(ValueError):
    def __init__(self, code: str, blockers: tuple[str, ...]):
        self.code = code
        self.blockers = blockers
        super().__init__(f"{code}: {', '.join(blockers)}")


@dataclass(frozen=True)
class WeatherIndexStationReading:
    station_id: str
    code: str
    source: str | None
    temp_f: Decimal | None
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class WeatherIndexPoint:
    timestamp_ms: int
    value_f: Decimal | None
    status: str
    contributors: int | None
    station_readings: tuple[WeatherIndexStationReading, ...]
    receipt_basis: Any = None


@dataclass(frozen=True)
class WeatherIndexSnapshot:
    city: str
    units: str
    config_version: str | None
    retrieved_at: str
    points: tuple[WeatherIndexPoint, ...]
    raw: Mapping[str, Any]
    can_execute: bool = False

    @property
    def valued_points(self) -> tuple[WeatherIndexPoint, ...]:
        return tuple(point for point in self.points if point.value_f is not None)

    @property
    def incomplete_points(self) -> tuple[WeatherIndexPoint, ...]:
        return tuple(point for point in self.points if point.status == "incomplete")


@dataclass(frozen=True)
class WeatherIndexCalibrationSnapshot:
    city: str
    retrieved_at: str
    calibrations: tuple[Mapping[str, Any], ...]
    raw: Mapping[str, Any]
    can_execute: bool = False


class KalshiWeatherIndexAdapter:
    """Read-only adapter for Kalshi's canonical hourly-temperature index.

    This adapter deliberately does not interpolate missing minutes, fill
    `incomplete` points, substitute NWS/METAR observations for the Kalshi index,
    or infer settlement from a nearby station. It only validates and freezes
    the public index evidence returned by Kalshi.
    """

    def __init__(self, get_json):
        self.get_json = get_json

    def snapshot(
        self,
        city: str,
        *,
        retrieved_at: str,
        detailed: bool = False,
    ) -> WeatherIndexSnapshot:
        city_slug = _clean_city(city)
        suffix = "?detailed=true" if detailed else ""
        url = f"{KALSHI_BASE_URL}/live_data/weather/{quote(city_slug, safe='-')}{suffix}"
        payload = self.get_json(url, None)
        return parse_weather_index_payload(
            payload,
            expected_city=city_slug,
            retrieved_at=retrieved_at,
        )

    def calibrations(
        self,
        city: str,
        *,
        retrieved_at: str,
    ) -> WeatherIndexCalibrationSnapshot:
        """Freeze Kalshi's published calibration timeline without reinterpreting it.

        The calibration endpoint is append-only evidence used to reproduce and
        audit the canonical index. Inner methodology fields are preserved raw
        until a separate calibration-contract parser validates their exact
        schema and effective-time semantics.
        """
        city_slug = _clean_city(city)
        url = f"{KALSHI_BASE_URL}/live_data/weather/{quote(city_slug, safe='-')}/calibrations"
        payload = self.get_json(url, None)
        if not isinstance(payload, Mapping):
            raise WeatherIndexError("WEATHER_INDEX_CALIBRATION_INVALID", ("PAYLOAD_NOT_OBJECT",))

        response_city = str(payload.get("city") or "").strip().lower()
        if response_city and response_city != city_slug:
            raise WeatherIndexError(
                "WEATHER_INDEX_IDENTITY_MISMATCH",
                (f"CALIBRATION_CITY_MISMATCH:{response_city}",),
            )

        raw_rows = payload.get("calibrations")
        if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
            raise WeatherIndexError(
                "WEATHER_INDEX_CALIBRATION_INVALID",
                ("CALIBRATIONS_ARRAY_MISSING",),
            )
        rows: list[Mapping[str, Any]] = []
        for row in raw_rows:
            if not isinstance(row, Mapping):
                raise WeatherIndexError(
                    "WEATHER_INDEX_CALIBRATION_INVALID",
                    ("CALIBRATION_ROW_NOT_OBJECT",),
                )
            rows.append(dict(row))

        return WeatherIndexCalibrationSnapshot(
            city=response_city or city_slug,
            retrieved_at=retrieved_at,
            calibrations=tuple(rows),
            raw=dict(payload),
            can_execute=False,
        )


def parse_weather_index_payload(
    payload: Mapping[str, Any],
    *,
    expected_city: str,
    retrieved_at: str,
) -> WeatherIndexSnapshot:
    if not isinstance(payload, Mapping):
        raise WeatherIndexError("WEATHER_INDEX_PAYLOAD_INVALID", ("PAYLOAD_NOT_OBJECT",))

    expected = _clean_city(expected_city)
    city = str(payload.get("city") or "").strip().lower()
    if not city:
        raise WeatherIndexError("WEATHER_INDEX_PAYLOAD_INVALID", ("CITY_MISSING",))
    if city != expected:
        raise WeatherIndexError(
            "WEATHER_INDEX_IDENTITY_MISMATCH",
            (f"CITY_MISMATCH:{city}",),
        )

    units = str(payload.get("units") or "").strip().lower()
    if units != "fahrenheit":
        raise WeatherIndexError(
            "WEATHER_INDEX_UNITS_UNSUPPORTED",
            (f"EXPECTED_FAHRENHEIT:{units or 'missing'}",),
        )

    config_version_raw = payload.get("config_version")
    config_version = str(config_version_raw).strip() if config_version_raw not in (None, "") else None

    raw_points = payload.get("timeseries")
    if not isinstance(raw_points, Sequence) or isinstance(raw_points, (str, bytes)):
        raise WeatherIndexError("WEATHER_INDEX_PAYLOAD_INVALID", ("TIMESERIES_ARRAY_MISSING",))

    points: list[WeatherIndexPoint] = []
    last_timestamp: int | None = None
    for raw_point in raw_points:
        if not isinstance(raw_point, Mapping):
            raise WeatherIndexError("WEATHER_INDEX_POINT_INVALID", ("POINT_NOT_OBJECT",))

        timestamp = _int_required(raw_point.get("t"), "TIMESTAMP_INVALID")
        if last_timestamp is not None and timestamp <= last_timestamp:
            raise WeatherIndexError(
                "WEATHER_INDEX_TEMPORAL_INVALID",
                ("TIMESTAMPS_NOT_STRICTLY_ASCENDING",),
            )
        last_timestamp = timestamp

        status = str(raw_point.get("status") or "").strip().lower()
        if status not in ALLOWED_INDEX_STATUSES:
            raise WeatherIndexError(
                "WEATHER_INDEX_STATUS_UNSUPPORTED",
                (f"STATUS:{status or 'missing'}",),
            )

        value = _decimal_optional(raw_point.get("v"), "INDEX_VALUE_INVALID")
        if status in {"normal", "degraded"} and value is None:
            raise WeatherIndexError(
                "WEATHER_INDEX_POINT_INVALID",
                (f"VALUED_STATUS_WITHOUT_VALUE:{status}",),
            )
        if status == "incomplete" and value is not None:
            raise WeatherIndexError(
                "WEATHER_INDEX_POINT_INVALID",
                ("INCOMPLETE_POINT_HAS_CANONICAL_VALUE",),
            )

        contributors = _int_optional(raw_point.get("contributors"), "CONTRIBUTORS_INVALID")
        if contributors is not None and contributors < 0:
            raise WeatherIndexError("WEATHER_INDEX_POINT_INVALID", ("CONTRIBUTORS_NEGATIVE",))

        station_readings = _parse_station_readings(raw_point.get("stations"))
        points.append(
            WeatherIndexPoint(
                timestamp_ms=timestamp,
                value_f=value,
                status=status,
                contributors=contributors,
                station_readings=station_readings,
                receipt_basis=raw_point.get("receipt_basis"),
            )
        )

    return WeatherIndexSnapshot(
        city=city,
        units="fahrenheit",
        config_version=config_version,
        retrieved_at=retrieved_at,
        points=tuple(points),
        raw=dict(payload),
        can_execute=False,
    )


def _parse_station_readings(raw: Any) -> tuple[WeatherIndexStationReading, ...]:
    if raw in (None, []):
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise WeatherIndexError("WEATHER_INDEX_POINT_INVALID", ("STATIONS_NOT_ARRAY",))

    out: list[WeatherIndexStationReading] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise WeatherIndexError("WEATHER_INDEX_POINT_INVALID", ("STATION_READING_NOT_OBJECT",))
        station_id = str(item.get("station_id") or "").strip()
        code = str(item.get("code") or "").strip()
        if not station_id or not code:
            raise WeatherIndexError(
                "WEATHER_INDEX_POINT_INVALID",
                ("STATION_READING_IDENTITY_MISSING",),
            )
        source_raw = item.get("source")
        source = str(source_raw).strip() if source_raw not in (None, "") else None
        temp_f = _decimal_optional(item.get("temp_f"), "STATION_TEMPERATURE_INVALID")
        out.append(
            WeatherIndexStationReading(
                station_id=station_id,
                code=code,
                source=source,
                temp_f=temp_f,
                raw=dict(item),
            )
        )
    return tuple(out)


def _clean_city(city: str) -> str:
    text = str(city or "").strip().lower()
    if not text or any(ch.isspace() for ch in text):
        raise WeatherIndexError("WEATHER_INDEX_CITY_INVALID", (str(city),))
    if not all(ch.isalnum() or ch == "-" for ch in text):
        raise WeatherIndexError("WEATHER_INDEX_CITY_INVALID", (str(city),))
    return text


def _int_required(value: Any, blocker: str) -> int:
    parsed = _int_optional(value, blocker)
    if parsed is None:
        raise WeatherIndexError("WEATHER_INDEX_POINT_INVALID", (blocker,))
    return parsed


def _int_optional(value: Any, blocker: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise WeatherIndexError("WEATHER_INDEX_POINT_INVALID", (blocker,)) from exc
    return parsed


def _decimal_optional(value: Any, blocker: str) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise WeatherIndexError("WEATHER_INDEX_POINT_INVALID", (blocker,)) from exc
    if not parsed.is_finite():
        raise WeatherIndexError("WEATHER_INDEX_POINT_INVALID", (blocker,))
    return parsed
