"""Offline NOAA NBM inventory checks. Index metadata is never model output."""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "kalshi_weather_v2" / "noaa_nbm_inventory.py"
spec = importlib.util.spec_from_file_location("nbm_inventory_testing", PATH)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
CYCLE = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
AT = CYCLE + timedelta(hours=1)


def index(*, init="2026102712"):
    return (f"1:0:d={init}:TMP:2 m above ground:3 hour fcst:\n"
            f"2:307200:d={init}:RH:2 m above ground:3 hour fcst:\n").encode()


def test_noaa_core_filename_inventory_and_provenance():
    path = m.url_for_core(cycle_utc=CYCLE, lead_hours=3)
    assert path == "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/blend.20261027/12/core/blend.t12z.core.f003.co.grib2"
    r = m.inspect_index(index_text=index(), cycle_utc=CYCLE, lead_hours=3, retrieved_at=AT).as_dict()
    assert r["index_url"] == path + ".idx"
    assert r["two_meter_temperature_records"] == 1
    assert r["record_count"] == 2
    assert r["valid_utc"] == "2026-10-27T15:00:00+00:00"
    assert r["status"] == "NBM_INDEX_METADATA_ONLY_NO_FORECAST_VALUES"
    assert r["point_in_time_forecast_values_verified"] is False
    assert r["probability_publishable"] is False
    assert r["can_execute"] is False


@pytest.mark.parametrize("bad,reason", [
    (dict(cycle_utc=CYCLE.replace(tzinfo=None), lead_hours=3), "NBM_CYCLE_REQUIRES_TIMEZONE"),
    (dict(cycle_utc=CYCLE.replace(minute=15), lead_hours=3), "NBM_CYCLE_NOT_HOURLY"),
    (dict(cycle_utc=CYCLE, lead_hours=0), "NBM_INVALID_FORECAST_HOUR"),
    (dict(cycle_utc=CYCLE, lead_hours=265), "NBM_INVALID_FORECAST_HOUR"),
    (dict(cycle_utc=CYCLE, lead_hours=True), "NBM_INVALID_FORECAST_HOUR"),
    (dict(cycle_utc=CYCLE, lead_hours=3, region="ak"), "NBM_ONLY_CONUS_CORE_SUPPORTED"),
])
def test_no_uncontrolled_file_requests(bad, reason):
    with pytest.raises(m.NbmEvidenceError, match=reason):
        m.url_for_core(**bad)


@pytest.mark.parametrize("raw,reason", [
    (b"", "NBM_INDEX_EMPTY_OR_OVERSIZED"),
    (b"bad content\n", "NBM_INDEX_MALFORMED_RECORD"),
    (index(init="2026102612"), "NBM_INDEX_CYCLE_MISMATCH"),
    (b"1:0:d=2026102712:TMP:2 m above ground:\n2:0:d=2026102712:RH:2 m above ground:\n", "NBM_INDEX_DUPLICATE_BYTE_OFFSET"),
])
def test_bad_index_fails_closed(raw, reason):
    with pytest.raises(m.NbmEvidenceError, match=reason):
        m.inspect_index(index_text=raw, cycle_utc=CYCLE, lead_hours=3, retrieved_at=AT)


def test_future_nbm_cycle_cannot_be_retroactively_claimed():
    with pytest.raises(m.NbmEvidenceError, match="NBM_FUTURE_CYCLE_AS_OF_RETRIEVAL"):
        m.inspect_index(index_text=index(), cycle_utc=CYCLE, lead_hours=3, retrieved_at=CYCLE-timedelta(minutes=1))


def test_no_temperature_element_is_not_a_temperature_forecast():
    r = m.inspect_index(index_text=b"1:0:d=2026102712:RH:2 m above ground:3 hour fcst:\n", cycle_utc=CYCLE, lead_hours=3, retrieved_at=AT)
    assert r.two_meter_temperature_records == 0
    assert r.status == "NBM_TEMP_ELEMENT_ABSENT"
    assert r.probability_publishable is False


def test_bounded_input():
    with pytest.raises(m.NbmEvidenceError, match="NBM_INDEX_EMPTY_OR_OVERSIZED"):
        m.inspect_index(index_text=b"x" * (m.MAX_INDEX_BYTES + 1), cycle_utc=CYCLE, lead_hours=3, retrieved_at=AT)


class Response:
    def __init__(self, url, data, status=200):
        self.url, self.data, self.status = url, data, status
    def geturl(self): return self.url
    def read(self, n): return self.data[:n]
    def __enter__(self): return self
    def __exit__(self, *_): return False


class Opener:
    def __init__(self, response): self.response = response; self.requests = []
    def open(self, request, timeout):
        self.requests.append((request.full_url, timeout))
        return self.response


def test_bounded_index_http_fetch_and_exact_nbm_url():
    url = m.url_for_core(cycle_utc=CYCLE, lead_hours=3) + ".idx"
    transport = Opener(Response(url, index()))
    report = m.fetch_index(cycle_utc=CYCLE, lead_hours=3, retrieved_at=AT, opener=transport)
    assert report.record_count == 2
    assert transport.requests == [(url, 12)]
    assert report.probability_publishable is False


def test_index_http_wrong_url_or_status_fails_closed():
    url = m.url_for_core(cycle_utc=CYCLE, lead_hours=3) + ".idx"
    for response, expected in [
        (Response("https://other.example/unverified.idx", index()), "NBM_INDEX_RESPONSE_URL_MISMATCH"),
        (Response(url, index(), status=204), "NBM_INDEX_HTTP_204"),
    ]:
        with pytest.raises(m.NbmEvidenceError, match=expected):
            m.fetch_index(cycle_utc=CYCLE, lead_hours=3, retrieved_at=AT, opener=Opener(response))
