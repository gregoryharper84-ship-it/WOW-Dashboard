"""Bounded, read-only NOAA Aviation Weather Center METAR acquisition.

A METAR is corroborating observation evidence, NOT proof of a Kalshi contract's
settlement. The exact contract settlement authority/station must be verified
independently. No prices, probabilities, orders, model weights, or database writes.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib import request, error

API_HOST = "aviationweather.gov"
API_URL = "https://aviationweather.gov/api/data/metar"
MAX_BODY_BYTES = 256_000
MAX_RESPONSE_ROWS = 400
MAX_OBSERVATION_AGE_MINUTES = 180
STATION = re.compile(r"[A-Z][A-Z0-9]{3}\Z")


class MetarEvidenceError(ValueError):
    """Typed, no-fallback acquisition or provenance hold."""


def utc_time(v: object, label: str) -> datetime:
    if isinstance(v, str):
        try:
            value = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MetarEvidenceError(f"{label}: INVALID_TIMESTAMP") from exc
        if value.tzinfo is None:
            raise MetarEvidenceError(f"{label}: TIMEZONE_MISSING")
    elif type(v) in (int, float) and math.isfinite(v):
        try:
            value = datetime.fromtimestamp(v, tz=timezone.utc)
        except (ValueError, OverflowError, OSError) as exc:
            raise MetarEvidenceError(f"{label}: INVALID_TIMESTAMP") from exc
    else:
        raise MetarEvidenceError(f"{label}: INVALID_TIMESTAMP")
    return value.astimezone(timezone.utc)


def _station(s: str) -> str:
    if not isinstance(s, str) or STATION.fullmatch(s) is None:
        raise MetarEvidenceError("INVALID_EXPLICIT_ICAO_STATION")
    return s


def _float(v: object, name: str) -> float:
    if type(v) not in (float, int) or not math.isfinite(v):
        raise MetarEvidenceError(f"{name}: MISSING_OR_NONFINITE")
    return float(v)


@dataclass(frozen=True)
class MetarSnapshot:
    schema: str
    provider: str
    observation_role: str
    station_icao: str
    observed_at_utc: str
    retrieved_at_utc: str
    temperature_c: float
    temperature_f: float
    age_minutes: float
    raw_sha256: str
    observations_seen: int
    source_url: str
    status: str
    settlement_authority_verified: bool = False
    probability_publishable: bool = False
    can_execute: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def parse_metar(raw: bytes, *, station_icao: str, as_of: datetime) -> MetarSnapshot:
    station_icao = _station(station_icao)
    as_of = utc_time(as_of.isoformat() if isinstance(as_of, datetime) else as_of, "as_of")
    if len(raw) > MAX_BODY_BYTES:
        raise MetarEvidenceError("METAR_RESPONSE_OVERSIZED")
    try:
        rows = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise MetarEvidenceError("METAR_INVALID_JSON") from exc
    if not isinstance(rows, list) or not 0 < len(rows) <= MAX_RESPONSE_ROWS:
        raise MetarEvidenceError("METAR_NO_BOUNDED_OBSERVATIONS")
    validated: list[tuple[datetime, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise MetarEvidenceError("METAR_INVALID_ROW")
        if row.get("icaoId") != station_icao:
            raise MetarEvidenceError("METAR_STATION_MISMATCH")
        stamp = row.get("obsTime", row.get("reportTime"))
        observed = utc_time(stamp, "obsTime")
        if observed > as_of:
            raise MetarEvidenceError("METAR_FUTURE_OBSERVATION")
        tc = _float(row.get("temp"), "temp")
        if tc < -100 or tc > 70:
            raise MetarEvidenceError("METAR_TEMPERATURE_OUT_OF_RANGE")
        validated.append((observed, tc))
    latest = max(v[0] for v in validated)
    temps = {t for stamp, t in validated if stamp == latest}
    if len(temps) != 1:
        raise MetarEvidenceError("METAR_SAME_TIME_CONFLICT")
    tc = next(iter(temps))
    age = (as_of - latest).total_seconds() / 60
    if age > MAX_OBSERVATION_AGE_MINUTES:
        raise MetarEvidenceError("METAR_STALE_OBSERVATION")
    return MetarSnapshot(
        schema="WOW_KALSHI_AWC_METAR_EVIDENCE_V1",
        provider="NOAA_AWC_METAR",
        observation_role="CORROBORATION_NOT_SETTLEMENT_AUTHORITY",
        station_icao=station_icao,
        observed_at_utc=latest.isoformat(),
        retrieved_at_utc=as_of.isoformat(),
        temperature_c=tc,
        temperature_f=round(tc * 9 / 5 + 32, 3),
        age_minutes=round(age, 2),
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        observations_seen=len(validated),
        source_url=API_URL,
        status="FRESH_STATION_OBSERVATION_CORROBORATION",
    )


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MetarEvidenceError("AWC_REDIRECT_REFUSED")


def fetch_metar(*, station_icao: str, as_of: datetime, opener=None) -> MetarSnapshot:
    """One HTTPS call per invocation. Callers must cache/rate-limit per station.

    No retry loop, no dynamic host, no mutable state or credential transport.
    """
    station_icao = _station(station_icao)
    endpoint = f"{API_URL}?ids={station_icao}&format=json"
    opener = opener or request.build_opener(_NoRedirect())
    req = request.Request(endpoint, headers={
        "Accept": "application/json",
        "User-Agent": "WOW-KalshiWeather-readonly-research/1.0",
    })
    try:
        with opener.open(req, timeout=12) as response:
            if response.geturl() != endpoint:
                raise MetarEvidenceError("AWC_RESPONSE_URL_MISMATCH")
            if response.status == 204:
                raise MetarEvidenceError("AWC_NO_DATA_204")
            if response.status != 200:
                raise MetarEvidenceError(f"AWC_HTTP_{response.status}")
            ctype = response.headers.get("Content-Type", "")
            if "application/json" not in ctype.lower():
                raise MetarEvidenceError("AWC_UNEXPECTED_CONTENT_TYPE")
            raw = response.read(MAX_BODY_BYTES + 1)
    except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
        raise MetarEvidenceError("AWC_HTTP_UNAVAILABLE") from exc
    return parse_metar(raw, station_icao=station_icao, as_of=as_of)
