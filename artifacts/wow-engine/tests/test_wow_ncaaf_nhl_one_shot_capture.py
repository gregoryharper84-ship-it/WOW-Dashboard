"""Offline tests for the isolated NCAAF/NHL raw capture; never contact ESPN."""
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

P = Path(__file__).resolve().parents[1] / "scripts/wow_ncaaf_nhl_one_shot_capture.py"
spec = importlib.util.spec_from_file_location("two_sport_capture", P)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
NOW = datetime(2026, 10, 22, 19, 30, tzinfo=timezone.utc)
DATE = "2026-10-24"


def event(state="pre", start="2026-10-24T19:00:00Z"):
    return {"id": "401234500", "date": start, "status": {"type": {"state": state}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"id": "33", "displayName": "Home"}},
                {"homeAway": "away", "team": {"id": "44", "displayName": "Away"}},
            ]}]}


def payload(events=None):
    return json.dumps({"events": [event()] if events is None else events}).encode()


class Response:
    def __init__(self, raw=None, when=None, url=None, headers=None, status=200, sport="NCAAF"):
        self.raw = payload() if raw is None else raw
        self.url = url or mod.source_url(sport, DATE)
        self.status = status
        self.headers = headers if headers is not None else {
            "Date": format_datetime(when or NOW, usegmt=True),
            "Content-Type": "application/json;charset=UTF-8"}
    def geturl(self): return self.url
    def getcode(self): return self.status
    def read(self, n): return self.raw[:n]
    def __enter__(self): return self
    def __exit__(self, *_): return False


class Opener:
    def __init__(self, response): self.response = response
    def open(self, req, timeout): return self.response


@pytest.mark.parametrize("sport", ["NCAAF", "NHL"])
def test_capture_replay_and_source_scope(sport, tmp_path):
    location = tmp_path / sport
    result = mod.capture(sport, DATE, location, now=NOW, opener=Opener(Response(sport=sport)))
    assert result["events_total"] == 1
    assert result["events_pregame_eligible"][0]["home"]["team_id"] == "33"
    assert result["can_execute"] is False
    assert result["full_slate_completeness_attested"] is False
    receipt = mod.verify(sport, DATE, location)
    assert receipt["status"] == "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS"
    assert receipt["raw_sha256"] == result["raw_sha256"]


def test_ncaaf_requires_explicit_group_and_limit():
    assert mod.source_url("NCAAF", DATE).endswith("?dates=20261024&groups=80&limit=500")


def test_nhl_scope():
    assert mod.source_url("NHL", DATE).endswith("/nhl/scoreboard?dates=20261024")


def test_no_unknown_sports_or_noncanonical_dates():
    with pytest.raises(mod.CaptureFailure, match="BAD_SPORT"):
        mod.source_url("NFL", DATE)
    with pytest.raises(mod.CaptureFailure, match="BAD_TARGET_DATE"):
        mod.source_url("NHL", "2026-1-1")


def test_http_date_missing():
    with pytest.raises(mod.CaptureFailure, match="SOURCE_HTTP_DATE_MISSING"):
        mod.fetch_source("NCAAF", DATE, now=NOW, opener=Opener(Response(headers={"Content-Type": "application/json"})))


def test_http_date_outside_tolerance():
    with pytest.raises(mod.CaptureFailure, match="SOURCE_HTTP_DATE_STALE_OR_SKEWED"):
        mod.fetch_source("NHL", DATE, now=NOW, opener=Opener(Response(when=NOW - timedelta(minutes=11), sport="NHL")))


def test_redirect_refusal():
    with pytest.raises(mod.CaptureFailure, match="SOURCE_REDIRECT_REFUSED"):
        mod.fetch_source("NHL", DATE, now=NOW, opener=Opener(Response(sport="NHL", url="https://fake.example")))
    with pytest.raises(mod.CaptureFailure, match="SOURCE_REDIRECT_REFUSED"):
        mod.RefuseRedirect().redirect_request(Mock(), None, 302, "moved", {}, "https://fake.example")


def test_invalid_content_type():
    with pytest.raises(mod.CaptureFailure, match="SOURCE_CONTENT_TYPE_INVALID"):
        mod.fetch_source("NCAAF", DATE, now=NOW, opener=Opener(Response(headers={
            "Content-Type": "text/html", "Date": format_datetime(NOW, usegmt=True)})))


def test_non_pregame_never_eligible():
    result = mod.classify_events("NHL", payload([event(state="in")]), now=NOW)
    assert not result["events_pregame_eligible"]
    assert result["events_skipped"][0]["reason"] == "EVENT_NOT_FUTURE_PREGAME"


def test_minimum_lead_boundary():
    e = event(start="2026-10-22T19:39:59Z")
    result = mod.classify_events("NCAAF", payload([e]), now=NOW)
    assert not result["events_pregame_eligible"]
    with pytest.raises(mod.CaptureFailure, match="BAD_MIN_LEAD"):
        mod.classify_events("NHL", payload(), now=NOW, min_lead=599)


def test_duplicate_identity_rejected():
    e = event()
    with pytest.raises(mod.CaptureFailure, match="SOURCE_EVENT_IDENTITY_DUPLICATE"):
        mod.classify_events("NHL", payload([e, e]), now=NOW)


def test_malformed_team_identity_rejected():
    e = event()
    e["competitions"][0]["competitors"][1]["team"]["id"] = "33"
    with pytest.raises(mod.CaptureFailure, match="SOURCE_TEAM_IDENTITY_MALFORMED"):
        mod.classify_events("NCAAF", payload([e]), now=NOW)


def test_malformed_payload_rejected():
    with pytest.raises(mod.CaptureFailure, match="SOURCE_EVENTS_MALFORMED"):
        mod.classify_events("NHL", b"{}", now=NOW)
    with pytest.raises(mod.CaptureFailure, match="SOURCE_JSON_INVALID"):
        mod.classify_events("NHL", b"{bad-json}", now=NOW)


def test_tamper_detection(tmp_path):
    location = tmp_path / "evidence"
    mod.capture("NHL", DATE, location, now=NOW, opener=Opener(Response(sport="NHL")))
    (location / "nhl_2026-10-24_espn_raw.json").write_bytes(b"{}")
    with pytest.raises(mod.CaptureFailure, match="CAPTURE_RAW_HASH_MISMATCH"):
        mod.verify("NHL", DATE, location)


def test_duplicate_capture_refused(tmp_path):
    location = tmp_path / "same"
    mod.capture("NCAAF", DATE, location, now=NOW, opener=Opener(Response()))
    with pytest.raises(mod.CaptureFailure, match="CAPTURE_OUTPUT_ALREADY_EXISTS"):
        mod.capture("NCAAF", DATE, location, now=NOW, opener=Opener(Response()))


def test_receipt_event_tampering_detected(tmp_path):
    location = tmp_path / "receipt"
    mod.capture("NHL", DATE, location, now=NOW, opener=Opener(Response(sport="NHL")))
    receipt_path = location / "nhl_2026-10-24_espn_capture_receipt.json"
    r = json.loads(receipt_path.read_text())
    r["events_pregame_eligible"] = []
    receipt_path.write_text(json.dumps(r))
    with pytest.raises(mod.CaptureFailure, match="CAPTURE_RECEIPT_EVENT_MISMATCH"):
        mod.verify("NHL", DATE, location)


def test_event_rows_cap():
    with pytest.raises(mod.CaptureFailure, match="SOURCE_EVENT_ROWS_EXCEED_CAP"):
        mod.classify_events("NCAAF", payload([event()] * 501), now=NOW)