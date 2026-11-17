"""Offline NOAA AWC METAR adapter tests: no external API requests or writes."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "kalshi_weather_v2" / "noaa_awc_metar.py"
spec = importlib.util.spec_from_file_location("metar_adapter_testing", PATH)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
NOW = datetime(2026, 10, 27, 22, 0, tzinfo=timezone.utc)


def body(rows=None):
    return json.dumps(rows if rows is not None else [
        {"icaoId": "KDFW", "obsTime": 1793136600, "temp": 28.0, "rawOb": "KDFW ..."}
    ]).encode()


def test_good_observation_uses_exact_icao_and_never_asserts_settlement():
    result = m.parse_metar(body(), station_icao="KDFW", as_of=NOW).as_dict()
    assert result["station_icao"] == "KDFW"
    assert result["temperature_c"] == 28.0
    assert result["temperature_f"] == 82.4
    assert result["raw_sha256"]
    assert result["observation_role"] == "CORROBORATION_NOT_SETTLEMENT_AUTHORITY"
    assert result["settlement_authority_verified"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_latest_station_record_is_selected():
    r = m.parse_metar(body([
        {"icaoId": "KDFW", "obsTime": 1793133000, "temp": 23.5},
        {"icaoId": "KDFW", "obsTime": 1793136600, "temp": 26.5},
    ]), station_icao="KDFW", as_of=NOW)
    assert r.temperature_c == 26.5 and r.observations_seen == 2


@pytest.mark.parametrize("rows,reason", [
    ([{"icaoId": "KDAL", "obsTime": 1793136600, "temp": 28}], "METAR_STATION_MISMATCH"),
    ([{"icaoId": "KDFW", "obsTime": 1793145600, "temp": 28}], "METAR_FUTURE_OBSERVATION"),
    ([{"icaoId": "KDFW", "obsTime": 1793110000, "temp": 28}], "METAR_STALE_OBSERVATION"),
    ([{"icaoId": "KDFW", "obsTime": 1793136600}], "temp: MISSING_OR_NONFINITE"),
    ([{"icaoId": "KDFW", "obsTime": 1793136600, "temp": 200}], "METAR_TEMPERATURE_OUT_OF_RANGE"),
    ([{"icaoId": "KDFW", "obsTime": 1793136600, "temp": 28},
      {"icaoId": "KDFW", "obsTime": 1793136600, "temp": 30}], "METAR_SAME_TIME_CONFLICT"),
])
def test_bad_metar_blocks(rows, reason):
    with pytest.raises(m.MetarEvidenceError, match=reason):
        m.parse_metar(body(rows), station_icao="KDFW", as_of=NOW)


@pytest.mark.parametrize("station", ["KDFW?x=1", "kdfw", "KDF", "KDFW,KDAL", " KDFW", "KDFW/"])
def test_station_must_be_unambiguous_allowlisted_format(station):
    with pytest.raises(m.MetarEvidenceError, match="INVALID_EXPLICIT_ICAO_STATION"):
        m.parse_metar(body(), station_icao=station, as_of=NOW)


def test_invalid_response_size_and_empty_list():
    with pytest.raises(m.MetarEvidenceError, match="METAR_RESPONSE_OVERSIZED"):
        m.parse_metar(b"x" * (m.MAX_BODY_BYTES + 1), station_icao="KDFW", as_of=NOW)
    with pytest.raises(m.MetarEvidenceError, match="METAR_NO_BOUNDED_OBSERVATIONS"):
        m.parse_metar(b"[]", station_icao="KDFW", as_of=NOW)


class Response:
    def __init__(self, url, data, *, status=200, content_type="application/json"):
        self.url, self.data, self.status = url, data, status
        self.headers = {"Content-Type": content_type}

    def geturl(self): return self.url
    def read(self, n): return self.data[:n]
    def __enter__(self): return self
    def __exit__(self, *_): return False


class Opener:
    def __init__(self, response): self.response = response; self.calls = 0; self.urls = []
    def open(self, req, timeout):
        self.calls += 1
        self.urls.append(req.full_url)
        assert timeout == 12
        return self.response


def test_fetch_single_bounded_canonical_request():
    url = m.API_URL + "?ids=KDFW&format=json"
    opener = Opener(Response(url, body()))
    got = m.fetch_metar(station_icao="KDFW", as_of=NOW, opener=opener)
    assert got.station_icao == "KDFW"
    assert opener.urls == [url]
    assert opener.calls == 1


@pytest.mark.parametrize("status,ctype,badurl,reason", [
    (204, "application/json", False, "AWC_NO_DATA_204"),
    (200, "text/html", False, "AWC_UNEXPECTED_CONTENT_TYPE"),
    (200, "application/json", True, "AWC_RESPONSE_URL_MISMATCH"),
    (500, "application/json", False, "AWC_HTTP_500"),
])
def test_acquisition_fails_typed(status, ctype, badurl, reason):
    url = m.API_URL + "?ids=KDFW&format=json"
    opener = Opener(Response("https://evil.example" if badurl else url, body(), status=status, content_type=ctype))
    with pytest.raises(m.MetarEvidenceError, match=reason):
        m.fetch_metar(station_icao="KDFW", as_of=NOW, opener=opener)
    assert opener.calls == 1


def test_rejects_naive_as_of_and_temperature_boolean():
    with pytest.raises(m.MetarEvidenceError, match="TIMEZONE_MISSING"):
        m.parse_metar(body(), station_icao="KDFW", as_of=datetime(2026, 10, 27, 22, 0))
    with pytest.raises(m.MetarEvidenceError, match="MISSING_OR_NONFINITE"):
        m.parse_metar(body([{"icaoId": "KDFW", "obsTime": 1793136600, "temp": True}]), station_icao="KDFW", as_of=NOW)
