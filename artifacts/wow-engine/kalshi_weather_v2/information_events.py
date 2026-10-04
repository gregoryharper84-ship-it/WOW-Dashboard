from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Iterable


class InformationEventError(ValueError):
    pass


class InformationEventType(str, Enum):
    ASOS_METAR_OBSERVATION = "ASOS_METAR_OBSERVATION"
    NWS_FORECAST_UPDATE = "NWS_FORECAST_UPDATE"
    HRRR_MODEL_CYCLE = "HRRR_MODEL_CYCLE"
    NBM_FORECAST_UPDATE = "NBM_FORECAST_UPDATE"
    SETTLEMENT_AUTHORITY_UPDATE = "SETTLEMENT_AUTHORITY_UPDATE"
    OTHER_AUTHORITATIVE_WEATHER = "OTHER_AUTHORITATIVE_WEATHER"


@dataclass(frozen=True)
class InformationEvent:
    event_id: str
    version: str
    event_type: InformationEventType
    lane: str
    source: str
    evidence_id: str
    expected_at: str
    available_at: str | None = None
    lead_time_bucket: str | None = None
    regime: str | None = None
    threshold_distance: str | None = None
    historical_latency_seconds: float | None = None
    reliability: float | None = None
    can_execute: bool = False

    def __post_init__(self) -> None:
        for name in ("event_id", "version", "lane", "source", "evidence_id", "expected_at"):
            if not str(getattr(self, name) or "").strip():
                raise InformationEventError(f"INFORMATION_EVENT_{name.upper()}_MISSING")
        expected = parse_event_time(self.expected_at)
        if self.available_at is not None:
            parse_event_time(self.available_at)
        if self.historical_latency_seconds is not None:
            value = float(self.historical_latency_seconds)
            if not math.isfinite(value) or value < 0.0:
                raise InformationEventError("INFORMATION_EVENT_LATENCY_INVALID")
        if self.reliability is not None:
            value = float(self.reliability)
            if not math.isfinite(value) or not (0.0 <= value <= 1.0):
                raise InformationEventError("INFORMATION_EVENT_RELIABILITY_INVALID")
        if self.can_execute:
            raise InformationEventError("INFORMATION_EVENT_EXECUTION_PROHIBITED")
        object.__setattr__(self, "expected_at", format_event_time(expected))
        if self.available_at is not None:
            object.__setattr__(self, "available_at", format_event_time(parse_event_time(self.available_at)))

    def is_available_as_of(self, as_of: str) -> bool:
        if self.available_at is None:
            return False
        return parse_event_time(self.available_at) <= parse_event_time(as_of)

    def is_future_as_of(self, as_of: str) -> bool:
        cutoff = parse_event_time(as_of)
        if self.is_available_as_of(as_of):
            return False
        if self.available_at is not None:
            return parse_event_time(self.available_at) > cutoff
        return parse_event_time(self.expected_at) > cutoff


class InformationEventRegistry:
    """Versioned, point-in-time-safe registry for weather information events."""

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], InformationEvent] = {}

    def register(self, event: InformationEvent) -> InformationEvent:
        key = (event.event_id, event.version)
        existing = self._events.get(key)
        if existing is not None and existing != event:
            raise InformationEventError("INFORMATION_EVENT_VERSION_COLLISION")
        self._events[key] = event
        return event

    def get(self, event_id: str, version: str) -> InformationEvent:
        try:
            return self._events[(event_id, version)]
        except KeyError as exc:
            raise InformationEventError("INFORMATION_EVENT_NOT_FOUND") from exc

    def available_as_of(self, as_of: str, *, lane: str | None = None) -> tuple[InformationEvent, ...]:
        items = [
            event for event in self._events.values()
            if event.is_available_as_of(as_of) and (lane is None or event.lane == lane)
        ]
        return tuple(sorted(items, key=event_sort_key))

    def future_as_of(self, as_of: str, *, lane: str | None = None) -> tuple[InformationEvent, ...]:
        items = [
            event for event in self._events.values()
            if event.is_future_as_of(as_of) and (lane is None or event.lane == lane)
        ]
        return tuple(sorted(items, key=event_sort_key))

    def register_many(self, events: Iterable[InformationEvent]) -> tuple[InformationEvent, ...]:
        return tuple(self.register(event) for event in events)


def event_sort_key(event: InformationEvent) -> tuple[datetime, str, str]:
    return (parse_event_time(event.expected_at), event.event_id, event.version)


def parse_event_time(value: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise InformationEventError("INFORMATION_EVENT_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None:
        raise InformationEventError("INFORMATION_EVENT_TIMESTAMP_TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def format_event_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
