"""Bounded NOAA National Blend of Models *inventory* evidence only.

Builds deterministic NOAA NBM v5 core CONUS file paths and validates a GRIB2
inventory sidecar's metadata. A .idx does NOT contain gridded forecast values.
This module does not interpolate, decode GRIB2, provide probabilities, or turn
NBM's initial cycle time into an observed publication/retrieval timestamp.
"""
from __future__ import annotations

import hashlib
import re
from urllib import request, error
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

NBM_BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod"
MAX_INDEX_BYTES = 2_000_000
MAX_INDEX_LINES = 40_000
RECORD = re.compile(r"^(\d+):(\d+):d=(\d{10}):(.+)$")


class NbmEvidenceError(ValueError):
    """Typed NBM manifest error; fail closed without forecast inference."""


def url_for_core(*, cycle_utc: datetime, lead_hours: int, region: str = "co") -> str:
    if not isinstance(cycle_utc, datetime) or cycle_utc.tzinfo is None:
        raise NbmEvidenceError("NBM_CYCLE_REQUIRES_TIMEZONE")
    cycle = cycle_utc.astimezone(timezone.utc)
    if cycle.minute or cycle.second or cycle.microsecond:
        raise NbmEvidenceError("NBM_CYCLE_NOT_HOURLY")
    if type(lead_hours) is not int or not 1 <= lead_hours <= 264:
        raise NbmEvidenceError("NBM_INVALID_FORECAST_HOUR")
    if region != "co":
        raise NbmEvidenceError("NBM_ONLY_CONUS_CORE_SUPPORTED")
    day = cycle.strftime("%Y%m%d")
    hour = cycle.strftime("%H")
    return f"{NBM_BASE}/blend.{day}/{hour}/core/blend.t{hour}z.core.f{lead_hours:03d}.co.grib2"


@dataclass(frozen=True)
class NbmIndexSnapshot:
    schema: str
    provider: str
    product: str
    init_utc: str
    valid_utc: str
    retrieved_at_utc: str
    index_url: str
    raw_sha256: str
    record_count: int
    two_meter_temperature_records: int
    status: str
    point_in_time_forecast_values_verified: bool = False
    settlement_authority_verified: bool = False
    probability_publishable: bool = False
    can_execute: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def inspect_index(*, index_text: bytes, cycle_utc: datetime, lead_hours: int, retrieved_at: datetime) -> NbmIndexSnapshot:
    base = url_for_core(cycle_utc=cycle_utc, lead_hours=lead_hours)
    if not isinstance(retrieved_at, datetime) or retrieved_at.tzinfo is None:
        raise NbmEvidenceError("NBM_RETRIEVAL_TIME_UNVERIFIED")
    retrieval = retrieved_at.astimezone(timezone.utc)
    cycle = cycle_utc.astimezone(timezone.utc)
    if retrieval < cycle:
        raise NbmEvidenceError("NBM_FUTURE_CYCLE_AS_OF_RETRIEVAL")
    if not isinstance(index_text, bytes) or not index_text or len(index_text) > MAX_INDEX_BYTES:
        raise NbmEvidenceError("NBM_INDEX_EMPTY_OR_OVERSIZED")
    try:
        lines = index_text.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise NbmEvidenceError("NBM_INDEX_NOT_ASCII") from exc
    if not 0 < len(lines) <= MAX_INDEX_LINES:
        raise NbmEvidenceError("NBM_INDEX_LINE_COUNT_INVALID")
    seen = set()
    hits = 0
    for line in lines:
        match = RECORD.fullmatch(line.strip())
        if match is None:
            raise NbmEvidenceError("NBM_INDEX_MALFORMED_RECORD")
        _, byte_offset, cycle_tag, label = match.groups()
        if cycle_tag != cycle.strftime("%Y%m%d%H"):
            raise NbmEvidenceError("NBM_INDEX_CYCLE_MISMATCH")
        offset = int(byte_offset)
        if offset in seen:
            raise NbmEvidenceError("NBM_INDEX_DUPLICATE_BYTE_OFFSET")
        seen.add(offset)
        if re.search(r"(?:^|:)TMP:2 m above ground:", ":" + label):
            hits += 1
    return NbmIndexSnapshot(
        schema="WOW_KALSHI_NOAA_NBM_INDEX_V1",
        provider="NOAA_NBM_NOMADS",
        product="CONUS_CORE_GRIB2_INDEX_METADATA_ONLY",
        init_utc=cycle.isoformat(),
        valid_utc=(cycle + timedelta(hours=lead_hours)).isoformat(),
        retrieved_at_utc=retrieval.isoformat(),
        index_url=base + ".idx",
        raw_sha256=hashlib.sha256(index_text).hexdigest(),
        record_count=len(lines),
        two_meter_temperature_records=hits,
        status="NBM_INDEX_METADATA_ONLY_NO_FORECAST_VALUES" if hits else "NBM_TEMP_ELEMENT_ABSENT",
    )


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise NbmEvidenceError("NBM_REDIRECT_REFUSED")


def fetch_index(*, cycle_utc: datetime, lead_hours: int, retrieved_at: datetime, opener=None) -> NbmIndexSnapshot:
    """Acquire only a bounded *.idx text sidecar, not the large GRIB2 data.

    NOAA NOMADS model files rotate quickly. A successful HTTP 200 verifies
    only that a matching index was retrieved at `retrieved_at`, not that the
    model fields were issued before an earlier historical decision.
    """
    url = url_for_core(cycle_utc=cycle_utc, lead_hours=lead_hours) + ".idx"
    opener = opener or request.build_opener(_NoRedirect())
    req = request.Request(url, headers={
        "Accept": "text/plain,application/octet-stream",
        "User-Agent": "WOW-KalshiWeather-readonly-research/1.0",
    })
    try:
        with opener.open(req, timeout=12) as response:
            if response.geturl() != url:
                raise NbmEvidenceError("NBM_INDEX_RESPONSE_URL_MISMATCH")
            if response.status != 200:
                raise NbmEvidenceError(f"NBM_INDEX_HTTP_{response.status}")
            raw = response.read(MAX_INDEX_BYTES + 1)
    except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
        raise NbmEvidenceError("NBM_INDEX_HTTP_UNAVAILABLE") from exc
    return inspect_index(index_text=raw, cycle_utc=cycle_utc,
                         lead_hours=lead_hours, retrieved_at=retrieved_at)
